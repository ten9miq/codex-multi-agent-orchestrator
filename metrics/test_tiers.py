"""Tier evidence regression tests use synthetic files, never local sessions."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import collect
import report
import smoke
from rollout_reader import Turn, parse_rollout_turns, parse_session_summary
from test_context import rollout_values, write_rollout


def context(tier: object = "default", **fields: object) -> dict:
    return {"type": "turn_context", "timestamp": "2026-01-01T00:00:00.5Z",
            "payload": {"service_tier": tier, **fields}}


class TierProvenanceTests(unittest.TestCase):
    def parse(self, values: list[dict]) -> tuple:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rollout-tiers.jsonl"
            write_rollout(path, values)
            return parse_rollout_turns(path), parse_session_summary(path)

    def test_known_turn_tier_preserves_source_without_request_claim(self) -> None:
        for raw, expected in (("default", "standard"), ("priority", "fast"),
                              ("standard", "standard"), (" FAST ", "fast")):
            with self.subTest(raw=raw):
                values = rollout_values()
                values[1]["payload"]["service_tier"] = raw
                turns, summary = self.parse(values)
                for record in (turns[0], summary):
                    self.assertEqual(record.service_tier, expected)
                    self.assertEqual(record.observed_service_tier, expected)
                    self.assertEqual(record.service_tier_evidence, "observed_turn")
                    self.assertEqual(record.service_tier_source, "turn_context.service_tier")
                    self.assertEqual(record.service_tier_observations[0]["raw_value"], raw)
                    self.assertEqual(record.service_tier_observations[0]["event_index"], 2)
                    self.assertEqual(record.configured_service_tier, "UNKNOWN")
                    self.assertEqual(record.requested_service_tier, "UNKNOWN")
                    self.assertEqual(record.request_tier_status, "UNVERIFIED")
                    self.assertIsNone(record.requested_service_tier_source)

    def test_midturn_changes_are_preserved_and_never_last_value_wins(self) -> None:
        for extra in (context("priority"), context("auto"), context(None),
                      {"type": "turn_context", "payload": {}}):
            with self.subTest(extra=extra):
                values = rollout_values()
                values.insert(4, extra)
                # A later known observation must not erase an unknown or change.
                values.insert(-1, context("default"))
                turns, summary = self.parse(values)
                for record in (turns[0], summary):
                    self.assertEqual(record.service_tier, "UNKNOWN")
                    self.assertEqual(record.observed_service_tier, "UNKNOWN")
                    self.assertEqual(len(record.service_tier_observations), 3)
                self.assertEqual(turns[0].service_tier_observations[1]["timestamp"], extra.get("timestamp"))
                self.assertEqual(turns[0].service_tier_observations[1]["event_index"], 5)
                self.assertEqual(turns[0].total_tokens, 420)
                self.assertTrue(smoke.validate_profile(summary, is_root=True, depth=0, child_count=0))

    def test_duplicate_uniform_context_is_known_but_still_not_request_evidence(self) -> None:
        values = rollout_values()
        values.insert(4, context("standard"))
        turns, _ = self.parse(values)
        self.assertEqual(turns[0].service_tier, "standard")
        self.assertEqual(len(turns[0].service_tier_observations), 2)
        self.assertEqual(turns[0].request_tier_status, "UNVERIFIED")

    def test_tier_does_not_carry_into_next_turn(self) -> None:
        values = rollout_values()
        values.extend([
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-b"}},
            {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn-b"}},
        ])
        turns, summary = self.parse(values)
        self.assertEqual(summary.service_tier, "UNKNOWN")
        self.assertEqual(summary.tier_unobserved_turn_count, 1)
        self.assertTrue(smoke.validate_profile(summary, is_root=True, depth=0, child_count=0))
        self.assertEqual(turns[0].service_tier, "standard")
        self.assertEqual(turns[1].service_tier, "UNKNOWN")
        self.assertEqual(turns[1].service_tier_evidence, "unknown")
        self.assertEqual(turns[1].service_tier_observations, [])

    def test_context_after_start_and_explicit_turn_mismatch(self) -> None:
        values = rollout_values()
        values.pop(1)
        values.insert(2, context("default", turn_id="turn-a"))
        values.insert(4, context("priority", turn_id="different-history-turn"))
        turns, _ = self.parse(values)
        self.assertEqual(turns[0].service_tier, "standard")
        self.assertEqual(len(turns[0].service_tier_observations), 1)

    def test_prestart_context_for_other_turn_is_not_attached(self) -> None:
        values = rollout_values()
        values[1]["payload"]["turn_id"] = "old-turn"
        turns, _ = self.parse(values)
        self.assertEqual(turns[0].service_tier, "UNKNOWN")
        self.assertEqual(turns[0].service_tier_observations, [])

    def test_nested_or_unsupported_events_do_not_create_stronger_evidence(self) -> None:
        values = rollout_values()
        values[1]["payload"].pop("service_tier")
        values[1]["payload"]["other"] = {"service_tier": "priority"}
        values.insert(-1, {"type": "unknown_request_event", "payload": {
            "requested_service_tier": "default", "service_tier": "priority"}})
        turns, summary = self.parse(values)
        for record in (turns[0], summary):
            self.assertEqual(record.service_tier, "UNKNOWN")
            self.assertEqual(record.requested_service_tier, "UNKNOWN")
            self.assertEqual(record.request_tier_status, "UNVERIFIED")

    def test_public_and_cache_roundtrip_preserve_evidence(self) -> None:
        values = rollout_values()
        values.insert(4, context("priority"))
        turns, _ = self.parse(values)
        cached = json.loads(json.dumps(turns[0].cache_record()))
        restored = Turn(**cached)
        self.assertEqual(restored.public(), turns[0].public())
        self.assertEqual(len(restored.service_tier_observations), 2)
        self.assertEqual(restored.service_tier, "UNKNOWN")

    def test_collect_invalidates_old_cache_and_reuses_new_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            sessions = home / "sessions"
            sessions.mkdir()
            source = sessions / "rollout-cache.jsonl"
            values = rollout_values()
            values.insert(4, context("priority"))
            write_rollout(source, values)
            output = home / "metrics" / "routing-metrics.jsonl"
            output.parent.mkdir()
            state_path = output.parent / "state.json"
            stat = source.stat()
            state_path.write_text(json.dumps({"version": 9, "files": {str(source.resolve()): {
                "sig": {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns},
                "turns": [{"turn_id": "stale", "service_tier": "fast",
                           "requested_service_tier": "fast"}],
            }}}), encoding="utf-8")
            # Current config must not rewrite evidence from historical rollout.
            (home / "config.toml").write_text('service_tier = "default"\n', encoding="utf-8")
            argv = ["collect.py", "--codex-home", str(home)]
            with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(collect.main(), 0)
            first = output.read_text(encoding="utf-8")
            row = json.loads(first)
            self.assertEqual(row["turn_id"], "turn-a")
            self.assertEqual(row["service_tier"], "UNKNOWN")
            self.assertEqual(row["configured_service_tier"], "UNKNOWN")
            self.assertEqual(row["requested_service_tier"], "UNKNOWN")
            self.assertEqual(len(row["service_tier_observations"]), 2)
            self.assertEqual(json.loads(state_path.read_text())["version"], collect.VERSION)
            with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()), \
                    patch.object(collect, "parse_rollout_turns", side_effect=AssertionError("cache miss")):
                self.assertEqual(collect.main(), 0)
            self.assertEqual(output.read_text(encoding="utf-8"), first)

    def test_smoke_pass_is_explicitly_limited_and_request_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            write_rollout(Path(temp) / "rollout-smoke.jsonl", rollout_values())
            argv = ["smoke.py", "--sessions-root", temp, "--minutes", "9999999", "--expect", "root"]
            output = io.StringIO()
            with patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
                self.assertEqual(smoke.main(), 0)
            self.assertIn("RESULT: PASS (TURN_CONTEXT_PROFILE_ONLY)", output.getvalue())
            self.assertIn("REQUEST_TIER: UNVERIFIED", output.getvalue())
            output = io.StringIO()
            with patch.object(sys, "argv", argv + ["--json"]), contextlib.redirect_stdout(output):
                self.assertEqual(smoke.main(), 0)
            result = json.loads(output.getvalue())
            self.assertTrue(result["observed_config_pass"])
            self.assertEqual(result["pass_scope"], "observed_turn_configuration_and_wiring")
            self.assertEqual(result["request_tier_status"], "UNVERIFIED")

    def test_legacy_or_configured_rows_do_not_establish_request_cost(self) -> None:
        weights = {"fixture-model": {"input": 2, "output": 10}}
        for fields in ({"service_tier": "fast"}, {"configured_service_tier": "standard"},
                       {"observed_service_tier": "standard", "service_tier_evidence": "observed_turn"},
                       {"requested_service_tier": "standard"}):
            with self.subTest(fields=fields):
                row = {"model": "fixture-model", "input_tokens": 1_000_000, **fields}
                self.assertIsNone(report.weighted_cost(row, weights))
                self.assertEqual(report.weighted_cost(row, weights, scenario_tier="standard"), 2)
                self.assertEqual(report.weighted_cost(row, weights, scenario_tier="fast"), 4)

    def test_old_report_rows_keep_headings_and_disclose_unclassified_cost(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "metrics.jsonl"
            source.write_text(json.dumps({"timestamp": "2026-01-01T12:00:00Z", "is_root": True,
                "service_tier": "standard", "model": "gpt-6.1-sol", "input_tokens": 1_000_000}) + "\n")
            argv = ["report.py", "--input", str(source), "--since", "2026-01-01T00:00:00Z",
                    "--until", "2026-01-02T00:00:00Z", "--weights", str(Path(__file__).parent / "cost-weights.json")]
            for scenario in ([], ["--scenario-tier", "standard"]):
                output = io.StringIO()
                with patch.object(sys, "argv", argv + scenario), contextlib.redirect_stdout(output):
                    self.assertEqual(report.main(), 0)
                rendered = output.getvalue()
                self.assertIn("API USD換算の推計", rendered)
                self.assertIn("Codex追加クレジット換算の推計", rendered)
                self.assertIn("requested tier              UNVERIFIED", rendered)
                self.assertIn("0/1 turns; request数 UNKNOWN", rendered)
                self.assertIn("Pro等のプラン内利用枠消費    推定不可", rendered)
                if scenario:
                    self.assertIn("standardを全tokenに仮定", rendered)
                    self.assertIn("参考シナリオ小計", rendered)
                else:
                    self.assertIn("料金は未算定", rendered)
                    self.assertNotIn("参考シナリオ小計", rendered)


if __name__ == "__main__":
    unittest.main()
