from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import report


class ReportTests(unittest.TestCase):
    def test_route_quality_context_verification_and_auto_review_sections(self) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        rows = [
            {
                "timestamp": timestamp,
                "is_root": True,
                "thread_id": "root-a",
                "turn_id": "turn-a",
                "root_thread_id": "root-a",
                "root_turn_id": "turn-a",
                "initial_route": "DIRECT_LUNA",
                "final_route": "DIRECT_LUNA",
                "effective_route": "DIRECT_LUNA",
                "model": "gpt-5.6-luna",
                "status": "COMPLETE",
                "verification": "PASS",
                "first_pass_success": True,
                "total_tokens": 120,
                "input_tokens": 100,
                "cached_input_tokens": 80,
                "output_tokens": 20,
                "context_window": 1000,
                "context_peak_tokens": 400,
                "context_peak_usage_pct": 40.0,
            },
            {
                "timestamp": timestamp,
                "is_root": False,
                "thread_id": "child-a",
                "turn_id": "child-turn-a",
                "root_thread_id": "root-a",
                "root_turn_id": "turn-a",
                "model": "gpt-5.6-terra",
                "total_tokens": 80,
                "input_tokens": 60,
                "cached_input_tokens": 30,
                "output_tokens": 20,
                "context_window": 1000,
                "context_peak_tokens": 500,
                "context_peak_usage_pct": 50.0,
            },
            {
                "timestamp": timestamp,
                "is_root": True,
                "thread_id": "root-b",
                "turn_id": "turn-b",
                "root_thread_id": "root-b",
                "root_turn_id": "turn-b",
                "initial_route": "WORKER_TERRA",
                "final_route": "CONTROLLER_SOL",
                "model": "gpt-5.6-terra",
                "status": "COMPLETE",
                "verification": "NOT_RUN",
                "possible_immediate_rework": True,
                "escalation_count": 1,
                "total_tokens": 200,
                "input_tokens": 150,
                "cached_input_tokens": 100,
                "output_tokens": 30,
                "context_window": 1000,
                "context_peak_tokens": 750,
                "context_peak_usage_pct": 75.0,
            },
            {
                "timestamp": timestamp,
                "is_root": False,
                "thread_id": "review-a",
                "turn_id": "review-turn-a",
                "model": "codex-auto-review",
                "total_tokens": 50,
                "input_tokens": 40,
                "cached_input_tokens": 20,
                "output_tokens": 10,
                "context_window": 1000,
                "context_peak_tokens": 300,
                "context_peak_usage_pct": 30.0,
            },
        ]
        weights = {
            "models": {
                "gpt-5.6-luna": {"input": 1, "cached_input": 0.1, "output": 2},
                "gpt-5.6-terra": {"input": 2, "cached_input": 0.2, "output": 4},
            }
        }
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            input_path = temp_path / "metrics.jsonl"
            weights_path = temp_path / "weights.json"
            input_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            weights_path.write_text(json.dumps(weights), encoding="utf-8")
            output = io.StringIO()
            previous_argv = sys.argv
            try:
                sys.argv = ["report.py", "--input", str(input_path), "--weights", str(weights_path)]
                with contextlib.redirect_stdout(output):
                    result = report.main()
            finally:
                sys.argv = previous_argv

        self.assertEqual(result, 0)
        rendered = output.getvalue()
        self.assertIn("Route別 品質・task使用量", rendered)
        self.assertIn("DIRECT_LUNA", rendered)
        self.assertIn("WORKER_TERRA", rendered)
        self.assertIn("検証結果（Root）", rendered)
        self.assertIn("NOT_RUN", rendered)
        self.assertIn("initial_route → effective_route", rendered)
        self.assertIn("CONTROLLER_SOL", rendered)
        self.assertIn("Auto Review（通常のRouting taskとは別枠）", rendered)
        self.assertIn("Context peak", rendered)


if __name__ == "__main__":
    unittest.main()
