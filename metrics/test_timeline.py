from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import timeline


def session_meta(thread_id: str, parent: str | None = None) -> dict:
    source = {} if parent is None else {"subagent": {"parent_thread_id": parent}}
    return {"type": "session_meta", "payload": {"id": thread_id, "source": source}}


def event(kind: str, timestamp: str, turn_id: str) -> dict:
    return {
        "type": "event_msg",
        "timestamp": timestamp,
        "payload": {"type": kind, "turn_id": turn_id},
    }


def write_rollout(path: Path, values: list[dict], incomplete_tail: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(json.dumps(value) + "\n" for value in values)
    if incomplete_tail:
        content += '{"type":"event_msg"'
    path.write_text(content, encoding="utf-8")


class LiveTimelineTests(unittest.TestCase):
    def test_recent_child_keeps_old_root_turn_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            sessions = Path(temp)
            write_rollout(
                sessions / "rollout-root.jsonl",
                [
                    session_meta("root-a"),
                    event("task_started", "2026-01-01T00:00:00Z", "root-turn"),
                    event("task_complete", "2026-01-01T02:00:00Z", "root-turn"),
                ],
            )
            write_rollout(
                sessions / "rollout-child.jsonl",
                [
                    session_meta("child-a", "root-a"),
                    event("task_started", "2026-01-01T01:00:00Z", "child-turn"),
                    event("task_complete", "2026-01-01T01:01:00Z", "child-turn"),
                ],
                incomplete_tail=True,
            )

            cutoff = datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc)
            rows, unreadable, total, parsed = timeline.load_live_rows(
                sessions, cutoff=cutoff
            )
            selected = timeline.filter_rows(rows, cutoff=cutoff, root_thread=None)

            self.assertEqual(unreadable, [])
            self.assertEqual((total, parsed), (2, 2))
            self.assertEqual([row["thread_id"] for row in selected], ["child-a"])
            self.assertEqual(selected[0]["root_thread_id"], "root-a")
            self.assertEqual(selected[0]["root_turn_id"], "root-turn")

    def test_session_filter_does_not_include_another_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            sessions = Path(temp)
            for suffix, root_id, child_id in (
                ("a", "root-a", "child-a"),
                ("b", "root-b", "child-b"),
            ):
                write_rollout(
                    sessions / f"rollout-root-{suffix}.jsonl",
                    [
                        session_meta(root_id),
                        event("task_started", "2026-01-01T00:00:00Z", f"turn-{suffix}"),
                        event("task_complete", "2026-01-01T02:00:00Z", f"turn-{suffix}"),
                    ],
                )
                write_rollout(
                    sessions / f"rollout-child-{suffix}.jsonl",
                    [
                        session_meta(child_id, root_id),
                        event("task_started", "2026-01-01T01:00:00Z", f"child-turn-{suffix}"),
                        event("task_complete", "2026-01-01T01:01:00Z", f"child-turn-{suffix}"),
                    ],
                )

            rows, unreadable, total, parsed = timeline.load_live_rows(
                sessions, root_thread="root-a"
            )
            selected = timeline.filter_rows(rows, cutoff=None, root_thread="root-a")

            self.assertEqual(unreadable, [])
            self.assertEqual((total, parsed), (4, 2))
            self.assertEqual({row["thread_id"] for row in selected}, {"root-a", "child-a"})
            self.assertTrue(all(row["root_thread_id"] == "root-a" for row in selected))


if __name__ == "__main__":
    unittest.main()
