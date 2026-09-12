#!/usr/bin/env python3
"""routing-metrics.jsonlから日本語のCodexルーティングレポートを表示する。"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from rollout_reader import parse_ts


def pct(n: float, d: float) -> float:
    return 0.0 if not d else n * 100.0 / d


def p90(values: list[int]) -> int:
    if not values:
        return 0
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * 0.9) - 1)]


def distribution(values: list[int]) -> tuple[int, int, int, int]:
    """平均、中央値、P90、最大。欠損(0)だけの列は0を返す。"""
    values = [int(value or 0) for value in values]
    if not values:
        return 0, 0, 0, 0
    return int(statistics.mean(values)), int(statistics.median(values)), p90(values), max(values)


def fmt_i(n: float | int) -> str:
    return f"{int(n):,}"


def fmt_pct(x: float) -> str:
    return f"{x:.1f}%"


def display_width(value: str) -> int:
    """端末上の表示幅。日本語など全角文字を2桁として扱う。"""
    return sum(2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1 for char in value)


def pad_cell(value: str, width: int, *, right: bool) -> str:
    padding = max(0, width - display_width(value))
    return (" " * padding + value) if right else (value + " " * padding)


def render_table(headers: list[str], rows: list[list[str]], *, right_columns: set[int]) -> list[str]:
    values = [headers, *rows]
    widths = [max(display_width(row[index]) for row in values) for index in range(len(headers))]
    output = []
    for row in values:
        cells = [
            pad_cell(value, widths[index], right=index in right_columns)
            for index, value in enumerate(row)
        ]
        # 数値列が連続しても読み分けやすいよう、列間に2スペースを置く。
        output.append("  " + "  ".join(cells))
    return output


def load_weights(path: Path) -> dict[str, dict[str, float]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("models", {}) if isinstance(data, dict) else {}
        return {k: v for k, v in raw.items() if isinstance(v, dict)}
    except Exception:
        return {}


def weighted_cost(row: dict[str, Any], weights: dict[str, dict[str, float]]) -> float | None:
    w = weights.get(row.get("model", ""))
    if not isinstance(w, dict):
        return None

    inp = int(row.get("input_tokens", 0) or 0)
    cached = int(row.get("cached_input_tokens", 0) or 0)
    out = int(row.get("output_tokens", 0) or 0)
    reasoning = int(row.get("reasoning_tokens", 0) or 0)
    uncached = max(0, inp - cached)

    # weightの単位は「100万tokenあたりの任意の相対値」。
    # reasoning専用weightがない場合はoutput weightを使う。
    return (
        uncached * float(w.get("input", 0))
        + cached * float(w.get("cached_input", w.get("input", 0)))
        + out * float(w.get("output", 0))
        + reasoning * float(w.get("reasoning", w.get("output", 0)))
    ) / 1_000_000


def build_parser() -> argparse.ArgumentParser:
    default_metrics = Path.home() / ".codex" / "metrics"
    parser = argparse.ArgumentParser(
        description=(
            "routing-metrics.jsonlを集計し、ルーティング品質・token使用量・"
            "昇格率・待機コスト・モデル別利用量を日本語で表示します。"
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=default_metrics / "routing-metrics.jsonl",
        help="入力Metrics JSONL。既定: ~/.codex/metrics/routing-metrics.jsonl",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="直近何日分を集計するか。既定: 30",
    )
    parser.add_argument(
        "--weights",
        type=Path,
        default=default_metrics / "cost-weights.json",
        help="重み付きコスト設定JSON。既定: ~/.codex/metrics/cost-weights.json",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.days <= 0:
        parser.error("--days は1以上を指定してください。")

    if not args.input.exists():
        print(f"Metricsファイルがありません: {args.input}")
        print("先に collect.py を実行してください。")
        return 2

    rows: list[dict[str, Any]] = []
    bad_lines = 0
    with args.input.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                bad_lines += 1
                continue
            if isinstance(value, dict):
                rows.append(value)

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    rows = [
        row
        for row in rows
        if (parse_ts(row.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff
    ]
    roots = [row for row in rows if row.get("is_root")]
    weights = load_weights(args.weights)

    print(f"Codex ルーティングレポート（直近 {args.days} 日）")
    print("=" * 72)
    print(f"Rootタスク数                 {len(roots):>10,}")
    print(f"全モデルturn数               {len(rows):>10,}")
    if bad_lines:
        print(f"解析不能JSONL行               {bad_lines:>10,}")

    if not rows:
        print("\n対象期間にMetricsがありません。")
        return 0

    routes = Counter(row.get("initial_route", "UNKNOWN") for row in roots)
    print("\n■ 初期ルート")
    if routes:
        for route, count in routes.most_common():
            print(f"  {route:<28} {count:>8,}")
    else:
        print("  Rootタスクなし")

    completed = sum(row.get("status") == "COMPLETE" for row in roots)
    first = sum(bool(row.get("first_pass_success")) for row in roots)
    rework = sum(bool(row.get("possible_immediate_rework")) for row in roots)
    verify_fail = sum(row.get("verification") == "FAIL" for row in roots)

    print("\n■ 品質")
    print(f"  完了率                       {fmt_pct(pct(completed, len(roots))):>10}")
    print(f"  初回完遂率                   {fmt_pct(pct(first, len(roots))):>10}")
    print(f"  即時手戻り候補率             {fmt_pct(pct(rework, len(roots))):>10}")
    print(f"  検証失敗率                   {fmt_pct(pct(verify_fail, len(roots))):>10}")
    print("  ※ 即時手戻りはheuristic判定で、確定値ではありません。")

    result_values = [int(row.get("result_estimated_tokens", 0) or 0) for row in rows]
    user_result_values = [int(row.get("user_result_estimated_tokens", 0) or 0) for row in rows]
    result_stats = distribution(result_values)
    user_result_stats = distribution(user_result_values)
    print("\n■ Agent返却結果サイズ（推定token、文字数÷4）")
    result_rows = [
        ["assistant結果", *(f"{value:,}" for value in result_stats)],
        ["USER_RESULT", *(f"{value:,}" for value in user_result_stats)],
    ]
    print("\n".join(render_table(["項目", "平均", "中央値", "P90", "最大"], result_rows, right_columns={1, 2, 3, 4})))
    print("  ※ USER_RESULTがない既存rolloutは0として扱います。")

    terra = [row for row in roots if row.get("initial_route") == "WORKER_TERRA"]
    sol = [row for row in roots if row.get("initial_route") == "CONTROLLER_SOL"]
    terra_sol = sum((row.get("escalation_count", 0) or 0) >= 1 for row in terra)
    sol_astra = sum(row.get("final_route") == "CONTROLLER_ASTRA" for row in sol)

    print("\n■ 昇格")
    print(
        f"  Terra → Sol                  {fmt_pct(pct(terra_sol, len(terra))):>10}"
        f"  ({terra_sol}/{len(terra)})"
    )
    print(
        f"  Sol → Astra                  {fmt_pct(pct(sol_astra, len(sol))):>10}"
        f"  ({sol_astra}/{len(sol)})"
    )

    by_model = defaultdict(
        lambda: {
            "turns": 0,
            "input": 0,
            "cached": 0,
            "output": 0,
            "reasoning": 0,
            "total": 0,
            "wait": 0,
            "wait_tokens": 0,
        }
    )
    for row in rows:
        item = by_model[row.get("model") or "UNKNOWN"]
        item["turns"] += 1
        for src, dst in (
            ("input_tokens", "input"),
            ("cached_input_tokens", "cached"),
            ("output_tokens", "output"),
            ("reasoning_tokens", "reasoning"),
            ("total_tokens", "total"),
            ("wait_tool_calls", "wait"),
            ("wait_status_tokens", "wait_tokens"),
        ):
            item[dst] += int(row.get(src, 0) or 0)

    print("\n■ モデル別使用量")
    model_rows: list[list[str]] = []
    for model, item in sorted(by_model.items(), key=lambda kv: kv[1]["total"], reverse=True):
        model_rows.append([
            model,
            f"{item['turns']:,}",
            fmt_i(item["input"]),
            fmt_i(item["cached"]),
            fmt_i(item["output"]),
            fmt_i(item["reasoning"]),
            fmt_i(item["total"]),
        ])
    print("\n".join(render_table(
        ["モデル", "turn", "入力", "キャッシュ", "出力", "推論", "合計"],
        model_rows,
        right_columns={1, 2, 3, 4, 5, 6},
    )))

    task_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        root_thread = row.get("root_thread_id") or (row.get("thread_id") if row.get("is_root") else None)
        root_turn = row.get("root_turn_id") or (row.get("turn_id") if row.get("is_root") else None)
        if root_thread and root_turn:
            task_groups[(root_thread, root_turn)].append(row)

    task_totals = [
        sum(int(item.get("total_tokens", 0) or 0) for item in group)
        for group in task_groups.values()
    ]
    if task_totals:
        print("\n■ タスク単位token分布（Root + 帰属subagent）")
        print(f"  平均                         {fmt_i(statistics.mean(task_totals)):>12}")
        print(f"  中央値                       {fmt_i(statistics.median(task_totals)):>12}")
        print(f"  P90                          {fmt_i(p90(task_totals)):>12}")

    input_sum = sum(int(row.get("input_tokens", 0) or 0) for row in rows)
    cached_sum = sum(int(row.get("cached_input_tokens", 0) or 0) for row in rows)
    wait_calls = sum(int(row.get("wait_tool_calls", 0) or 0) for row in rows)
    wait_tokens = sum(int(row.get("wait_status_tokens", 0) or 0) for row in rows)
    total_tokens = sum(int(row.get("total_tokens", 0) or 0) for row in rows)

    print("\n■ Coordination / 待機")
    print(f"  キャッシュ入力比率           {fmt_pct(pct(cached_sum, input_sum)):>10}")
    print(f"  wait/status系tool call       {wait_calls:>10,}")
    print(
        f"  status-only token            {fmt_i(wait_tokens):>10}"
        f"  ({fmt_pct(pct(wait_tokens, total_tokens))})"
    )

    if weights:
        costs = [weighted_cost(row, weights) for row in rows]
        costs = [cost for cost in costs if cost is not None]
        if costs:
            print("\n■ 重み付きコスト")
            print(f"  合計                         {sum(costs):>12.4f}")
            task_costs: list[float] = []
            for group in task_groups.values():
                values = [weighted_cost(row, weights) for row in group]
                values = [value for value in values if value is not None]
                if values:
                    task_costs.append(sum(values))
            if task_costs:
                print(f"  タスク平均                   {statistics.mean(task_costs):>12.4f}")
            print(f"  weight設定                   {args.weights}")
            print("  ※ API料金やCodex subscription creditそのものではなく、設定した相対weightです。")
    else:
        print("\n■ 重み付きコスト")
        print("  無効です。cost-weights.json にモデル別weightを設定すると表示されます。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
