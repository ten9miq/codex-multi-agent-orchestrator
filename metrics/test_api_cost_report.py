"""CLI integration against synthetic turn metrics, never personal rollout data."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ApiCostReportTests(unittest.TestCase):
    def test_cli_keeps_aggregate_context_unclassified_even_in_explicit_scenarios(self) -> None:
        metrics = Path(__file__).parent
        rows = [
            {
                "timestamp": "2026-10-01T12:00:00Z", "is_root": True,
                "thread_id": "synthetic-root", "turn_id": str(index),
                "model": "gpt-6.1-sol", "service_tier": "standard",
                "observed_service_tier": "standard", "service_tier_evidence": "observed_turn",
                "input_tokens": 200000, "cached_input_tokens": 100000,
                "output_tokens": 10, "reasoning_tokens": 5, "total_tokens": 200010,
                "context_peak_tokens": 900000, "context_window": 1050000,
                "compaction_count": 2,
                # Unknown lookalike fields must not authorize request costing.
                "request_samples": [{"request_id": str(index), "input_tokens": 300000}],
                "request_evidence": "observed_request",
            }
            for index in (1, 2)
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "metrics.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            for tier, cost in ((None, None), ("standard", "0.4202"), ("fast", "0.8404")):
                with self.subTest(tier=tier):
                    args = [sys.executable, str(metrics / "report.py"), "--input", str(path),
                            "--since", "2026-10-01T00:00:00Z", "--until", "2026-10-02T00:00:00Z",
                            "--weights", str(metrics / "cost-weights.json"),
                            "--credit-rates", str(metrics / "codex-credit-rates.json")]
                    if tier:
                        args += ["--scenario-tier", tier]
                    result = subprocess.run(args, text=True, capture_output=True,
                                            encoding="utf-8", timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    rendered = result.stdout
                    self.assertIn("算定可能turn coverage", rendered)
                    self.assertIn("0/2 turns; request数 UNKNOWN", rendered)
                    self.assertIn("短/長文脈の料金未分類        2/2 turns", rendered)
                    self.assertIn("実request adapterは未接続", rendered)
                    self.assertIn("Pro等のプラン内利用枠消費    推定不可", rendered)
                    api_section = rendered.split("■ API USD換算の推計", 1)[1].split("■ Codex追加クレジット", 1)[0]
                    if tier:
                        self.assertIn("速度を仮定した短文脈基準シナリオ", api_section)
                        self.assertIn(cost, api_section)
                    else:
                        self.assertIn("料金は未算定", api_section)
                        self.assertNotIn("参考シナリオ小計", api_section)


if __name__ == "__main__":
    unittest.main()
