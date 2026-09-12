from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

import smoke
from rollout_reader import parse_rollout_turns, parse_session_summary


def write_rollout(path: Path, values: list[dict]) -> None:
    path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")


def rollout_values(*, include_context: bool = True) -> list[dict]:
    values = [
        {"type": "session_meta", "payload": {"id": "root-a", "source": {}}},
        {
            "type": "turn_context",
            "payload": {"model": "gpt-5.6-luna", "effort": "medium", "multi_agent_version": "v2"},
        },
        {
            "type": "event_msg",
            "timestamp": "2026-01-01T00:00:00Z",
            "payload": {"type": "task_started", "turn_id": "turn-a", "model_context_window": 800},
        },
    ]
    if include_context:
        for tokens in (120, 300):
            values.append(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {"total_tokens": tokens * 2},
                            "last_token_usage": {"total_tokens": tokens},
                            "model_context_window": 1000,
                        },
                    },
                }
            )
    values.append(
        {
            "type": "event_msg",
            "timestamp": "2026-01-01T00:00:01Z",
            "payload": {"type": "task_complete", "turn_id": "turn-a"},
        }
    )
    return values


class ContextObservabilityTests(unittest.TestCase):
    def test_effective_context_uses_last_token_snapshot_and_tracks_peak(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rollout-context.jsonl"
            write_rollout(path, rollout_values())

            summary = parse_session_summary(path)
            turns = parse_rollout_turns(path)

        self.assertEqual(summary.context_window, 1000)
        self.assertEqual(summary.context_tokens, 300)
        self.assertEqual(summary.context_peak_tokens, 300)
        self.assertEqual(summary.context_usage_pct, 30.0)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].context_window, 1000)
        self.assertEqual(turns[0].context_peak_tokens, 300)
        self.assertEqual(turns[0].context_peak_usage_pct, 30.0)
        self.assertEqual(turns[0].effective_route, turns[0].final_route)
        self.assertEqual(turns[0].public()["context_usage_pct"], 30.0)
        self.assertEqual(turns[0].cache_record()["context_peak_tokens"], 300)

    def test_missing_context_is_nonfatal_even_with_expect(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            sessions = Path(temp)
            write_rollout(sessions / "rollout-context.jsonl", rollout_values(include_context=False))
            output = io.StringIO()
            argv = sys.argv
            try:
                sys.argv = ["smoke.py", "--sessions-root", str(sessions), "--minutes", "9999999", "--expect", "root", "--context", "--json"]
                with contextlib.redirect_stdout(output):
                    result = smoke.main()
            finally:
                sys.argv = argv

        payload = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertTrue(payload["pass"])
        self.assertEqual(payload["warnings"], 0)
        self.assertEqual(len(payload["context_warnings"]), 1)
        self.assertIsNone(payload["sessions"][0]["context_tokens"])


if __name__ == "__main__":
    unittest.main()
