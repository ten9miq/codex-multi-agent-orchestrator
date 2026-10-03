#!/usr/bin/env python3
"""Evidence-gated API text-token estimates; deliberately not a rollout adapter.

Only a validated adapter may create RequestUsage from an actual response. A
turn's cumulative tokens, last context snapshot, configured window, requested
model/tier, or compaction event is not request-level billing evidence. This
module does not infer or accept an undocumented rollout request schema.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable


DEFAULT_PRICING_PATH = Path(__file__).with_name("api-pricing-2026-10-03.json")
TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens")
RATE_FIELDS = ("input", "cached_input", "cache_write", "output")
TIERS = {"default": "standard", "standard": "standard", "priority": "fast", "fast": "fast"}


@dataclass(frozen=True)
class Evidence:
    """Source reference tied to the same real request, not requested settings.

    ``scope='request', origin='response'`` attests that a reviewed adapter
    extracted this value from actual response evidence. These are this module's
    normalized interface, NOT names of fields observed in Codex rollout logs.
    """

    request_id: str | None = None
    source: str | None = None
    scope: str = "unknown"
    origin: str = "unknown"


@dataclass(frozen=True)
class RequestUsage:
    """One completed usage observation with a stable, dataset-unique request ID.

    input_tokens includes cache reads and cache writes. output_tokens includes
    reasoning. Explicit zero cache writes is required; missing is not zero.
    Adapters must namespace request IDs when a source does not guarantee global
    uniqueness and must never use a token total as the request identity.
    """

    request_id: str | None = None
    model: str | None = None
    service_tier: str | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    usage_evidence: Evidence = Evidence()
    model_evidence: Evidence = Evidence()
    tier_evidence: Evidence = Evidence()


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not a rate")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid rate") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("rates must be finite and nonnegative")
    return result


def load_pricing_catalog(path: Path = DEFAULT_PRICING_PATH) -> dict[str, Any]:
    """Load and validate a versioned local snapshot; never fetch or guess rates."""
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_pricing_catalog(data)
    return data


def validate_pricing_catalog(data: Any) -> None:
    if (not isinstance(data, dict) or type(data.get("schema_version")) is not int
            or data["schema_version"] != 1):
        raise ValueError("unsupported API pricing catalog")
    if data.get("unit") != "usd_per_million_tokens" or data.get("scope") != "openai_api_text_tokens":
        raise ValueError("not an API text-token catalog")
    for key in ("version", "as_of", "source"):
        if not _text(data.get(key)):
            raise ValueError(f"missing pricing {key}")
    models = data.get("models")
    if not isinstance(models, dict) or not models:
        raise ValueError("missing model-specific prices")
    for model, rule in models.items():
        if not _text(model) or not isinstance(rule, dict) or not _text(rule.get("source")):
            raise ValueError("missing model-specific source")
        threshold = rule.get("long_context")
        if not isinstance(threshold, dict) or not _count(threshold.get("threshold_tokens")):
            raise ValueError("missing model-specific threshold")
        if (threshold.get("measure") != "request_input_including_cached_and_cache_write"
                or threshold.get("comparison") != "gt"
                or threshold.get("applies_to") != "full_request"):
            raise ValueError("unsupported long-context rule")
        tiers = rule.get("tiers")
        if not isinstance(tiers, dict) or not tiers:
            raise ValueError("missing tier-specific prices")
        for tier, prices in tiers.items():
            if tier not in {"standard", "fast"} or not isinstance(prices, dict):
                raise ValueError("unsupported pricing tier")
            for band in ("short", "long"):
                if not isinstance(prices.get(band), dict):
                    raise ValueError("missing context-specific rates")
                for key in RATE_FIELDS:
                    _decimal(prices[band].get(key))


def _unclassified(request_id: str | None, reasons: list[str], catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "classification": "unclassified",
        "usd": None,
        "reason_codes": reasons,
        "pricing_version": catalog["version"],
        "pricing_as_of": catalog["as_of"],
        "is_invoice": False,
    }


def _estimate_request(usage: RequestUsage, catalog: dict[str, Any]) -> dict[str, Any]:
    reasons = []
    if not _text(usage.request_id):
        reasons.append("request_identity_missing")
    for field in ("usage_evidence", "model_evidence", "tier_evidence"):
        evidence = getattr(usage, field)
        if (not isinstance(evidence, Evidence) or evidence.scope != "request"
                or evidence.origin != "response" or not _text(evidence.source)
                or evidence.request_id != usage.request_id or not _text(usage.request_id)):
            reasons.append(f"{field}_insufficient")
    for field in TOKEN_FIELDS:
        if not _count(getattr(usage, field)):
            reasons.append(f"{field}_missing_or_invalid")
    if usage.reasoning_tokens is not None and not _count(usage.reasoning_tokens):
        reasons.append("reasoning_tokens_invalid")
    if all(_count(getattr(usage, key)) for key in TOKEN_FIELDS):
        if usage.cached_input_tokens + usage.cache_write_tokens > usage.input_tokens:
            reasons.append("cache_partition_exceeds_input")
        if _count(usage.reasoning_tokens) and usage.reasoning_tokens > usage.output_tokens:
            reasons.append("reasoning_exceeds_output")
    model = catalog["models"].get(usage.model) if isinstance(usage.model, str) else None
    if model is None:
        reasons.append("model_price_unverified")
    tier = TIERS.get(usage.service_tier) if isinstance(usage.service_tier, str) else None
    if tier is None or (model is not None and tier not in model["tiers"]):
        reasons.append("tier_price_unverified")
    if reasons:
        return _unclassified(usage.request_id, reasons, catalog)

    # The threshold includes cache-read and cache-write tokens. It excludes
    # output/reasoning, prior requests, session totals, and context-window size.
    band = "long" if usage.input_tokens > model["long_context"]["threshold_tokens"] else "short"
    rates = model["tiers"][tier][band]
    counts = {
        "input": usage.input_tokens - usage.cached_input_tokens - usage.cache_write_tokens,
        "cached_input": usage.cached_input_tokens,
        "cache_write": usage.cache_write_tokens,
        "output": usage.output_tokens,
    }
    parts = {key: Decimal(counts[key]) * _decimal(rates[key]) / 1_000_000 for key in RATE_FIELDS}
    return {
        "request_id": usage.request_id,
        "classification": band,
        "estimate_kind": "evidence_based_api_text_token_estimate",
        "usd": float(sum(parts.values())),
        "components_usd": {key: float(value) for key, value in parts.items()},
        "model": usage.model,
        "service_tier": tier,
        "reason_codes": [],
        "pricing_version": catalog["version"],
        "pricing_as_of": catalog["as_of"],
        "pricing_source": model["source"],
        "is_invoice": False,
    }


def _same_observation(left: RequestUsage, right: RequestUsage) -> bool:
    # Python considers True == 1 and 1.0 == 1; malformed duplicate records must
    # not disappear behind an earlier valid integer usage record.
    return left == right and all(
        type(getattr(left, key)) is type(getattr(right, key))
        for key in (*TOKEN_FIELDS, "reasoning_tokens")
    )


def estimate_requests(
    usages: Iterable[RequestUsage], catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify and price independent requests, with fail-closed deduplication.

    Identical records with the same request ID are counted once. Conflicting
    records sharing an ID are all excluded. Distinct IDs with identical counts
    are both counted. No compaction event resets this identity tracking.
    Classified cost is a subtotal only; unclassified usage is NEVER free.
    """
    catalog = load_pricing_catalog() if catalog is None else catalog
    validate_pricing_catalog(catalog)
    groups: dict[str, list[RequestUsage]] = defaultdict(list)
    missing_ids = []
    observed_records = 0
    for usage in usages:
        if not isinstance(usage, RequestUsage):
            raise TypeError("a validated adapter must supply RequestUsage, not raw rollout dictionaries")
        observed_records += 1
        if _text(usage.request_id):
            groups[usage.request_id].append(usage)
        else:
            missing_ids.append(usage)
    results = []
    duplicates = 0
    for request_id, records in groups.items():
        if any(not _same_observation(record, records[0]) for record in records[1:]):
            results.append(_unclassified(request_id, ["conflicting_request_observations"], catalog))
        else:
            duplicates += len(records) - 1
            results.append(_estimate_request(records[0], catalog))
    results.extend(_estimate_request(usage, catalog) for usage in missing_ids)
    counts = Counter(result["classification"] for result in results)
    classified = [result for result in results if result["usd"] is not None]
    subtotal = float(sum(Decimal(str(result["usd"])) for result in classified)) if classified else None
    return {
        "pricing_version": catalog["version"],
        "pricing_as_of": catalog["as_of"],
        "observed_records": observed_records,
        "identified_requests": len(groups),
        "unidentified_records": len(missing_ids),
        "duplicate_records_ignored": duplicates,
        "classified_requests": len(classified),
        "short_requests": counts["short"],
        "long_requests": counts["long"],
        "unclassified_observations": counts["unclassified"],
        "classified_usd_subtotal": subtotal,
        "all_identified_requests_classified": bool(results) and not counts["unclassified"],
        "is_invoice": False,
        "exclusions": list(catalog.get("exclusions", [])),
        "requests": results,
    }


def rollout_cost_coverage(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Honest report hook for the existing turn-only Metrics schema.

    Even supplied lookalike request fields or favorable turn provenance cannot
    replace a verified real-log adapter. No adapter has been validated here.
    Turn counts are known; request counts and request pricing coverage are not.
    """
    total = sum(1 for _ in rows)
    return {
        "total_rows": total,
        "classified_rows": 0,
        "unclassified_rows": total,
        "unknown_request_count": True,
        "reason": "request_attribution_unavailable",
    }
