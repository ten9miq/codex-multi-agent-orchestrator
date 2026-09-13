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


DEFAULT_DAYS = 30


class ExplicitDaysAction(argparse.Action):
    """--days が既定値ではなく明示指定されたことを記録する。"""

    def __call__(self, parser: argparse.ArgumentParser, namespace: argparse.Namespace,
                 values: int, option_string: str | None = None) -> None:
        setattr(namespace, self.dest, values)
        setattr(namespace, "days_explicit", True)


def parse_datetime_argument(value: str) -> datetime:
    """CLIの日付時刻をUTCへ正規化する。

    offsetなしの値は実行PCのローカルタイムゾーンとして解釈する。Pythonの
    ``astimezone()`` はそのローカル設定を用いるため、Windowsのタイムゾーン設定
    （この環境では Asia/Tokyo）に従う。
    """
    text = value.strip()
    if not text:
        raise argparse.ArgumentTypeError("日時を指定してください。")

    # YYYY/MM/DD HH:MM[:SS] を fromisoformat が読める形へ変換する。
    if len(text) >= 10 and text[4:5] == "/" and text[7:8] == "/":
        text = f"{text[:4]}-{text[5:7]}-{text[8:]}"
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "日時は ISO 8601、YYYY/MM/DD HH:MM[:SS]、"
            "または YYYY-MM-DD HH:MM[:SS] で指定してください。"
        ) from exc

    # tzinfoなしはローカル時刻、offset付きはそのoffsetを保ったままUTCへ変換する。
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


def resolve_date_range(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime, str]:
    """引数からUTCの半開区間 ``[since, until)`` と表示用ラベルを返す。"""
    has_explicit_range = args.since is not None or args.until is not None
    days_explicit = bool(getattr(args, "days_explicit", False))
    if args.days <= 0:
        parser.error("--days は1以上を指定してください。")
    if days_explicit and has_explicit_range:
        parser.error("--days は --since または --until と併用できません。")

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if args.since is not None and args.until is not None:
        since, until = args.since, args.until
        label = "明示指定"
    elif args.since is not None:
        since, until = args.since, current
        label = "--since から現在"
    elif args.until is not None:
        # --daysとの併用は不許可なので、既定の直近日数を開始点に用いる。
        since, until = args.until - timedelta(days=DEFAULT_DAYS), args.until
        label = f"--until までの直近 {DEFAULT_DAYS} 日"
    else:
        since, until = current - timedelta(days=args.days), current
        label = f"直近 {args.days} 日"

    if since >= until:
        parser.error("--since は --until より前の日時を指定してください。")
    return since, until, label


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


def route_value(row: dict[str, Any], field: str) -> str:
    value = row.get(field)
    return value if isinstance(value, str) and value else "UNKNOWN"


def effective_route(row: dict[str, Any]) -> str:
    """新旧Metricsの実効Routeを互換的に読む。"""
    for field in ("effective_route", "final_route", "route"):
        value = route_value(row, field)
        if value != "UNKNOWN":
            return value
    return "UNKNOWN"


def verification_value(row: dict[str, Any]) -> str:
    value = route_value(row, "verification").upper()
    return value if value in {"PASS", "FAIL", "NOT_RUN"} else "UNKNOWN"


def rework_class_value(row: dict[str, Any]) -> str:
    """新field欠損の旧Metricsを NONE と誤認せず UNKNOWN として集計する。"""
    value = route_value(row, "rework_class")
    return value if value in {"NONE", "USER_FOLLOWUP", "MODEL_CORRECTION", "UNKNOWN"} else "UNKNOWN"


def provenance_value(row: dict[str, Any], field: str) -> str:
    """protocol fieldの出所。旧cache/JSONLの欠損はUNKNOWNとして表示する。"""
    value = row.get(field)
    return value if isinstance(value, str) and value else "UNKNOWN"


def execution_mode_value(row: dict[str, Any]) -> str:
    value = route_value(row, "execution_mode")
    return value if value in {"LEGACY_ROOT_MODEL", "ORCHESTRATED_ROUTE"} else "UNKNOWN"


