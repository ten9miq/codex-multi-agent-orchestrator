#!/usr/bin/env python3
"""Codex rollout JSONLからルーティング/使用量Metricsを収集する。"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from rollout_reader import Turn, parse_rollout_turns, parse_ts

# 連続Root turnの手戻り分類を追加したため、旧cue cacheは再利用しない。
VERSION = 6


def attach_root_tasks(turns: list[Turn]) -> None:
    parent_by_thread: dict[str, str | None] = {}
    for turn in turns:
        parent_by_thread.setdefault(turn.thread_id, turn.parent_thread_id)

    def find_root(thread_id: str) -> str:
        seen: set[str] = set()
        current = thread_id
        while current not in seen:
            seen.add(current)
            parent = parent_by_thread.get(current)
            if not parent:
                return current
            current = parent
        return thread_id

    roots_by_thread: dict[str, list[Turn]] = {}
    for turn in turns:
        if turn.is_root:
            turn.root_thread_id = turn.thread_id
            turn.root_turn_id = turn.turn_id
            roots_by_thread.setdefault(turn.thread_id, []).append(turn)
    for items in roots_by_thread.values():
        items.sort(key=lambda item: parse_ts(item.timestamp) or datetime.min.replace(tzinfo=timezone.utc))

    for turn in turns:
        if turn.is_root:
            continue
        root_thread = find_root(turn.thread_id)
        turn.root_thread_id = root_thread
        child_start = parse_ts(turn.timestamp)
        candidates = roots_by_thread.get(root_thread, [])
        chosen: Turn | None = None
        if child_start:
            for root_turn in candidates:
                start = parse_ts(root_turn.timestamp)
                end = parse_ts(root_turn.end_timestamp)
                if start and start <= child_start and (end is None or child_start <= end):
                    chosen = root_turn
                elif start and start <= child_start and chosen is None:
                    chosen = root_turn
        if chosen is None and candidates:
            chosen = candidates[-1]
        if chosen is not None:
            turn.root_turn_id = chosen.turn_id


def mark_rework(turns: list[Turn]) -> None:
    """連続Root turnのcueを前turnへ転記し、旧heuristic互換の印も維持する。"""
    by_thread: dict[str, list[Turn]] = {}
    for turn in turns:
        if turn.is_root:
            by_thread.setdefault(turn.thread_id, []).append(turn)
    for items in by_thread.values():
        items.sort(key=lambda item: parse_ts(item.timestamp) or datetime.min.replace(tzinfo=timezone.utc))
        for previous, current in zip(items, items[1:]):
            start = parse_ts(previous.end_timestamp or previous.timestamp)
            end = parse_ts(current.timestamp)
            if not start or not end:
                continue
            if not 0 <= (end - start).total_seconds() <= 30 * 60:
                continue
            cue_class = current.user_rework_class
            if cue_class == "NONE":
                continue
            previous.rework_class = cue_class
            # 旧fieldは旧patternだけで決め、新しい分類による意味の変更を避ける。
            if current.user_legacy_rework_cue:
                previous.possible_immediate_rework = True
            if cue_class in {"MODEL_CORRECTION", "UNKNOWN"}:
                previous.first_pass_success = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Codexの ~/.codex/sessions/**/rollout-*.jsonl を読み取り、"
            "ルーティング・token使用量・昇格・待機などを routing-metrics.jsonl へ集計します。"
        )
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")),
        help="Codexホーム。既定: CODEX_HOME または ~/.codex",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="出力JSONL。既定: <CODEX_HOME>/metrics/routing-metrics.jsonl",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    home = args.codex_home.expanduser()
    sessions = home / "sessions"
    output = args.output or (home / "metrics" / "routing-metrics.jsonl")
    state_path = output.parent / "state.json"
    output.parent.mkdir(parents=True, exist_ok=True)

    state: dict = {"version": VERSION, "files": {}}
    if state_path.exists():
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("version") == VERSION:
                state = loaded
        except Exception:
            print("注意: state.jsonを読み込めなかったため、新しい状態として再解析します。")

    cache = state.setdefault("files", {})
    live_paths: set[str] = set()
    all_turns: list[Turn] = []
    files = sorted(sessions.rglob("rollout-*.jsonl")) if sessions.exists() else []

    if not sessions.exists():
        print(f"注意: sessionsディレクトリがありません: {sessions}")

    for path in files:
        key = str(path.resolve())
        live_paths.add(key)
        try:
            stat = path.stat()
        except OSError:
            continue
        signature = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        cached = cache.get(key)
        if cached and cached.get("sig") == signature and isinstance(cached.get("turns"), list):
            for data in cached["turns"]:
                keep = {
                    key_: value
                    for key_, value in data.items()
                    if key_ in Turn.__dataclass_fields__ and not key_.startswith("_")
                }
                all_turns.append(Turn(**keep))
            continue

        try:
            parsed = parse_rollout_turns(path)
        except (OSError, PermissionError) as exc:
            print(f"警告: 読み取れないrolloutをスキップしました: {path} ({exc})")
            continue
        cache[key] = {"sig": signature, "turns": [turn.cache_record() for turn in parsed]}
        all_turns.extend(parsed)

    for key in list(cache):
        if key not in live_paths:
            cache.pop(key, None)

    attach_root_tasks(all_turns)
    mark_rework(all_turns)
    all_turns.sort(
        key=lambda turn: (
            parse_ts(turn.timestamp) or datetime.min.replace(tzinfo=timezone.utc),
            turn.thread_id,
            turn.turn_id,
        )
    )

    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for turn in all_turns:
            handle.write(json.dumps(turn.public(), ensure_ascii=False, separators=(",", ":")) + "\n")

    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state["record_count"] = len(all_turns)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Codex Metrics収集完了")
    print(f"  rolloutファイル数 : {len(files):,}")
    print(f"  turnレコード数     : {len(all_turns):,}")
    print(f"  出力先             : {output}")
    print(f"  状態ファイル       : {state_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
