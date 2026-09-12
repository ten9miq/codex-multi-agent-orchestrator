#!/usr/bin/env python3
"""Metrics JSONL またはlive rolloutを session / turn / agent の時系列として表示する。

このスクリプトは Metrics JSONL を読み取るだけで、session、state、設定には書き込まない。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from collect import attach_root_tasks
from rollout_reader import (
    JsonlStats,
    event_type,
    first_key,
    iter_jsonl,
    parse_rollout_turns,
    parse_ts,
    source_metadata,
)


UNASSIGNED = "(unassigned)"


def text(value: Any) -> str | None:
    """空でない文字列だけを ID として扱う。"""
    return value.strip() if isinstance(value, str) and value.strip() else None


def number(row: dict[str, Any], field: str) -> int:
    value = row.get(field, 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def row_timestamp(row: dict[str, Any]) -> datetime | None:
    return parse_ts(row.get("timestamp"))


def sort_key(row: dict[str, Any]) -> tuple[datetime, str, str]:
    # 時刻なしの行は最後に置く。turn_id も使い、同時刻でも出力を安定させる。
    return (
        row_timestamp(row) or datetime.max.replace(tzinfo=timezone.utc),
        text(row.get("turn_id")) or "",
        text(row.get("thread_id")) or "",
    )


def root_thread_id(row: dict[str, Any]) -> str | None:
    """collect.py と同じく Root 自身は thread_id を session ID として補う。"""
    return text(row.get("root_thread_id")) or (
        text(row.get("thread_id")) if row.get("is_root") else None
    )


def root_turn_id(row: dict[str, Any]) -> str | None:
    """Root の root_turn_id が欠落した古い行だけ、自己 turn を補う。"""
    return text(row.get("root_turn_id")) or (
        text(row.get("turn_id")) if row.get("is_root") else None
    )


def load_rows(path: Path) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    bad_lines = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                bad_lines += 1
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows, bad_lines


@dataclass
class LiveTurnHint:
    """詳細解析前の選別に必要なturn境界だけを保持する。"""

    turn_id: str = ""
    timestamp: str = ""
    end_timestamp: str = ""


@dataclass(eq=False)
class LiveRolloutHint:
    """rolloutの軽量なsession/親子/turn索引。永続化はしない。"""

    path: Path
    thread_id: str
    parent_thread_id: str | None = None
    is_root: bool = True
    turns: list[LiveTurnHint] = field(default_factory=list)
    json_errors: int = 0


def scan_live_rollout(path: Path) -> LiveRolloutHint:
    """token・tool本文を解釈せず、session metadataとturn境界だけを読む。"""
    thread_id = path.stem
    parent: str | None = None
    is_root = True
    turns: list[LiveTurnHint] = []
    current: LiveTurnHint | None = None
    stats = JsonlStats()

    for obj in iter_jsonl(path, stats):
        typ = obj.get("type")
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else obj
        if typ == "session_meta":
            value = first_key(payload, "id") or first_key(payload, "thread_id")
            if isinstance(value, str) and value:
                thread_id = value
            is_child, _, parent, _ = source_metadata(payload)
            is_root = not is_child

        event = event_type(obj)
        if event in {"task_started", "turn_started"}:
            if current is not None:
                turns.append(current)
            timestamp = obj.get("timestamp") or first_key(payload, "timestamp") or ""
            turn_id = first_key(payload, "turn_id") or first_key(payload, "id") or ""
            current = LiveTurnHint(turn_id=str(turn_id), timestamp=str(timestamp))
        elif event in {"task_complete", "turn_complete"} and current is not None:
            current.end_timestamp = str(
                obj.get("timestamp") or first_key(payload, "timestamp") or ""
            )
            turns.append(current)
            current = None

    if current is not None:
        turns.append(current)
    return LiveRolloutHint(
        path=path,
        thread_id=thread_id,
        parent_thread_id=parent,
        is_root=is_root,
        turns=turns,
        json_errors=stats.json_errors,
    )


def _unique_parent_map(hints: list[LiveRolloutHint]) -> dict[LiveRolloutHint, LiveRolloutHint]:
    """一意に解決できる親だけを結ぶ。ID衝突時は別sessionを誤結合しない。"""
    by_thread: dict[str, list[LiveRolloutHint]] = defaultdict(list)
    for hint in hints:
        by_thread[hint.thread_id].append(hint)

    parents: dict[LiveRolloutHint, LiveRolloutHint] = {}
    for hint in hints:
        if not hint.parent_thread_id:
            continue
        candidates = by_thread.get(hint.parent_thread_id, [])
        if len(candidates) == 1 and candidates[0] is not hint:
            parents[hint] = candidates[0]
    return parents


def _root_key(
    hint: LiveRolloutHint,
    parents: dict[LiveRolloutHint, LiveRolloutHint],
) -> tuple[str, str]:
    """session grouping keyと、解決できたroot thread IDを返す。"""
    seen: set[LiveRolloutHint] = set()
    current = hint
    while current not in seen:
        seen.add(current)
        if current.is_root:
            return ("root", current.thread_id)
        parent = parents.get(current)
        if parent is None:
            break
        current = parent
    # 欠損・循環・ID衝突はpath単位に隔離し、別sessionへ混ぜない。
    return ("unassigned", str(hint.path.resolve()))


def _hint_in_period(hint: LiveRolloutHint, cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    return any(
        (stamp := parse_ts(turn.timestamp)) is not None and stamp >= cutoff
        for turn in hint.turns
    )


def load_live_rows(
    sessions_root: Path,
    *,
    cutoff: datetime | None = None,
    root_thread: str | None = None,
) -> tuple[list[dict[str, Any]], list[str], int, int]:
    """軽量索引で候補を絞り、必要なrolloutだけを既存ロジックで詳細解析する。

    候補childの祖先も詳細解析するため、期間外に始まったRoot turnへの帰属を維持する。
    session単位で ``attach_root_tasks`` を呼び、別session間の誤結合を避ける。
    collect.pyのstate/cache/outputには触れない。
    """
    hints: list[LiveRolloutHint] = []
    unreadable: list[str] = []
    files = sorted(sessions_root.rglob("rollout-*.jsonl"))
    for path in files:
        try:
            hints.append(scan_live_rollout(path))
        except (OSError, PermissionError) as exc:
            unreadable.append(f"{path}: {exc}")

    parents = _unique_parent_map(hints)
    roots = {hint: _root_key(hint, parents) for hint in hints}
    selected: set[LiveRolloutHint] = set()
    for hint in hints:
        root_id = roots[hint][1] if roots[hint][0] == "root" else None
        session_matches = not root_thread or root_thread in (root_id, hint.thread_id)
        if session_matches and _hint_in_period(hint, cutoff):
            selected.add(hint)

    # attach_root_tasksがRoot turnを時刻で選べるよう、選択childの祖先を含める。
    for hint in list(selected):
        current = hint
        seen: set[LiveRolloutHint] = set()
        while current not in seen and current in parents:
            seen.add(current)
            current = parents[current]
            selected.add(current)

    turns_by_session: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for hint in sorted(selected, key=lambda item: str(item.path)):
        try:
            turns_by_session[roots[hint]].extend(parse_rollout_turns(hint.path))
        except (OSError, PermissionError) as exc:
            unreadable.append(f"{hint.path}: {exc}")

    turns = []
    for session_turns in turns_by_session.values():
        attach_root_tasks(session_turns)
        turns.extend(session_turns)
    return [turn.public() for turn in turns], unreadable, len(files), len(selected)


def filter_rows(
    rows: Iterable[dict[str, Any]],
    *,
    cutoff: datetime | None,
    root_thread: str | None,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for row in rows:
        stamp = row_timestamp(row)
        if cutoff is not None and (stamp is None or stamp < cutoff):
            continue
        if root_thread:
            # --session は root session と、root ID未解決時の当該thread の両方を指定できる。
            if root_thread not in (root_thread_id(row), text(row.get("thread_id"))):
                continue
        selected.append(row)
    return selected


def agent_record(row: dict[str, Any]) -> dict[str, Any]:
    """表示/JSON用の、1つのMetrics turnに対応するagentノード。"""
    return {
        "timestamp": text(row.get("timestamp")),
        "end_timestamp": text(row.get("end_timestamp")),
        "thread_id": text(row.get("thread_id")),
        "parent_thread_id": text(row.get("parent_thread_id")),
        "turn_id": text(row.get("turn_id")),
        "is_root": bool(row.get("is_root")),
        "agent_role": text(row.get("agent_role")),
        "route": text(row.get("final_route")) or text(row.get("route")) or "UNKNOWN",
        "model": text(row.get("model")) or "UNKNOWN",
        "reasoning_effort": text(row.get("reasoning_effort")),
        "status": text(row.get("status")) or "UNKNOWN",
        "verification": text(row.get("verification")) or "UNKNOWN",
        "duration_seconds": row.get("duration_seconds"),
        "tokens": {
            "input": number(row, "input_tokens"),
            "cached_input": number(row, "cached_input_tokens"),
            "output": number(row, "output_tokens"),
            "reasoning": number(row, "reasoning_tokens"),
            "total": number(row, "total_tokens"),
        },
    }


def unassigned_session_key(row: dict[str, Any], index: int) -> str:
    """Root session が分からない行を、別 session と誤って統合しないための内部キー。"""
    return "unassigned:" + (
        text(row.get("thread_id")) or text(row.get("turn_id")) or str(index)
    )


def build_sessions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """root_thread_id -> root_turn_id -> parent_thread_id/thread_id の三層を構築する。"""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    session_labels: dict[str, str] = {}
    for index, row in enumerate(rows):
        session_id = root_thread_id(row)
        if session_id:
            key = session_id
            session_labels[key] = session_id
        else:
            key = unassigned_session_key(row, index)
            session_labels[key] = UNASSIGNED
        grouped[key].append(row)

    sessions: list[dict[str, Any]] = []
    for session_key, session_rows in grouped.items():
        turns_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
        turn_labels: dict[str, str] = {}
        for index, row in enumerate(session_rows):
            turn_id = root_turn_id(row)
            if turn_id:
                key = turn_id
                turn_labels[key] = turn_id
            else:
                # root_turn_idなしchildは、同一session内でも他turnに混ぜない。
                key = "unassigned:" + (
                    text(row.get("thread_id")) or text(row.get("turn_id")) or str(index)
                )
                turn_labels[key] = UNASSIGNED
            turns_by_key[key].append(row)

        turns: list[dict[str, Any]] = []
        for turn_key, turn_rows in turns_by_key.items():
            turns.append(
                {
                    "root_turn_id": turn_labels[turn_key],
                    "agents": build_agent_tree(turn_rows),
                    "start_timestamp": text(min(turn_rows, key=sort_key).get("timestamp")),
                }
            )
        turns.sort(key=lambda item: parse_ts(item["start_timestamp"]) or datetime.max.replace(tzinfo=timezone.utc))
        sessions.append(
            {
                "root_thread_id": session_labels[session_key],
                "turns": turns,
                "start_timestamp": text(min(session_rows, key=sort_key).get("timestamp")),
            }
        )
    sessions.sort(key=lambda item: parse_ts(item["start_timestamp"]) or datetime.max.replace(tzinfo=timezone.utc))
    return sessions


def build_agent_tree(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """1 root-turn内だけで parent_thread_id を解決してagent treeにする。"""
    ordered = sorted(rows, key=sort_key)
    nodes = [{**agent_record(row), "children": []} for row in ordered]
    by_thread: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        if node["thread_id"]:
            by_thread[node["thread_id"]].append(node)

    roots: list[dict[str, Any]] = []
    parent_by_node: dict[int, dict[str, Any]] = {}
    for node in nodes:
        parent_id = node["parent_thread_id"]
        parent: dict[str, Any] | None = None
        if parent_id and parent_id != node["thread_id"]:
            candidates = by_thread.get(parent_id, [])
            if candidates:
                # 同一threadに複数recordがある場合は、子より前の最新recordを親にする。
                preceding = [
                    candidate
                    for candidate in candidates
                    if (candidate["timestamp"] or "") <= (node["timestamp"] or "")
                ]
                parent = preceding[-1] if preceding else candidates[0]
        # 壊れた入力の循環parentは、このturn内で親子化しない。
        seen = {id(node)}
        ancestor = parent
        while ancestor is not None and id(ancestor) not in seen:
            seen.add(id(ancestor))
            ancestor = parent_by_node.get(id(ancestor))
        if ancestor is not None:
            parent = None
        if parent is None:
            roots.append(node)
        else:
            parent_by_node[id(node)] = parent
            parent["children"].append(node)
    return roots


def fmt_id(value: str | None) -> str:
    """人間向け表示だけで長いIDを末尾8文字へ短縮する。"""
    if not value:
        return UNASSIGNED
    if value == UNASSIGNED or len(value) <= 12:
        return value
    return value[-8:]


def fmt_text(value: str | None) -> str:
    """IDではない表示値を短縮せず、欠損値だけ補う。"""
    return value if value else UNASSIGNED


def fmt_timestamp(value: str | None) -> str:
    """人間向け時刻を実行環境のローカルタイムゾーンで表示する。"""
    parsed = parse_ts(value)
    return parsed.astimezone().isoformat() if parsed is not None else fmt_text(value)


def fmt_agent(node: dict[str, Any], show_tokens: bool) -> str:
    role = node["agent_role"] or ("root" if node["is_root"] else "agent")
    parts = [
        f"{role} thread={fmt_id(node['thread_id'])}",
        f"turn={fmt_id(node['turn_id'])}",
        f"{node['model']}",
        node["route"],
        node["status"],
    ]
    if node["verification"] != "UNKNOWN":
        parts.append(f"verify={node['verification']}")
    if show_tokens:
        token = node["tokens"]
        parts.append(
            "tokens="
            f"{token['total']:,} (in={token['input']:,}, cache={token['cached_input']:,}, "
            f"out={token['output']:,}, reason={token['reasoning']:,})"
        )
    return " | ".join(parts)


def render_agent_tree(nodes: list[dict[str, Any]], prefix: str, show_tokens: bool) -> list[str]:
    lines: list[str] = []
    for index, node in enumerate(nodes):
        last = index == len(nodes) - 1
        branch = "└─" if last else "├─"
        lines.append(f"{prefix}{branch} Agent {fmt_agent(node, show_tokens)}")
        lines.extend(render_agent_tree(node["children"], prefix + ("   " if last else "│  "), show_tokens))
    return lines


def render_sessions(sessions: list[dict[str, Any]], show_tokens: bool) -> str:
    lines = ["Codex Timeline (Session → Turn → Agent)", "=" * 72]
    if not sessions:
        lines.append("対象のMetrics行はありません。")
        return "\n".join(lines)
    for session in sessions:
        label = fmt_id(session["root_thread_id"])
        lines.append(f"Session {label}  start={fmt_timestamp(session['start_timestamp'])}")
        for turn in session["turns"]:
            lines.append(f"  Turn {fmt_id(turn['root_turn_id'])}  start={fmt_timestamp(turn['start_timestamp'])}")
            lines.extend(render_agent_tree(turn["agents"], "  ", show_tokens))
    return "\n".join(lines)


def build_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in sorted(rows, key=sort_key):
        event = agent_record(row)
        event["root_thread_id"] = root_thread_id(row) or UNASSIGNED
        event["root_turn_id"] = root_turn_id(row) or UNASSIGNED
        events.append(event)
    return events


def render_events(events: list[dict[str, Any]], show_tokens: bool) -> str:
    lines = ["Codex Timeline Events (timestamp order)", "=" * 72]
    if not events:
        lines.append("対象のMetrics行はありません。")
        return "\n".join(lines)
    for event in events:
        parts = [
            fmt_timestamp(event["timestamp"]),
            f"session={fmt_id(event['root_thread_id'])}",
            f"root-turn={fmt_id(event['root_turn_id'])}",
            fmt_agent(event, show_tokens),
        ]
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def parse_since(value: str) -> datetime:
    parsed = parse_ts(value)
    if parsed is None:
        raise argparse.ArgumentTypeError("ISO 8601形式の時刻を指定してください。")
    return parsed.astimezone(timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="routing-metrics.jsonlをSession → Turn → Agentまたはイベント時系列で表示します。"
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")),
        help="Codexホーム。既定: CODEX_HOME または ~/.codex",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="入力Metrics JSONL。既定: <codex-home>/metrics/routing-metrics.jsonl",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="collect.pyを介さず <codex-home>/sessions/**/rollout-*.jsonl を直接解析します。",
    )
    parser.add_argument("--minutes", type=float, help="直近N分だけ表示します。")
    parser.add_argument("--since", type=parse_since, help="このISO 8601時刻以降だけ表示します。")
    parser.add_argument(
        "--session",
        "--root-thread",
        dest="root_thread",
        help="root thread/session IDで絞り込みます。",
    )
    parser.add_argument(
        "--view",
        choices=("sessions", "events"),
        default="sessions",
        help="表示形式。既定: sessions",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable JSONで出力します。")
    parser.add_argument("--show-tokens", action="store_true", help="各Agent/Eventにtoken内訳を表示します。")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.minutes is not None and args.minutes < 0:
        parser.error("--minutes は0以上を指定してください。")
    if args.live and args.input is not None:
        parser.error("--live と --input は同時に指定できません。")

    cutoff = args.since
    if args.minutes is not None:
        by_minutes = datetime.now(timezone.utc) - timedelta(minutes=args.minutes)
        cutoff = max((item for item in (cutoff, by_minutes) if item is not None), default=None)

    unreadable: list[str] = []
    if args.live:
        sessions_root = args.codex_home.expanduser() / "sessions"
        if not sessions_root.is_dir():
            print(f"sessionsディレクトリがありません: {sessions_root}", file=sys.stderr)
            return 2
        rows, unreadable, rollout_files, parsed_rollout_files = load_live_rows(
            sessions_root,
            cutoff=cutoff,
            root_thread=args.root_thread,
        )
        input_label = str(sessions_root)
        bad_lines = 0
    else:
        input_path = (
            args.input
            or (args.codex_home.expanduser() / "metrics" / "routing-metrics.jsonl")
        ).expanduser()
        if not input_path.is_file():
            print(f"Metricsファイルがありません: {input_path}", file=sys.stderr)
            print("先に collect.py を実行するか、--live を指定してください。", file=sys.stderr)
            return 2
        rows, bad_lines = load_rows(input_path)
        input_label = str(input_path)
        rollout_files = None
        parsed_rollout_files = None

    rows = filter_rows(rows, cutoff=cutoff, root_thread=args.root_thread)
    if args.view == "sessions":
        data: dict[str, Any] = {
            "view": "sessions",
            "input": input_label,
            "bad_json_lines": bad_lines,
            "sessions": build_sessions(rows),
        }
        output = json.dumps(data, ensure_ascii=False, indent=2) if args.json else render_sessions(data["sessions"], args.show_tokens)
    else:
        data = {
            "view": "events",
            "input": input_label,
            "bad_json_lines": bad_lines,
            "events": build_events(rows),
        }
        output = json.dumps(data, ensure_ascii=False, indent=2) if args.json else render_events(data["events"], args.show_tokens)
    if args.live:
        data["live"] = True
        data["rollout_files"] = rollout_files
        data["parsed_rollout_files"] = parsed_rollout_files
        data["unreadable_rollouts"] = unreadable
        output = json.dumps(data, ensure_ascii=False, indent=2) if args.json else output
    print(output)
    if bad_lines and not args.json:
        print(f"\n注意: 解析不能JSONL行を {bad_lines} 行無視しました。", file=sys.stderr)
    if unreadable and not args.json:
        print(f"\n注意: 読み取れないrolloutを {len(unreadable)} 件無視しました。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