def mean_or_dash(values: list[float]) -> str:
    return "-" if not values else f"{statistics.mean(values):.4f}"


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
        action=ExplicitDaysAction,
        default=DEFAULT_DAYS,
        help="直近何日分を集計するか。既定: 30。--since/--untilとは併用不可",
    )
    parser.set_defaults(days_explicit=False)
    parser.add_argument(
        "--since",
        type=parse_datetime_argument,
        metavar="DATETIME",
        help="開始日時（含む）。offsetなしはローカル時刻として解釈",
    )
    parser.add_argument(
        "--until",
        type=parse_datetime_argument,
        metavar="DATETIME",
        help=(
            "終了日時（含まない）。offsetなしはローカル時刻として解釈。"
            "単独指定時はその30日前から集計"
        ),
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

    since, until, period_label = resolve_date_range(args, parser)

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

    def is_in_period(row: dict[str, Any]) -> bool:
        timestamp = parse_ts(row.get("timestamp"))
        return timestamp is not None and since <= timestamp.astimezone(timezone.utc) < until

    rows = [row for row in rows if is_in_period(row)]
    roots = [row for row in rows if row.get("is_root")]
    weights = load_weights(args.weights)

    print(f"Codex ルーティングレポート（{period_label}）")
    print(f"対象期間（UTC、終了を含まない） {since.isoformat()} ～ {until.isoformat()}")
    print("=" * 72)
    root_threads = {
        str(row.get("root_thread_id") or row.get("thread_id"))
        for row in roots
        if row.get("root_thread_id") or row.get("thread_id")
    }
    print(f"Root turn数                  {len(roots):>10,}")
    print(f"ユニークRoot thread/session数 {len(root_threads):>10,}")
    print(f"全モデルturn数               {len(rows):>10,}")
    if bad_lines:
        print(f"解析不能JSONL行               {bad_lines:>10,}")

    if not rows:
        print("\n対象期間にMetricsがありません。")
        return 0

    task_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        root_thread = row.get("root_thread_id") or (row.get("thread_id") if row.get("is_root") else None)
        root_turn = row.get("root_turn_id") or (row.get("turn_id") if row.get("is_root") else None)
        if root_thread and root_turn:
            task_groups[(str(root_thread), str(root_turn))].append(row)

    task_summaries: list[dict[str, Any]] = []
    for root in roots:
        key = (str(root.get("root_thread_id") or root.get("thread_id") or ""), str(root.get("root_turn_id") or root.get("turn_id") or ""))
        group = task_groups.get(key, [root])
        costs = [weighted_cost(item, weights) for item in group]
        task_summaries.append({
            "root": root,
            "total_tokens": sum(int(item.get("total_tokens", 0) or 0) for item in group),
            "cost": sum(value for value in costs if value is not None) if any(value is not None for value in costs) else None,
        })

    routes = Counter(route_value(row, "initial_route") for row in roots)
    print("\n■ 初期ルート")
    if routes:
        for route, count in routes.most_common():
            print(f"  {route:<28} {count:>8,}")
    else:
        print("  Rootタスクなし")

    completed = sum(row.get("status") == "COMPLETE" for row in roots)
    first = sum(bool(row.get("first_pass_success")) for row in roots)
    rework = sum(bool(row.get("possible_immediate_rework")) for row in roots)
    verify_fail = sum(verification_value(row) == "FAIL" for row in roots)
    observed_verifications = sum(
        provenance_value(row, "verification_source") != "UNKNOWN" for row in roots
    )

    print("\n■ 品質")
    print(f"  完了率                       {fmt_pct(pct(completed, len(roots))):>10}")
    print(f"  初回完遂率                   {fmt_pct(pct(first, len(roots))):>10}")
    print(f"  即時手戻り候補率（旧heuristic） {fmt_pct(pct(rework, len(roots))):>10}")
    print(f"  検証coverage（verification_source） {fmt_pct(pct(observed_verifications, len(roots))):>10}")
    print(f"  既知内 検証FAIL率            {fmt_pct(pct(verify_fail, observed_verifications)):>10}")
    print(f"  全Root 検証FAIL率            {fmt_pct(pct(verify_fail, len(roots))):>10}")
    print("  ※ 手戻り分類は次Root turnの明示cueによるheuristicです。Route品質はRoot turn単位です。")

    print("\n■ 即時手戻り分類（Root turn単位）")
    rework_classes = Counter(rework_class_value(row) for row in roots)
    rework_rows = [
        [value, fmt_i(rework_classes[value]), fmt_pct(pct(rework_classes[value], len(roots)))]
        for value in ("NONE", "USER_FOLLOWUP", "MODEL_CORRECTION", "UNKNOWN")
    ]
    print("\n".join(render_table(
        ["rework_class", "件数", "Root turn比"], rework_rows, right_columns={1, 2}
    )))

    print("\n■ 会話内手戻り（Root thread/session単位）")
    roots_by_thread: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for root in roots:
        thread = root.get("root_thread_id") or root.get("thread_id")
        if thread:
            roots_by_thread[str(thread)].append(root)
    conversation_rows = []
    for value in ("USER_FOLLOWUP", "MODEL_CORRECTION", "UNKNOWN"):
        count = sum(
            any(rework_class_value(root) == value for root in thread_roots)
            for thread_roots in roots_by_thread.values()
        )
        conversation_rows.append([value, fmt_i(count), fmt_pct(pct(count, len(roots_by_thread)))])
    any_rework_threads = sum(
        any(rework_class_value(root) != "NONE" for root in thread_roots)
        for thread_roots in roots_by_thread.values()
    )
    conversation_rows.append(["ANY_CLASS", fmt_i(any_rework_threads), fmt_pct(pct(any_rework_threads, len(roots_by_thread)))])
    print("\n".join(render_table(
        ["thread内分類（重複可）", "thread数", "thread比"], conversation_rows, right_columns={1, 2}
    )))

    print("\n■ Route別 品質・task使用量（Root + 帰属subagent）")
    by_route: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in task_summaries:
        by_route[route_value(task["root"], "initial_route")].append(task)
    route_rows: list[list[str]] = []
    for route, tasks in sorted(by_route.items(), key=lambda item: (-len(item[1]), item[0])):
        roots_for_route = [task["root"] for task in tasks]
        costs = [float(task["cost"]) for task in tasks if task["cost"] is not None]
        totals = [int(task["total_tokens"]) for task in tasks]
        route_rows.append([
            route,
            fmt_i(len(tasks)),
            fmt_pct(pct(sum(root.get("status") == "COMPLETE" for root in roots_for_route), len(tasks))),
            fmt_pct(pct(sum(bool(root.get("first_pass_success")) for root in roots_for_route), len(tasks))),
            fmt_pct(pct(sum(bool(root.get("possible_immediate_rework")) for root in roots_for_route), len(tasks))),
            fmt_pct(pct(sum(rework_class_value(root) == "USER_FOLLOWUP" for root in roots_for_route), len(tasks))),
            fmt_pct(pct(sum(rework_class_value(root) == "MODEL_CORRECTION" for root in roots_for_route), len(tasks))),
            fmt_pct(pct(sum(rework_class_value(root) == "UNKNOWN" for root in roots_for_route), len(tasks))),
            fmt_i(statistics.mean(totals) if totals else 0),
            fmt_i(p90(totals)),
            mean_or_dash(costs),
        ])
    if route_rows:
        print("\n".join(render_table(
            ["初期Route", "Root turn数", "完了", "初回", "旧手戻り", "追加要求", "モデル訂正", "不明", "平均token", "P90", "平均cost"],
            route_rows,
            right_columns={1, 2, 3, 4, 5, 6, 7, 8, 9, 10},
        )))
    else:
        print("  Rootタスクなし")
    print("  ※ costはweight未設定のtaskを除いた平均です。")

    print("\n■ 検証結果（Root）")
    verification_rows = []
    verifications = Counter(verification_value(row) for row in roots)
    for value in ("PASS", "FAIL", "NOT_RUN", "UNKNOWN"):
        count = verifications[value]
        verification_rows.append([value, fmt_i(count), fmt_pct(pct(count, len(roots)))])
    print("\n".join(render_table(
        ["verification", "件数", "割合"], verification_rows, right_columns={1, 2}
    )))

    print("\n■ 完了状態・検証の観測範囲と出所（Root）")
    status_sources = Counter(provenance_value(row, "status_source") for row in roots)
    status_source_order = [
        "assistant_message",
        "task_complete.last_agent_message",
        "completion_event",
        "UNKNOWN",
    ]
    remaining_status_sources = sorted(set(status_sources) - set(status_source_order))
    status_source_rows = [
        [source, fmt_i(status_sources[source]), fmt_pct(pct(status_sources[source], len(roots)))]
        for source in [*status_source_order, *remaining_status_sources]
        if status_sources[source]
    ]
    print("\n".join(render_table(
        ["status_source", "件数", "割合"], status_source_rows, right_columns={1, 2}
    )))

    print(
        f"  verification_sourceを観測したRoot {fmt_i(observed_verifications):>10}"
        f"/{fmt_i(len(roots))} ({fmt_pct(pct(observed_verifications, len(roots)))})"
    )
    print(f"  既知内FAIL率                   {fmt_pct(pct(verify_fail, observed_verifications)):>10}")
    print(f"  全Root FAIL率                  {fmt_pct(pct(verify_fail, len(roots))):>10}")
    verification_sources = Counter(
        provenance_value(row, "verification_source") for row in roots
    )
    source_order = ["assistant_message", "task_complete.last_agent_message", "UNKNOWN"]
    remaining_sources = sorted(set(verification_sources) - set(source_order))
    source_rows = [
        [source, fmt_i(verification_sources[source]), fmt_pct(pct(verification_sources[source], len(roots)))]
        for source in [*source_order, *remaining_sources]
        if verification_sources[source]
    ]
    print("\n".join(render_table(
        ["verification_source", "件数", "割合"], source_rows, right_columns={1, 2}
    )))

    print("\n■ Route別 検証結果（Root）")
    route_verification_rows: list[list[str]] = []
    for route, route_roots in sorted(
        ((route, [row for row in roots if route_value(row, "initial_route") == route]) for route in routes),
        key=lambda item: item[0],
    ):
        values = Counter(verification_value(row) for row in route_roots)
        route_verification_rows.append([
            route,
            fmt_i(len(route_roots)),
            *(fmt_i(values[value]) for value in ("PASS", "FAIL", "NOT_RUN", "UNKNOWN")),
        ])
    if route_verification_rows:
        print("\n".join(render_table(
            ["初期Route", "件数", "PASS", "FAIL", "NOT_RUN", "UNKNOWN"],
            route_verification_rows,
            right_columns={1, 2, 3, 4, 5},
        )))
    else:
        print("  Rootタスクなし")

    print("\n■ 実行モード（Root）")
    execution_modes = Counter(execution_mode_value(row) for row in roots)
    execution_rows = [
        [mode, fmt_i(execution_modes[mode]), fmt_pct(pct(execution_modes[mode], len(roots)))]
        for mode in ("ORCHESTRATED_ROUTE", "LEGACY_ROOT_MODEL", "UNKNOWN")
    ]
    print("\n".join(render_table(
        ["execution_mode", "件数", "割合"], execution_rows, right_columns={1, 2}
    )))

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

    terra = [row for row in roots if route_value(row, "initial_route") == "WORKER_TERRA"]
    sol = [row for row in roots if route_value(row, "initial_route") == "CONTROLLER_SOL"]
    terra_sol = sum((row.get("escalation_count", 0) or 0) >= 1 for row in terra)
    sol_astra = sum(effective_route(row) == "CONTROLLER_ASTRA" for row in sol)
    escalated = sum((row.get("escalation_count", 0) or 0) >= 1 for row in roots)

    print("\n■ 昇格")
    print(
        f"  Terra → Sol                  {fmt_pct(pct(terra_sol, len(terra))):>10}"
        f"  ({terra_sol}/{len(terra)})"
    )
    print(
        f"  Sol → Astra                  {fmt_pct(pct(sol_astra, len(sol))):>10}"
        f"  ({sol_astra}/{len(sol)})"
    )
    print(
        f"  全Rootで昇格あり             {fmt_pct(pct(escalated, len(roots))):>10}"
        f"  ({escalated}/{len(roots)})"
    )
    transitions = Counter(
        (route_value(row, "initial_route"), effective_route(row))
        for row in roots
    )
    print("\n  initial_route → effective_route")
    transition_rows = [
        [initial, effective, fmt_i(count)]
        for (initial, effective), count in sorted(transitions.items(), key=lambda item: (-item[1], item[0]))
    ]
    print("\n".join(render_table(
        ["initial_route", "effective_route", "件数"], transition_rows, right_columns={2}
    )))

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

    auto_review_rows = [
        row for row in rows
        if str(row.get("model") or "").lower().startswith("codex-auto-review")
    ]
    print("\n■ Auto Review（通常のRouting taskとは別枠）")
    if auto_review_rows:
        auto_input = sum(int(row.get("input_tokens", 0) or 0) for row in auto_review_rows)
        auto_cached = sum(int(row.get("cached_input_tokens", 0) or 0) for row in auto_review_rows)
        auto_output = sum(int(row.get("output_tokens", 0) or 0) for row in auto_review_rows)
        auto_reasoning = sum(int(row.get("reasoning_tokens", 0) or 0) for row in auto_review_rows)
        auto_total = sum(int(row.get("total_tokens", 0) or 0) for row in auto_review_rows)
        all_total = sum(int(row.get("total_tokens", 0) or 0) for row in rows)
        auto_costs = [weighted_cost(row, weights) for row in auto_review_rows]
        auto_costs = [cost for cost in auto_costs if cost is not None]
        print(f"  turn                         {fmt_i(len(auto_review_rows)):>12}")
        print(f"  total token                  {fmt_i(auto_total):>12}")
        print(f"  input / cached / uncached    {fmt_i(auto_input)} / {fmt_i(auto_cached)} / {fmt_i(max(0, auto_input - auto_cached))}")
        print(f"  output / reasoning           {fmt_i(auto_output)} / {fmt_i(auto_reasoning)}")
        print(f"  cached input比率             {fmt_pct(pct(auto_cached, auto_input)):>12}")
        print(f"  全total tokenに占める比率    {fmt_pct(pct(auto_total, all_total)):>12}")
        if auto_costs:
            print(f"  weighted cost                {sum(auto_costs):>12.4f}")
        else:
            print("  weighted cost                - (model weight未設定)")
    else:
        print("  対象turnなし")

    context_by_model: dict[str, list[tuple[int, int | None, float | None]]] = defaultdict(list)
    for row in rows:
        peak = row.get("context_peak_tokens")
        if isinstance(peak, bool):
            continue
        try:
            peak_value = int(peak)
        except (TypeError, ValueError):
            continue
        window = row.get("context_window")
        try:
            window_value = int(window) if window is not None else None
        except (TypeError, ValueError):
            window_value = None
        usage = row.get("context_peak_usage_pct")
        try:
            usage_value = float(usage) if usage is not None else None
        except (TypeError, ValueError):
            usage_value = None
        if usage_value is None and window_value and window_value > 0:
            usage_value = peak_value * 100.0 / window_value
        context_by_model[route_value(row, "model")].append((peak_value, window_value, usage_value))

    print("\n■ Context peak（turnごとのlast_token_usage由来。累積tokenではありません）")
    context_rows: list[list[str]] = []
    for model, values in sorted(context_by_model.items(), key=lambda item: (-len(item[1]), item[0])):
        peaks = [item[0] for item in values]
        windows = [item[1] for item in values if item[1] is not None and item[1] > 0]
        usages = [item[2] for item in values if item[2] is not None]
        context_rows.append([
            model,
            fmt_i(len(values)),
            fmt_i(statistics.median(windows)) if windows else "-",
            fmt_i(statistics.mean(peaks)),
            fmt_i(p90(peaks)),
            fmt_i(max(peaks)),
            fmt_pct(statistics.mean(usages)) if usages else "-",
            fmt_pct(p90(usages)) if usages else "-",
        ])
    if context_rows:
        print("\n".join(render_table(
            ["model", "観測turn", "window中央値", "peak平均", "peak P90", "peak最大", "peak使用率平均", "P90"],
            context_rows,
            right_columns={1, 2, 3, 4, 5, 6, 7},
        )))
    else:
        print("  context snapshotを持つturnなし")

    print("\n■ Context window 分布（turn）")
    context_windows = Counter()
    for row in rows:
        value = row.get("context_window")
        if isinstance(value, bool):
            context_windows["UNKNOWN"] += 1
            continue
        try:
            window = int(value)
        except (TypeError, ValueError):
            window = 0
        context_windows[fmt_i(window) if window > 0 else "UNKNOWN"] += 1
    print("\n".join(render_table(
        ["context_window", "turn"],
        [[window, fmt_i(count)] for window, count in sorted(
            context_windows.items(), key=lambda item: (item[0] == "UNKNOWN", item[0])
        )],
        right_columns={1},
    )))

    compaction_count = sum(int(row.get("compaction_count", 0) or 0) for row in rows)
    compaction_tokens: list[int] = []
    compaction_windows: list[int] = []
    for row in rows:
        tokens = row.get("compaction_context_tokens")
        windows = row.get("compaction_context_windows")
        if isinstance(tokens, list):
            for value in tokens:
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    compaction_tokens.append(value)
        if isinstance(windows, list):
            for value in windows:
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    compaction_windows.append(value)
    print("\n■ Compaction（rolloutのcompacted event観測）")
    print(f"  compaction event              {fmt_i(compaction_count):>10}")
    print(f"  発生時Context観測             {fmt_i(len(compaction_tokens))}/{fmt_i(compaction_count)}")
    if compaction_tokens:
        token_stats = distribution(compaction_tokens)
        print("\n".join(render_table(
            ["発生時Context token", "平均", "中央値", "P90", "最大"],
            [["observed", *(fmt_i(value) for value in token_stats)]],
            right_columns={1, 2, 3, 4},
        )))
        if compaction_windows:
            print(f"  発生時window中央値            {fmt_i(statistics.median(compaction_windows)):>10}")
    else:
        print("  発生時Context                 UNKNOWN（対応eventまたはsnapshotなし）")

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
