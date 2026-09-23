#!/usr/bin/env python3
"""Codex Multi-Agentの実効配線をrollout JSONLから検証するSmoke Testツール。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from rollout_reader import SessionSummary, parse_session_summary, parse_ts

ROLE_PROFILES = {
    "scout": ("gpt-6-luna", "medium", "v2", True),
    "worker_luna": ("gpt-6-luna", "high", "v2", True),
    "worker_sol": ("gpt-6-sol", "medium", "v2", True),
    "controller_sol": ("gpt-6-sol", "medium", "v2", False),
    "expert": ("gpt-6-sol", "xhigh", "v2", True),
    "controller_astra": ("gpt-6-astra", "high", "v2", False),
}
EXPECT_CHOICES = (
    "root",
    "scout",
    "worker_luna",
    "worker_sol",
    "controller_sol",
    "controller_sol_scout",
    "controller_astra",
)


def parse_since(value: str) -> datetime:
    parsed = parse_ts(value)
    if parsed is None:
        raise argparse.ArgumentTypeError(
            f"--since を解釈できません: {value!r}。"
            "ISO-8601形式を指定してください。例: 2026-09-12T20:31:28+09:00"
        )
    return parsed.astimezone(timezone.utc)


def short_id(value: str | None) -> str:
    if not value:
        return "-"
    return value if len(value) <= 12 else value[-8:]


def file_mtime_utc(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def depth_for(session: SessionSummary, by_id: dict[str, SessionSummary]) -> int:
    depth = 0
    current = session
    seen: set[str] = set()
    while current.parent_thread_id and current.parent_thread_id in by_id:
        if current.session_id in seen:
            return 999
        seen.add(current.session_id)
        depth += 1
        current = by_id[current.parent_thread_id]
    return depth


def child_map(records: Iterable[SessionSummary]) -> dict[str, list[SessionSummary]]:
    output: dict[str, list[SessionSummary]] = {}
    for record in records:
        if record.parent_thread_id:
            output.setdefault(record.parent_thread_id, []).append(record)
    for values in output.values():
        values.sort(key=lambda item: item.first_timestamp or item.path)
    return output


def is_orchestrator_record(record: SessionSummary) -> bool:
    """Smoke Testで検証すべきRoot/ThreadSpawn agentか判定する。

    Guardian/Auto Review/Compact/Review等の内部補助sessionは
    SessionSource上はsubagentでも、今回のOrchestrator treeには属さない。
    parent_thread_idもagent_roleもないchild sessionは検証対象外とする。
    """
    if not record.is_child:
        return True
    return bool(record.parent_thread_id or record.agent_role)


def validate_profile(
    record: SessionSummary,
    *,
    is_root: bool,
    depth: int,
    child_count: int,
) -> list[str]:
    issues: list[str] = []

    if record.turn_context_count == 0:
        issues.append("turn_context が見つかりません")

    if is_root:
        expected = ("gpt-6-luna", "medium", "v2")
        actual = (record.model, record.reasoning_effort, record.multi_agent_version)
        labels = ("model", "effort", "multi_agent_version")
        for label, exp, got in zip(labels, expected, actual):
            if got != exp:
                issues.append(
                    f"Root {label}: 期待値={exp}, 実効値={got or '<missing>'}"
                )
    else:
        if not record.agent_role:
            issues.append("agent_role がありません")
        elif record.agent_role not in ROLE_PROFILES:
            issues.append(f"未知の agent_role: {record.agent_role}")
        else:
            model, effort, mav, leaf = ROLE_PROFILES[record.agent_role]
            for label, exp, got in (
                ("model", model, record.model),
                ("effort", effort, record.reasoning_effort),
                ("multi_agent_version", mav, record.multi_agent_version),
            ):
                if got != exp:
                    issues.append(
                        f"{record.agent_role} {label}: 期待値={exp}, 実効値={got or '<missing>'}"
                    )
            if leaf and child_count:
                issues.append(
                    f"{record.agent_role} はLeafですが、child agentが {child_count} 体あります"
                )

    if depth > 2:
        issues.append(
            f"agent階層が深すぎます: depth={depth}。"
            "想定上限は Root -> Controller -> Leaf です"
        )

    # 書き込み中JSONLの末尾1行が不完全なのは通常起こり得る。
    if record.json_errors > 1:
        issues.append(f"JSONとして解析できない行が {record.json_errors} 行あります")

    return issues


def find_roots(
    records: list[SessionSummary],
    by_id: dict[str, SessionSummary],
) -> list[SessionSummary]:
    roots = [
        record
        for record in records
        if not record.parent_thread_id or record.parent_thread_id not in by_id
    ]
    return sorted(roots, key=lambda item: item.first_timestamp or item.path)


def shape_issues(
    expect: str | None,
    roots: list[SessionSummary],
    children: dict[str, list[SessionSummary]],
) -> list[str]:
    if not expect:
        return []

    issues: list[str] = []
    if len(roots) != 1:
        issues.append(
            f"--expect {expect} では選択時間内にRootが1つだけ必要です。"
            f"検出数: {len(roots)}"
        )
        return issues

    root = roots[0]
    root_children = children.get(root.session_id, [])
    roles = [item.agent_role for item in root_children]

    if expect == "root":
        if root_children:
            issues.append(f"Rootのみを期待しましたが、child roleがあります: {roles}")
    elif expect == "scout":
        if roles != ["scout"]:
            issues.append(f"期待: Root -> scout、実際のRoot child: {roles}")
    elif expect == "worker_luna":
        if roles != ["worker_luna"]:
            issues.append(f"期待: Root -> worker_luna、実際のRoot child: {roles}")
    elif expect == "worker_sol":
        if roles != ["worker_sol"]:
            issues.append(f"期待: Root -> worker_sol、実際のRoot child: {roles}")
    elif expect == "controller_sol":
        if roles != ["controller_sol"]:
            issues.append(f"期待: Root -> controller_sol、実際のRoot child: {roles}")
    elif expect == "controller_astra":
        if roles != ["controller_astra"]:
            issues.append(f"期待: Root -> controller_astra、実際のRoot child: {roles}")
    elif expect == "controller_sol_scout":
        if roles != ["controller_sol"]:
            issues.append(f"期待: Root -> controller_sol、実際のRoot child: {roles}")
        else:
            controller = root_children[0]
            nested = children.get(controller.session_id, [])
            nested_roles = [item.agent_role for item in nested]
            if nested_roles != ["scout"]:
                issues.append(
                    f"期待: controller_sol -> scout、実際のController child: {nested_roles}"
                )

    return issues


def print_tree(
    roots: list[SessionSummary],
    children: dict[str, list[SessionSummary]],
    issues_by_id: dict[str, list[str]],
    *,
    show_context: bool = False,
) -> None:
    visited: set[str] = set()

    def visit(record: SessionSummary, prefix: str, is_last: bool, is_root: bool) -> None:
        if record.session_id in visited:
            print(f"{prefix}[WARN] 重複/循環を検出: id={short_id(record.session_id)}")
            return
        visited.add(record.session_id)

        branch = "" if is_root else ("└─ " if is_last else "├─ ")
        status = "OK" if not issues_by_id.get(record.session_id) else "WARN"
        role = "ROOT" if is_root else (record.agent_role or "(role missing)")
        task = f" | task={record.task_name}" if record.task_name else ""

        print(
            f"{prefix}{branch}[{status}] {role} | "
            f"{record.model or '<missing>'} / {record.reasoning_effort or '<missing>'} / "
            f"{record.multi_agent_version or '<missing>'} | id={short_id(record.session_id)}{task}"
        )
        if show_context:
            print(f"{prefix}   Context: {format_context(record)}")

        for issue in issues_by_id.get(record.session_id, []):
            print(f"{prefix}   ! {issue}")

        items = children.get(record.session_id, [])
        next_prefix = prefix if is_root else prefix + ("   " if is_last else "│  ")
        for index, child in enumerate(items):
            visit(child, next_prefix, index == len(items) - 1, False)

    for root in roots:
        visit(root, "", True, True)
        print()


def format_context(record: SessionSummary) -> str:
    if record.context_window is None:
        return "window=<unknown>; current=<unknown>; peak=<unknown>; usage=<unknown>"

    def tokens(value: int | None) -> str:
        return str(value) if value is not None else "<unknown>"

    def percent(value: float | None) -> str:
        return f"{value:.1f}%" if value is not None else "<unknown>"

    return (
        f"window={record.context_window}; current={tokens(record.context_tokens)} "
        f"({percent(record.context_usage_pct)}); peak={tokens(record.context_peak_tokens)} "
        f"({percent(record.context_peak_usage_pct)})"
    )


def context_warnings(records: Iterable[SessionSummary]) -> list[str]:
    """contextは観測補助。欠損で既存の配線PASS/FAILを変えない。"""
    warnings: list[str] = []
    for record in records:
        missing = []
        if record.context_window is None:
            missing.append("window")
        if record.context_tokens is None:
            missing.append("usage")
        if missing:
            warnings.append(
                f"id={short_id(record.session_id)}: context {', '.join(missing)} がJSONLにありません"
            )
    return warnings


def print_table(records: list[SessionSummary], *, show_context: bool = False) -> None:
    # 機械識別子は英語のまま維持し、見出しだけ日本語化する。
    headers = ("Session", "Parent", "Role", "Model", "Effort", "MAV", "Task")
    rows = [
        (
            short_id(item.session_id),
            short_id(item.parent_thread_id),
            item.agent_role or "ROOT",
            item.model or "-",
            item.reasoning_effort or "-",
            item.multi_agent_version or "-",
            item.task_name or "-",
        )
        for item in records
    ]
    if show_context:
        headers = (*headers, "Context")
        rows = [(*row, format_context(item)) for row, item in zip(rows, records)]

    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(str(value)))

    print("  ".join(str(value).ljust(widths[index]) for index, value in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "指定時間以降のCodex rollout JSONLを解析し、"
            "Root/Child/Grandchildの実効 model・effort・Multi-Agent runtime・role を検証します。"
        )
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")),
        help="Codexホーム。既定: CODEX_HOME または ~/.codex",
    )
    parser.add_argument(
        "--sessions-root",
        type=Path,
        default=None,
        help="sessionsディレクトリを直接指定。通常は不要です。",
    )
    parser.add_argument(
        "--since",
        type=parse_since,
        default=None,
        help="この時刻以降を対象にする。ISO-8601/.NET ToString('o')形式を利用できます。",
    )
    parser.add_argument(
        "--minutes",
        type=int,
        default=10,
        help="--since未指定時、直近何分を見るか。既定: 10",
    )
    parser.add_argument(
        "--table",
        action="store_true",
        help="ツリーに加えてsession詳細表を表示する。",
    )
    parser.add_argument(
        "--context",
        action="store_true",
        help="実効context window、現在/peak使用量、使用率を表示する。欠損は非致命の警告として表示する。",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="人間向け表示ではなくJSONで結果を出力する。",
    )
    parser.add_argument(
        "--expect",
        choices=EXPECT_CHOICES,
        default=None,
        help=(
            "期待する配線形状を厳密チェックする。"
            "例: controller_sol_scout = Root -> controller_sol -> scout"
        ),
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.minutes <= 0:
        parser.error("--minutes は1以上を指定してください。")

    sessions_root = args.sessions_root or (args.codex_home.expanduser() / "sessions")
    cutoff = args.since or (datetime.now(timezone.utc) - timedelta(minutes=args.minutes))

    if not sessions_root.exists():
        print(f"sessionsディレクトリがありません: {sessions_root}", file=sys.stderr)
        return 2

    files: list[Path] = []
    for path in sessions_root.rglob("rollout-*.jsonl"):
        try:
            if file_mtime_utc(path) >= cutoff:
                files.append(path)
        except OSError:
            continue
    files.sort(key=lambda path: path.stat().st_mtime)

    if not files:
        print(f"{cutoff.astimezone().isoformat()} 以降のrolloutが見つかりません。")
        print("Smoke Test直後なら --minutes 15 など対象時間を広げてください。")
        return 2

    records: list[SessionSummary] = []
    unreadable: list[str] = []
    for path in files:
        try:
            records.append(parse_session_summary(path))
        except OSError as exc:
            unreadable.append(f"{path}: {exc}")

    # Smoke Testでは、今回のOrchestrator treeに属するRoot/ThreadSpawn agentだけを検証する。
    # codex-auto-review / Guardian / Review / Compact等の内部補助sessionは除外する。
    ignored_records = [record for record in records if not is_orchestrator_record(record)]
    records = [record for record in records if is_orchestrator_record(record)]

    by_id = {record.session_id: record for record in records}
    children = child_map(records)
    roots = find_roots(records, by_id)

    issues_by_id: dict[str, list[str]] = {}
    for record in records:
        issues = validate_profile(
            record,
            is_root=record in roots,
            depth=depth_for(record, by_id),
            child_count=len(children.get(record.session_id, [])),
        )
        if issues:
            issues_by_id[record.session_id] = issues

    shape = shape_issues(args.expect, roots, children)
    observed_context_warnings = context_warnings(records)
    warning_count = (
        sum(len(value) for value in issues_by_id.values())
        + len(shape)
        + len(unreadable)
    )

    if args.json_output:
        result = {
            "since": cutoff.isoformat(),
            "expect": args.expect,
            "pass": warning_count == 0,
            "warnings": warning_count,
            "shape_issues": shape,
            "unreadable": unreadable,
            "sessions": [asdict(record) for record in records],
            "ignored_internal_sessions": [asdict(record) for record in ignored_records],
            "profile_issues": issues_by_id,
            "context_warnings": observed_context_warnings,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if warning_count == 0 else 1

    print("Codex Multi-Agent Smoke Test")
    print(f"対象開始時刻 : {cutoff.astimezone().isoformat()}")
    print(f"検証rollout数 : {len(records)}")
    if ignored_records:
        print(f"内部補助除外数 : {len(ignored_records)}")
    if args.expect:
        print(f"期待配線       : {args.expect}")
    print()

    print_tree(roots, children, issues_by_id, show_context=args.context)

    if args.table:
        print("■ Session詳細")
        print_table(records, show_context=args.context)
        print()
        if ignored_records:
            print("■ Smoke Test対象外の内部補助session")
            for item in ignored_records:
                print(
                    f"  {short_id(item.session_id)}  "
                    f"{item.model or '-'} / {item.reasoning_effort or '-'} / "
                    f"{item.multi_agent_version or '-'}"
                )
            print()

    for issue in shape:
        print(f"! 配線: {issue}")
    for issue in unreadable:
        print(f"! 読取: {issue}")
    if args.context:
        for issue in observed_context_warnings:
            print(f"! Context（非致命）: {issue}")

    if warning_count == 0:
        print("RESULT: PASS")
        print("実効model / effort / Multi-Agent runtime / role配線は期待値と一致しています。")
        return 0

    print(f"RESULT: CHECK（警告 {warning_count} 件）")
    print("上記の「!」行を確認してください。設定または期待した配線形状と実効値が異なります。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
