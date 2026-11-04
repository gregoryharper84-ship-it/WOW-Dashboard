"""Opt-in, post-score LLP annotation of private DAFT research evidence.

This wrapper reads caller-curated private metadata, never connects to Discord,
and never changes scorer inputs, sporting probabilities, bounds, rank, or terminal.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from v17.daft_research_market import (
    CAN_EXECUTE, finite_number, quote_age_seconds, same_contract, same_market,
)

SHADOW_FLAG = "WOW_LLP_DAFT_SHADOW_ENABLED"
PRIVATE_SIGNALS_ENV = "WOW_DAFT_PRIVATE_SIGNALS_PATH"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"


def enabled() -> bool:
    return os.getenv(SHADOW_FLAG, "false").strip().lower() == "true"


def load_private_signals() -> tuple[list[dict[str, Any]], str]:
    """No private Discord data is built into or returned by this adapter."""
    name = os.getenv(PRIVATE_SIGNALS_ENV, "")
    if not name:
        return [], "PRIVATE_FEED_NOT_CONFIGURED"
    p = Path(name)
    try:
        if not p.is_absolute() or p.is_symlink() or not p.is_file():
            return [], "PRIVATE_FEED_UNAVAILABLE"
        # Keep paid source records out of the public checkout even if misconfigured.
        if p.resolve().is_relative_to(Path.cwd().resolve()):
            return [], "PRIVATE_FEED_PATH_INSIDE_SOURCE_TREE"
        if p.stat().st_size > 2_000_000:
            return [], "PRIVATE_FEED_TOO_LARGE"
        content = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return [], "PRIVATE_FEED_UNAVAILABLE"
    if not isinstance(content, list) or not all(isinstance(row, dict) for row in content):
        return [], "PRIVATE_FEED_INVALID"
    return content, "AVAILABLE"


def build_annotation(
    candidate: Mapping[str, Any] | None,
    signals: list[Mapping[str, Any]],
    *,
    as_of: str,
    feed_status: str = "AVAILABLE",
    max_quote_age_seconds: int = 120,
) -> dict[str, Any]:
    """Research annotation only; never generates or replaces numeric model output."""
    candidate_contract = candidate if isinstance(candidate, Mapping) else {}
    examined = []
    for row in signals:
        reference = row.get("contract") if isinstance(row.get("contract"), Mapping) else {}
        if not same_market(candidate_contract, reference):
            continue
        exact = same_contract(candidate_contract, reference)
        candidate_selection = str(candidate_contract.get("selection") or "").casefold()
        reference_selection = str(reference.get("selection") or "").casefold()
        age = quote_age_seconds(row.get("offered_quote_at"), as_of)
        price = finite_number(row.get("offered_decimal_odds"))
        fresh = age is not None and age <= max_quote_age_seconds
        # A different same-market selection is a directional conflict, not a
        # claim of calibrated probability disagreement.
        agreement = ("AGREES" if exact else
                     "DISAGREES" if candidate_selection and reference_selection and
                     candidate_selection != reference_selection else "UNRESOLVED")
        eligible = (row.get("research_eligibility") == "PROSPECTIVE"
                    and row.get("identity_verified") is True
                    and exact and fresh and price is not None and price > 1.0)
        examined.append({
            "exact_contract": exact, "directional_relation": agreement,
            "quote_age_seconds": age, "quote_fresh": fresh,
            "available_price_verified": bool(fresh and price is not None and price > 1),
            "source_market_probability": row.get("source_estimated_probability"),
            "verification_status": row.get("verification_status") or "UNRESOLVED",
            "research_eligible": eligible,
            "closing_line_value_decimal": row.get("verified_comparable_clv"),
        })
    exact_rows = [row for row in examined if row["exact_contract"]]
    most_recent = min(exact_rows, key=lambda r: r["quote_age_seconds"] if r["quote_age_seconds"] is not None else float("inf")) if exact_rows else None
    return {
        "schema_version": "WOW_DAFT_LLP_SHADOW_V1",
        "mode": "RESEARCH_SHADOW_ONLY",
        "external_signal_status": ("MATCHED" if exact_rows else
                                   "UNRESOLVED_CONTRACT" if examined else
                                   "UNAVAILABLE" if feed_status != "AVAILABLE" else "NO_MATCH"),
        "private_feed_status": feed_status,
        "same_market_signal_count": len(examined),
        "exact_contract_signal_count": len(exact_rows),
        "directional_relation": most_recent["directional_relation"] if most_recent else (
            examined[0]["directional_relation"] if examined else "UNKNOWN"),
        "quote_age_seconds": most_recent["quote_age_seconds"] if most_recent else None,
        "price_available": most_recent["available_price_verified"] if most_recent else False,
        "source_market_probability": most_recent["source_market_probability"] if most_recent else None,
        "independent_mechanism_verified": most_recent["verification_status"] == "VERIFIED" if most_recent else False,
        "comparable_clv": most_recent["closing_line_value_decimal"] if most_recent else None,
        "research_eligible": any(r["research_eligible"] for r in exact_rows),
        "eligible_signal_count": sum(1 for r in exact_rows if r["research_eligible"]),
        "research_reasons": ([] if exact_rows else
                             ["EXACT_CONTRACT_NOT_VERIFIED" if examined else "NO_COMPARABLE_FEED_RECORD"]),
        "source_probability_is_not_governed_probability": True,
        "probability_mutated": False, "calibrated_lower_bound_mutated": False,
        "rank_mutated": False, "terminal_mutated": False,
        "terminal_authority": TERMINAL_AUTHORITY, "can_execute": CAN_EXECUTE,
    }


def _contract_from_request(req: Any, result: Mapping[str, Any]) -> Mapping[str, Any] | None:
    # Never fabricate market period, line, selection, or settlement basis.
    value = result.get("canonical_contract")
    if isinstance(value, Mapping):
        return value
    market = getattr(req, "market_input", None)
    if isinstance(market, Mapping):
        contract = market.get("canonical_contract")
        if isinstance(contract, Mapping):
            return contract
    return None


def install_llp_daft_research_shadow(team_event_module: Any) -> bool:
    if not enabled():
        return False
    if getattr(team_event_module, "_v17_llp_daft_research_shadow_installed", False):
        return True
    original = getattr(team_event_module, "score_team_event_request", None)
    if not callable(original):
        return False

    def score_with_shadow(req: Any, *, event_api: Any, canonical_hydration_required: bool = False):
        result = original(req, event_api=event_api,
                          canonical_hydration_required=canonical_hydration_required)
        if not isinstance(result, dict):
            return result
        out = dict(result)
        try:
            signals, feed_status = load_private_signals()
            # Capture time, not current time, is the only acceptable timestamp
            # for prospective records. Current time is only used for quote age.
            from datetime import datetime, timezone
            as_of = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            out["llp_daft_research_shadow"] = build_annotation(
                _contract_from_request(req, result), signals,
                as_of=as_of, feed_status=feed_status,
            )
        except Exception:
            # Optional research must not break or rewrite a certified score.
            out["llp_daft_research_shadow"] = build_annotation(
                None, [], as_of="1970-01-01T00:00:00Z",
                feed_status="RESEARCH_ADAPTER_ERROR",
            )
        out["can_execute"] = False
        return out

    team_event_module.score_team_event_request = score_with_shadow
    team_event_module._v17_llp_daft_research_shadow_original = original
    team_event_module._v17_llp_daft_research_shadow_installed = True
    return True
