from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from api_cost import (
    Evidence, RequestUsage, estimate_requests, load_pricing_catalog,
    rollout_cost_coverage, validate_pricing_catalog,
)


def request(request_id: str = "response-1", **changes) -> RequestUsage:
    """Synthetic normalized evidence, not a fabricated Codex rollout schema."""
    evidence = Evidence(request_id, "synthetic-fixture:response", "request", "response")
    usage = RequestUsage(
        request_id=request_id, model="gpt-6.1-sol", service_tier="standard",
        input_tokens=100, cached_input_tokens=20, cache_write_tokens=30,
        output_tokens=10, reasoning_tokens=5,
        usage_evidence=evidence, model_evidence=evidence, tier_evidence=evidence,
    )
    return replace(usage, **changes)


class ApiCostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_pricing_catalog()

    def estimate(self, *usages: RequestUsage):
        return estimate_requests(usages, self.catalog)

    def test_threshold_below_equal_above_is_per_request_input(self) -> None:
        for count, band, input_rate, output_rate in (
            (271999, "short", 2, 10), (272000, "short", 2, 10),
            (272001, "long", 4, 15),
        ):
            with self.subTest(count=count):
                result = self.estimate(request(input_tokens=count, cached_input_tokens=0, cache_write_tokens=0))
                item = result["requests"][0]
                self.assertEqual(item["classification"], band)
                self.assertAlmostEqual(item["usd"], (count * input_rate + 10 * output_rate) / 1e6)
                self.assertEqual(item["pricing_version"], self.catalog["version"])
                self.assertFalse(item["is_invoice"])

    def test_cached_tokens_still_count_for_threshold_and_get_long_cache_rate(self) -> None:
        item = self.estimate(request(input_tokens=300000, cached_input_tokens=280000,
                                     cache_write_tokens=10000))["requests"][0]
        self.assertEqual(item["classification"], "long")
        # 10K ordinary at $4 + 280K read at $.20 + 10K write at $5.
        self.assertAlmostEqual(item["usd"], (10000 * 4 + 280000 * .20 + 10000 * 5 + 10 * 15) / 1e6)
        self.assertAlmostEqual(item["components_usd"]["cache_write"], .05)

    def test_cache_write_is_partition_not_an_additive_input_charge(self) -> None:
        item = self.estimate(request(input_tokens=1000, cached_input_tokens=200,
                                     cache_write_tokens=800, output_tokens=0,
                                     reasoning_tokens=0))["requests"][0]
        self.assertEqual(item["components_usd"]["input"], 0)
        self.assertAlmostEqual(item["usd"], (200 * .10 + 800 * 2.50) / 1e6)

    def test_two_short_requests_are_not_long_when_cumulative_input_is_long(self) -> None:
        result = self.estimate(request("a", input_tokens=200000), request("b", input_tokens=200000))
        self.assertEqual(result["short_requests"], 2)
        self.assertEqual(result["long_requests"], 0)
        self.assertEqual(result["classified_requests"], 2)

    def test_output_does_not_trigger_long_context_and_reasoning_is_not_added(self) -> None:
        a = request(input_tokens=260000, output_tokens=100000, reasoning_tokens=99999)
        b = replace(a, reasoning_tokens=0)
        item = self.estimate(a)["requests"][0]
        self.assertEqual(item["classification"], "short")
        self.assertEqual(item["usd"], self.estimate(b)["requests"][0]["usd"])
        self.assertEqual(item["components_usd"]["output"], 1)

    def test_mixed_model_and_tier_use_each_request_without_inheritance(self) -> None:
        records = [
            request("sol", model="gpt-6.1-sol", service_tier="default"),
            request("luna", model="gpt-6-luna", service_tier="priority"),
            request("astra", model="gpt-6-astra", service_tier="standard", input_tokens=300000),
            request("missing-tier", service_tier=None),
            request("missing-model", model=None),
        ]
        result = self.estimate(*records)
        self.assertEqual(result["classified_requests"], 3)
        self.assertEqual(result["long_requests"], 1)
        self.assertEqual(result["unclassified_observations"], 2)
        self.assertFalse(result["all_identified_requests_classified"])
        self.assertEqual(result["requests"][1]["service_tier"], "fast")
        self.assertAlmostEqual(result["requests"][1]["usd"], (50*.2 + 20*.02 + 30*.25 + 10*1) / 1e6)
        self.assertAlmostEqual(result["classified_usd_subtotal"], sum(x["usd"] or 0 for x in result["requests"]))

    def test_unknown_models_and_tiers_are_not_alias_or_default_inferred(self) -> None:
        for change in ({"model": "gpt-6-sol"}, {"model": "gpt-6.1-sol-unverified-snapshot"},
                       {"model": "gpt-5.6-terra"}, {"service_tier": "auto"},
                       {"service_tier": "flex"}, {"service_tier": "ultrafast"}):
            with self.subTest(change=change):
                result = self.estimate(request(**change))
                self.assertEqual(result["unclassified_observations"], 1)
                self.assertIsNone(result["classified_usd_subtotal"])

    def test_duplicate_usage_is_counted_once_but_distinct_request_is_not_deduplicated(self) -> None:
        a = request("a")
        b = request("b")
        result = self.estimate(a, a, b, a)
        self.assertEqual(result["observed_records"], 4)
        self.assertEqual(result["identified_requests"], 2)
        self.assertEqual(result["duplicate_records_ignored"], 2)
        self.assertEqual(result["classified_requests"], 2)
        self.assertAlmostEqual(result["classified_usd_subtotal"], 2 * self.estimate(a)["classified_usd_subtotal"])

    def test_conflicting_duplicate_usage_model_or_tier_is_unclassified(self) -> None:
        a = request("a")
        for change in ({"input_tokens": 200}, {"input_tokens": 100.0},
                       {"model": "gpt-6-luna"}, {"service_tier": "fast"}):
            with self.subTest(change=change):
                result = self.estimate(a, replace(a, **change))
                self.assertIsNone(result["classified_usd_subtotal"])
                self.assertEqual(result["requests"][0]["reason_codes"], ["conflicting_request_observations"])

    def test_compaction_does_not_reset_duplicate_identity_or_price_a_snapshot(self) -> None:
        a = request("a")
        compacted = request("compaction", usage_evidence=Evidence(
            "compaction", "synthetic-fixture:compacted", "compaction", "event"))
        result = self.estimate(a, compacted, a)
        self.assertEqual(result["duplicate_records_ignored"], 1)
        self.assertEqual(result["classified_requests"], 1)
        self.assertEqual(result["unclassified_observations"], 1)
        self.assertEqual(result["classified_usd_subtotal"], self.estimate(a)["classified_usd_subtotal"])

    def test_missing_cache_write_or_cache_read_is_not_zero(self) -> None:
        for change in ({"cache_write_tokens": None}, {"cached_input_tokens": None}):
            with self.subTest(change=change):
                item = self.estimate(request(**change))["requests"][0]
                self.assertEqual(item["classification"], "unclassified")
                self.assertIsNone(item["usd"])

    def test_incomplete_or_nonrequest_evidence_cannot_be_promoted(self) -> None:
        cases = [Evidence(), Evidence("other-request", "fixture", "request", "response"),
                 Evidence("response-1", None, "request", "response"),
                 Evidence("response-1", "turn_context", "turn", "response"),
                 Evidence("response-1", "total_token_usage", "session", "response"),
                 Evidence("response-1", "config.toml", "request", "configured"),
                 Evidence("response-1", "request-options", "request", "requested")]
        for field in ("usage_evidence", "model_evidence", "tier_evidence"):
            for evidence in cases:
                with self.subTest(field=field, evidence=evidence):
                    result = self.estimate(request(**{field: evidence}))
                    self.assertIsNone(result["classified_usd_subtotal"])
                    self.assertIn(f"{field}_insufficient", result["requests"][0]["reason_codes"])

    def test_invalid_counts_are_not_clamped_coerced_or_silently_priced(self) -> None:
        cases = [{"input_tokens": value} for value in (-1, True, "100", 1.5, float("nan"), None)]
        cases += [{"cached_input_tokens": 90}, {"cache_write_tokens": 100},
                  {"reasoning_tokens": 11}, {"reasoning_tokens": -1}]
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertIsNone(self.estimate(request(**changes))["classified_usd_subtotal"])

    def test_unknown_request_identity_does_not_claim_request_count_or_zero_cost(self) -> None:
        result = self.estimate(request(request_id=""))
        self.assertEqual(result["identified_requests"], 0)
        self.assertEqual(result["unidentified_records"], 1)
        self.assertIsNone(result["classified_usd_subtotal"])
        self.assertFalse(result["all_identified_requests_classified"])

    def test_empty_observations_are_not_complete_or_free_usage(self) -> None:
        result = self.estimate()
        self.assertIsNone(result["classified_usd_subtotal"])
        self.assertFalse(result["all_identified_requests_classified"])

    def test_raw_rollout_dicts_are_not_accepted_as_request_records(self) -> None:
        with self.assertRaises(TypeError):
            self.estimate({"last_token_usage": {"input_tokens": 300000}})

    def test_existing_turns_are_unclassified_regardless_of_window_total_or_tier(self) -> None:
        rows = [{"input_tokens": 900000, "context_window": 1050000,
                 "context_peak_tokens": 800000, "model": "gpt-6.1-sol",
                 "observed_service_tier": "standard", "service_tier_evidence": "observed_turn",
                 "last_token_usage": {"total_tokens": 500000},
                 "request_samples": [{"input_tokens": 300000}]}]
        before = copy.deepcopy(rows)
        self.assertEqual(rollout_cost_coverage(rows), {
            "total_rows": 1, "classified_rows": 0, "unclassified_rows": 1,
            "unknown_request_count": True, "reason": "request_attribution_unavailable",
        })
        self.assertEqual(rows, before)

    def test_catalog_is_versioned_sourced_and_does_not_encode_pro_quota(self) -> None:
        self.assertEqual(self.catalog["as_of"], "2026-10-03")
        self.assertEqual(self.catalog["scope"], "openai_api_text_tokens")
        self.assertIn("chatgpt_pro_quota", self.catalog["exclusions"])
        self.assertEqual(set(self.catalog["models"]), {"gpt-6-luna", "gpt-6.1-sol", "gpt-6-astra"})
        for model, rule in self.catalog["models"].items():
            self.assertEqual(rule["source"], f"https://developers.openai.com/api/docs/models/{model}")
            self.assertEqual(rule["long_context"]["threshold_tokens"], 272000)
            for key, value in rule["tiers"]["standard"]["short"].items():
                self.assertEqual(rule["tiers"]["fast"]["short"][key], value * 2)
        self.assertNotIn("pro_quota", self.estimate(request()))

    def test_malformed_catalog_is_rejected_instead_of_defaulting(self) -> None:
        cases = []
        for key in ("schema_version", "version", "as_of", "source", "models", "scope", "unit"):
            data = copy.deepcopy(self.catalog)
            del data[key]
            cases.append(data)
        for value in (None, -1, True, "NaN", "Infinity"):
            data = copy.deepcopy(self.catalog)
            data["models"]["gpt-6.1-sol"]["tiers"]["standard"]["short"]["cached_input"] = value
            cases.append(data)
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_pricing_catalog(data)

    def test_custom_model_rule_is_read_from_catalog_not_global_threshold(self) -> None:
        data = copy.deepcopy(self.catalog)
        data["models"]["gpt-6-luna"]["long_context"]["threshold_tokens"] = 500000
        usage = request(model="gpt-6-luna", input_tokens=300000)
        self.assertEqual(estimate_requests([usage], data)["short_requests"], 1)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "prices.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(load_pricing_catalog(path), data)


if __name__ == "__main__":
    unittest.main()
