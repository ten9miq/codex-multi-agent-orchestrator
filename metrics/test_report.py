from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import report


class ReportTests(unittest.TestCase):
    def test_datetime_argument_normalizes_offsets_and_accepts_requested_formats(self) -> None:
        self.assertEqual(
            report.parse_datetime_argument("2026-01-02T03:04:05+09:00"),
            datetime(2026, 1, 1, 18, 4, 5, tzinfo=timezone.utc),
        )
        self.assertEqual(
            report.parse_datetime_argument("2026/01/02 03:04+09:00"),
            datetime(2026, 1, 1, 18, 4, tzinfo=timezone.utc),
        )
        self.assertEqual(
            report.parse_datetime_argument("2026-01-02 03:04:05Z"),
            datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        )

    def test_explicit_range_is_half_open_and_displayed_in_utc(self) -> None:
        rows = [
            {"timestamp": "2026-01-01T23:59:59Z", "is_root": True},
            {"timestamp": "2026-01-02T00:30:00Z", "is_root": True},
            {"timestamp": "2026-01-02T01:00:00Z", "is_root": True},
        ]
        with tempfile.TemporaryDirectory() as temp:
            input_path = Path(temp) / "metrics.jsonl"
            input_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            output = io.StringIO()
            previous_argv = sys.argv
            try:
                sys.argv = [
                    "report.py", "--input", str(input_path),
                    "--since", "2026/01/02 09:00",
                    "--until", "2026-01-02T10:00:00+09:00",
                ]
                with contextlib.redirect_stdout(output):
                    result = report.main()
            finally:
                sys.argv = previous_argv

        self.assertEqual(result, 0)
        rendered = output.getvalue()
        self.assertIn("対象期間（UTC、終了を含まない） 2026-01-02T00:00:00+00:00 ～ 2026-01-02T01:00:00+00:00", rendered)
        self.assertIn("Root turn数", rendered)
        self.assertIn("ユニークRoot thread/session数", rendered)

    def test_until_only_uses_default_days_and_days_conflicts_with_range(self) -> None:
        parser = report.build_parser()
        until = report.parse_datetime_argument("2026-02-01T00:00:00Z")
        args = parser.parse_args(["--until", "2026-02-01T00:00:00Z"])
        since, resolved_until, label = report.resolve_date_range(
            args, parser, now=datetime(2026, 3, 1, tzinfo=timezone.utc)
        )
        self.assertEqual(since, until - timedelta(days=report.DEFAULT_DAYS))
        self.assertEqual(resolved_until, until)
        self.assertEqual(label, "--until までの直近 30 日")

        conflicting = parser.parse_args([
            "--days", "7", "--since", "2026-02-01T00:00:00Z",
        ])
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stderr(io.StringIO()):
            report.resolve_date_range(conflicting, parser)
        self.assertEqual(raised.exception.code, 2)

    def test_route_quality_context_verification_and_auto_review_sections(self) -> None:
        # Reportのhalf-open終端と同じtickにならないよう、少し前の時刻を使う。
        timestamp = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
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
                "route_source": "model",
                "execution_mode": "ORCHESTRATED_ROUTE",
                "model": "gpt-5.6-luna",
                "status": "COMPLETE",
                "status_source": "completion_event",
                "verification": "PASS",
                "verification_source": "task_complete.last_agent_message",
                "first_pass_success": True,
                "rework_class": "NONE",
                "total_tokens": 120,
                "input_tokens": 100,
                "cached_input_tokens": 80,
                "output_tokens": 20,
                "context_window": 1000,
                "context_peak_tokens": 400,
                "context_peak_usage_pct": 40.0,
                "compaction_count": 1,
                "compaction_context_tokens": [400],
                "compaction_context_windows": [1000],
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
                "route_source": "model_and_spawn_agent",
                "execution_mode": "LEGACY_ROOT_MODEL",
                "model": "gpt-5.6-terra",
                "status": "COMPLETE",
                "verification": "NOT_RUN",
                "verification_source": "assistant_message",
                "possible_immediate_rework": True,
                "rework_class": "MODEL_CORRECTION",
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
        self.assertIn("即時手戻り分類（Root turn単位）", rendered)
        self.assertIn("会話内手戻り（Root thread/session単位）", rendered)
        self.assertIn("USER_FOLLOWUP", rendered)
        self.assertIn("MODEL_CORRECTION", rendered)
        self.assertIn("検証coverage（verification_source）", rendered)
        self.assertIn("既知内 検証FAIL率", rendered)
        self.assertIn("全Root 検証FAIL率", rendered)
        self.assertIn("DIRECT_LUNA", rendered)
        self.assertIn("Route判定の出所（Root）", rendered)
        self.assertIn("model推定のDIRECT_LUNA", rendered)
        self.assertIn("明示的なDirect選択を証明しません", rendered)
        self.assertIn("WORKER_TERRA", rendered)
        self.assertIn("検証結果（Root）", rendered)
        self.assertIn("完了状態・検証の観測範囲と出所（Root）", rendered)
        self.assertIn("completion_event", rendered)
        self.assertIn("task_complete.last_agent_message", rendered)
        self.assertIn("assistant_message", rendered)
        self.assertIn("Route別 検証結果（Root）", rendered)
        self.assertIn("NOT_RUN", rendered)
        self.assertIn("実行モード（Root）", rendered)
        self.assertIn("ORCHESTRATED_ROUTE", rendered)
        self.assertIn("LEGACY_ROOT_MODEL", rendered)
        self.assertIn("initial_route → effective_route", rendered)
        self.assertIn("CONTROLLER_SOL", rendered)
        self.assertIn("Auto Review（通常のRouting taskとは別枠）", rendered)
        self.assertIn("Context peak", rendered)
        self.assertIn("Context window 分布", rendered)
        self.assertIn("Compaction", rendered)
        self.assertIn("Codex ルーティングレポート（直近 30 日）", rendered)


if __name__ == "__main__":
    unittest.main()
