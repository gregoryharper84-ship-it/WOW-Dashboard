"""
Acceptance tests for compute_stake_confidence (LLP v16.1A patch).

Canonical sample payloads for SMALL / MEDIUM / HIGH / BLOCKED are inline
below each test as comments so downstream agents can reference them.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from gate_engine.llp_governance import (
    compute_stake_confidence,
    LLPLabel,
    _MEDIUM_PROB_MIN, _MEDIUM_EDGE_MIN, _MEDIUM_LEDGER_MIN,
    _HIGH_PROB_MIN, _HIGH_EDGE_MIN, _HIGH_LEDGER_MIN,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _clean_context(**kwargs):
    """Return a fully-populated context_flags dict with sane defaults."""
    return {
        "final_lock":             True,
        "exposure_ok":            True,
        "ledger_candidate_count": 150,
        "clv_graduation_ok":      True,
        "timestamp_present":      True,
        **kwargs,
    }


# ---------------------------------------------------------------------------
# Test 1 — LLP_PLAYABLE + any high prob → stake_tier SMALL (label ceiling)
# ---------------------------------------------------------------------------
def test_playable_label_always_caps_at_small():
    """
    Even when model_probability and edge would qualify for HIGH, the
    PLAYABLE label hard-caps stake_tier at SMALL.

    Sample SMALL payload (from PLAYABLE):
      {
        "final_label": "LLP_PLAYABLE",
        "confidence_tier": "VERY_HIGH",
        "stake_tier": "SMALL",
        "recommended_stake": 0.5,
        "max_allowed_stake": 0.5,
        "stake_cap_reason": "PLAYABLE_LABEL_CEILING",
        "big_stake_status": "CAPPED_BY_LABEL",
        "big_stake_blockers": ["PLAYABLE_CANNOT_EXCEED_SMALL"]
      }
    """
    result = compute_stake_confidence(
        final_label="LLP_PLAYABLE",
        model_probability=0.65,
        edge=0.08,
        context_flags=_clean_context(ledger_candidate_count=200),
    )
    assert result["stake_tier"] == "SMALL", result
    assert result["big_stake_status"] == "CAPPED_BY_LABEL"
    assert "PLAYABLE_CANNOT_EXCEED_SMALL" in result["big_stake_blockers"]
    assert result["confidence_tier"] == "VERY_HIGH"


# ---------------------------------------------------------------------------
# Test 2 — LLP_APPROVED, prob=0.59, edge=0.03, final_lock → max MEDIUM not HIGH
# ---------------------------------------------------------------------------
def test_approved_medium_not_high_when_prob_below_high_min():
    """
    prob=0.59 clears the MEDIUM floor (0.58) but not HIGH (0.61).
    edge=0.03 clears MEDIUM floor (0.025) but not HIGH (0.05).
    Result: MEDIUM — never HIGH.

    Sample MEDIUM payload:
      {
        "final_label": "LLP_APPROVED",
        "confidence_tier": "HIGH",
        "stake_tier": "MEDIUM",
        "recommended_stake": 0.75,
        "max_allowed_stake": 1.0,
        "stake_cap_reason": "APPROVED_MEDIUM_TIER",
        "big_stake_status": "APPROVED",
        "big_stake_blockers": [...]   # HIGH-specific blockers listed here
      }
    """
    result = compute_stake_confidence(
        final_label="LLP_APPROVED",
        model_probability=0.59,
        edge=0.03,
        context_flags=_clean_context(),
    )
    assert result["stake_tier"] == "MEDIUM", result
    assert result["big_stake_status"] == "APPROVED"
    assert result["confidence_tier"] == "HIGH"
    assert result["max_allowed_stake"] == 1.0
    any_high_prob_blocker = any(
        "PROB_BELOW_HIGH" in b for b in result["big_stake_blockers"]
    )
    assert any_high_prob_blocker, result["big_stake_blockers"]


# ---------------------------------------------------------------------------
# Test 3 — LLP_APPROVED, prob=0.61, edge=0.06, full context → HIGH
# ---------------------------------------------------------------------------
def test_approved_high_when_all_gates_pass():
    """
    Every HIGH gate passes → stake_tier HIGH.

    Sample HIGH payload:
      {
        "final_label": "LLP_APPROVED",
        "confidence_tier": "VERY_HIGH",
        "stake_tier": "HIGH",
        "recommended_stake": 1.25,
        "max_allowed_stake": 1.5,
        "stake_cap_reason": "APPROVED_HIGH_TIER",
        "big_stake_status": "APPROVED",
        "big_stake_blockers": []
      }
    """
    result = compute_stake_confidence(
        final_label="LLP_APPROVED",
        model_probability=0.61,
        edge=0.06,
        context_flags=_clean_context(
            final_lock=True,
            exposure_ok=True,
            ledger_candidate_count=100,
            clv_graduation_ok=True,
            timestamp_present=True,
        ),
    )
    assert result["stake_tier"] == "HIGH", result
    assert result["big_stake_status"] == "APPROVED"
    assert result["big_stake_blockers"] == []
    assert result["recommended_stake"] == 1.25
    assert result["max_allowed_stake"] == 1.50


# ---------------------------------------------------------------------------
# Test 4 — Missing edge → PASS (non-APPROVED) or SMALL (APPROVED), never MEDIUM/HIGH
# ---------------------------------------------------------------------------
def test_missing_edge_blocks_medium_and_high():
    """
    When edge is None:
    - Non-APPROVED labels: stake_tier is PASS (label ceiling already applies)
    - APPROVED label: stake_tier is SMALL (MEDIUM/HIGH require edge)
    Never MEDIUM or HIGH.

    Sample BLOCKED payload (APPROVED + no edge):
      {
        "stake_tier": "SMALL",
        "big_stake_status": "BLOCKED",
        "big_stake_blockers": ["NO_EDGE_VALUE", ...]
      }
    """
    for label in ("LLP_WATCH", "LLP_SCOUT", "LLP_REJECT", "LLP_CUT"):
        r = compute_stake_confidence(
            final_label=label,
            model_probability=0.60,
            edge=None,
            context_flags=_clean_context(),
        )
        assert r["stake_tier"] == "PASS", f"label={label}: {r}"

    r_approved = compute_stake_confidence(
        final_label="LLP_APPROVED",
        model_probability=0.62,
        edge=None,
        context_flags=_clean_context(),
    )
    assert r_approved["stake_tier"] == "SMALL", r_approved
    assert r_approved["big_stake_status"] == "BLOCKED"
    assert "NO_EDGE_VALUE" in r_approved["big_stake_blockers"], r_approved["big_stake_blockers"]
    assert r_approved["stake_tier"] not in ("MEDIUM", "HIGH")


# ---------------------------------------------------------------------------
# Test 5 — Missing timestamp → MEDIUM/HIGH blocked
# ---------------------------------------------------------------------------
def test_missing_timestamp_blocks_medium_and_high():
    """
    timestamp_present=False blocks both MEDIUM and HIGH, caps at SMALL.

    Sample BLOCKED payload (timestamp missing):
      {
        "stake_tier": "SMALL",
        "big_stake_status": "BLOCKED",
        "big_stake_blockers": ["NO_TIMESTAMP", ...]
      }
    """
    result = compute_stake_confidence(
        final_label="LLP_APPROVED",
        model_probability=0.62,
        edge=0.06,
        context_flags=_clean_context(timestamp_present=False),
    )
    assert result["stake_tier"] == "SMALL", result
    assert result["big_stake_status"] == "BLOCKED"
    assert "NO_TIMESTAMP" in result["big_stake_blockers"], result["big_stake_blockers"]


# ---------------------------------------------------------------------------
# Test 6 — Exposure breach → MEDIUM/HIGH blocked
# ---------------------------------------------------------------------------
def test_exposure_breach_blocks_medium_and_high():
    """
    exposure_ok=False → MEDIUM and HIGH are blocked regardless of prob/edge.
    """
    result = compute_stake_confidence(
        final_label="LLP_APPROVED",
        model_probability=0.65,
        edge=0.07,
        context_flags=_clean_context(exposure_ok=False),
    )
    assert result["stake_tier"] == "SMALL", result
    assert result["big_stake_status"] == "BLOCKED"
    assert "EXPOSURE_BREACH" in result["big_stake_blockers"], result["big_stake_blockers"]


# ---------------------------------------------------------------------------
# Test 7 — Output never introduces new LLP label values
# ---------------------------------------------------------------------------
def test_output_uses_only_valid_llp_labels():
    """
    compute_stake_confidence adds stake/confidence fields but never
    adds or modifies final_label — only the six canonical LLP labels exist.
    """
    valid_labels = {l.value for l in LLPLabel}
    cases = [
        ("LLP_APPROVED",  0.62, 0.06, _clean_context()),
        ("LLP_PLAYABLE",  0.57, 0.03, _clean_context()),
        ("LLP_WATCH",     0.54, 0.01, _clean_context()),
        ("LLP_SCOUT",     0.52, None, _clean_context()),
        ("LLP_REJECT",    0.50, None, _clean_context()),
        ("LLP_CUT",       0.48, None, _clean_context()),
    ]
    for label, prob, edge, ctx in cases:
        result = compute_stake_confidence(label, prob, edge, context_flags=ctx)
        assert "final_label" not in result, (
            f"compute_stake_confidence must not return final_label: {result}"
        )
        for key in ("stake_tier", "confidence_tier", "big_stake_status"):
            val = result[key]
            assert val not in valid_labels, (
                f"Field {key}={val!r} leaked an LLP label into stake output"
            )
        for blocker in result.get("big_stake_blockers", []):
            for vl in valid_labels:
                assert vl != blocker, (
                    f"big_stake_blockers contains bare LLP label: {blocker}"
                )
