"""
governance.py — WOW-PATCH-2026-07-15-PROP-CALIBRATION-EXPOSURE-AND-SLIP-GOVERNANCE

Canonical active-patch registry and governance handshake.

Every prop-scoring run must supply expected_governance_hash.
A mismatch returns RUN_INVALID_GOVERNANCE_MISMATCH (HTTP 409 at route level).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Engine identity
# ---------------------------------------------------------------------------
MASTER_SPEC_VERSION  = "WOW-v16"
ENGINE_CODE_VERSION  = "v16.5"

# ---------------------------------------------------------------------------
# Canonical patch registry
# ---------------------------------------------------------------------------
_PATCH_REGISTRY: list[dict[str, Any]] = [
    {
        "patch_id":    "WOW-CORE-v16",
        "version":     "16.0",
        "effective_at": "2026-06-01",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  0,
        "can_execute": False,
        "description": "WOW v16 Clean Core — base gate engine",
    },
    {
        "patch_id":    "WOW-PATCH-2026-06-27-SHARP-ANCHOR",
        "version":     "1.0",
        "effective_at": "2026-06-27",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  10,
        "can_execute": False,
        "description": "Sharp market anchor + house rules matrix",
    },
    {
        "patch_id":    "WOW-PATCH-2026-07-07-JS-STYLE",
        "version":     "1.0",
        "effective_at": "2026-07-07",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  20,
        "can_execute": False,
        "description": "JS-style conversion + slip governance",
    },
    {
        "patch_id":    "WOW-PATCH-2026-07-10-COMBO-SETTLEMENT",
        "version":     "1.0",
        "effective_at": "2026-07-10",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  30,
        "can_execute": False,
        "description": "Combo & settlement governance (Rules A-G)",
    },
    {
        "patch_id":    "WOW-PATCH-MANDATORY-RECONSTRUCTION-v1.0",
        "version":     "1.0",
        "effective_at": "2026-07-14",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  40,
        "can_execute": False,
        "description": "Mandatory data acquisition and reconstruction",
    },
    {
        "patch_id":    "WOW-PATCH-2026-07-15-PROP-CALIBRATION-EXPOSURE-AND-SLIP-GOVERNANCE",
        "version":     "1.0",
        "effective_at": "2026-07-15",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  50,
        "can_execute": False,
        "description": (
            "Prop calibration, exposure, and slip governance: "
            "settlement-aware market delta; source ceilings; "
            "component/composite mutex; opportunity-state consistency; "
            "duplicate exposure; promo rules; calibration suspension; "
            "Prop Reliability Freeze 2026-07-15 through 2026-07-22"
        ),
        "freeze_start": "2026-07-15",
        "freeze_end":   "2026-07-22",
    },
    {
        "patch_id":    "WOW-PATCH-2026-07-15-LLP-DATA-ACQUISITION-RESILIENCE",
        "version":     "1.0",
        "effective_at": "2026-07-15",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  60,
        "can_execute": False,
        "description": (
            "LLP data acquisition resilience: league-scoped event identity; "
            "provider market aliases (moneyline→h2h); UTC normalization; "
            "league-aware time tolerance; doubleheader detection; "
            "PrizePicks decimal/American disambiguation; "
            "two-book no-vig consensus reconstruction with outlier filtering; "
            "source ceilings by data quality; anti-circular model probability; "
            "contract-stage reporting. "
            "Extends WOW-PATCH-2026-07-15-PROP-CALIBRATION-EXPOSURE-AND-SLIP-GOVERNANCE."
        ),
    },
    {
        "patch_id":    "WOW-PATCH-2026-07-15-PROP-CONFIDENCE-AND-MARKET-LABEL-SEPARATION",
        "version":     "1.0",
        "effective_at": "2026-07-15",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  70,
        "can_execute": False,
        "description": (
            "Prop confidence/market/money/slip decision separation: "
            "analysis_mode (HIT_CONFIDENCE/MARKET_EDGE/SLIP_EV/FULL_APPROVAL); "
            "payout scope — missing payout blocks MONEY_QUALIFIED only, never HIT_CONFIDENCE; "
            "governance degradation — local valid + remote unavailable → DEGRADED "
            "  (research/confidence allowed, money/final_approved blocked); "
            "market evidence labels (MARKET_UNVERIFIED_HOLD/ONE_SIDED_MARKET_SUPPORT/"
            "MARKET_CORROBORATED_HOLD/MARKET_VERIFIED_HOLD); "
            "strict two-sided no-vig; adjacent-line interpolation uncertainty; "
            "confidence labels (FINAL_CONFIDENCE_HIGH/MEDIUM/LOW/UNOBTAINABLE); "
            "probability audit (PROVISIONAL when incomplete); "
            "board-source classification (screenshot → research only); "
            "same-game correlation — narrative alone never blocks individual confidence; "
            "four-decision terminal output: confidence/market/money/slip; "
            "FINAL_CONFIDENCE_HIGH never aliases FINAL_APPROVED."
        ),
    },
    {
        "patch_id":    "WOW-PATCH-2026-07-15-GOVERNANCE-RESILIENCE-AND-ERROR-CONTRACT",
        "version":     "1.0",
        "effective_at": "2026-07-15",
        "expires_at":  None,
        "status":      "ACTIVE",
        "precedence":  80,
        "can_execute": False,
        "description": (
            "Governance resilience and structured error contract: "
            "distinct error codes (GOVERNANCE_UNAVAILABLE / "
            "GOVERNANCE_CACHED_DEGRADED_RUN / GOVERNANCE_MISMATCH / "
            "GOVERNANCE_CONTRACT_INVALID / SCAN_UNAVAILABLE_DEGRADED_RUN); "
            "GOVERNANCE_UNAVAILABLE and GOVERNANCE_MISMATCH are never "
            "interchangeable — unavailable=no comparison made, mismatch="
            "comparison failed; "
            "in-process GovernanceSnapshot cache (default 5-min TTL) allows "
            "degraded research run at MODEL_QUALIFIED_HOLD when live endpoint "
            "is transiently unreachable; "
            "RunGovernancePin pins verified governance identity to run_id at "
            "handshake success — mid-run outages cannot erase already-verified "
            "governance; "
            "GET /wow/engine/health (no external I/O, sub-ms) reports process "
            "health, governance load, snapshot metadata, and DB env state; "
            "make_error_contract() returns retryable/retry_after/stage/label_ceiling "
            "on every failure so callers can distinguish transient from deterministic; "
            "degraded ceiling table: UNAVAILABLE→RESEARCH_INTEREST, "
            "CACHED_DEGRADED→MODEL_QUALIFIED_HOLD, MISMATCH→run_invalid. "
            "Extends all active v16 patches."
        ),
    },
]

# ---------------------------------------------------------------------------
# Hash computation
# ---------------------------------------------------------------------------

def _active_patches() -> list[dict[str, Any]]:
    return [p for p in _PATCH_REGISTRY if p.get("status") == "ACTIVE"]


def compute_governance_hash(patches: list[dict[str, Any]] | None = None) -> str:
    """
    Deterministic SHA-256 hash of (patch_id, version) pairs for all active patches,
    sorted by patch_id, encoded as UTF-8 JSON.
    """
    if patches is None:
        patches = _active_patches()
    fingerprint = sorted(
        [{"patch_id": p["patch_id"], "version": p["version"]} for p in patches],
        key=lambda x: x["patch_id"],
    )
    raw = json.dumps(fingerprint, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


_GOVERNANCE_HASH: str = compute_governance_hash()
_ACTIVE_PATCH_IDS: list[str] = [p["patch_id"] for p in _active_patches()]
# _LOADED_AT is a runtime observability field only — NOT part of the hash.
_LOADED_AT: str = datetime.now(timezone.utc).isoformat()

# Derive effective_at / expires_at from the latest active patch (by precedence).
def _latest_active_patch() -> dict[str, Any]:
    active = _active_patches()
    return max(active, key=lambda p: p.get("precedence", 0)) if active else {}

_LATEST_PATCH = _latest_active_patch()


# ---------------------------------------------------------------------------
# Governance status
# ---------------------------------------------------------------------------

def get_governance_status() -> dict[str, Any]:
    """
    Return the full governance status object for the GET endpoint.

    Required fields (per spec):
      master_spec_version, active_patch_ids, governance_hash,
      engine_code_version, effective_at, expires_at, can_execute
    """
    return {
        "master_spec_version":  MASTER_SPEC_VERSION,
        "engine_code_version":  ENGINE_CODE_VERSION,
        "active_patch_ids":     list(_ACTIVE_PATCH_IDS),
        "governance_hash":      _GOVERNANCE_HASH,
        "loaded_at":            _LOADED_AT,
        "effective_at":         _LATEST_PATCH.get("effective_at"),
        "expires_at":           _LATEST_PATCH.get("expires_at"),
        "patch_count":          len(_active_patches()),
        "patches":              _active_patches(),
        "status":               "ACTIVE",
        "can_execute":          False,
    }


# ---------------------------------------------------------------------------
# Handshake validation
# ---------------------------------------------------------------------------

def validate_handshake(
    expected_hash: str | None,
    expected_patch_ids: list[str] | None = None,
    expected_master_spec_version: str | None = None,
) -> dict[str, Any]:
    """
    Validate the caller's governance expectations against the server's state.

    Returns:
        {
          valid:   bool
          code:    "GOVERNANCE_MATCH" | "RUN_INVALID_GOVERNANCE_MISMATCH"
          detail:  str
          server_hash: str
          expected_hash: str | None
        }
    """
    server_hash = _GOVERNANCE_HASH
    mismatches: list[str] = []

    if expected_hash is not None and expected_hash != server_hash:
        mismatches.append(
            f"governance_hash mismatch: expected={expected_hash[:16]}… "
            f"server={server_hash[:16]}…"
        )

    if expected_master_spec_version is not None:
        if expected_master_spec_version != MASTER_SPEC_VERSION:
            mismatches.append(
                f"master_spec_version mismatch: "
                f"expected={expected_master_spec_version} server={MASTER_SPEC_VERSION}"
            )

    if expected_patch_ids is not None:
        expected_set = set(expected_patch_ids)
        server_set   = set(_ACTIVE_PATCH_IDS)
        missing_from_server = expected_set - server_set
        missing_from_caller = server_set - expected_set
        if missing_from_server:
            mismatches.append(f"patch_ids caller expects but server lacks: {missing_from_server}")
        if missing_from_caller:
            mismatches.append(f"patch_ids server has but caller did not list: {missing_from_caller}")

    if mismatches:
        return {
            "valid":         False,
            "code":          "RUN_INVALID_GOVERNANCE_MISMATCH",
            "can_execute":   False,
            "detail":        "; ".join(mismatches),
            "server_hash":   server_hash,
            "expected_hash": expected_hash,
            "mismatches":    mismatches,
        }

    return {
        "valid":         True,
        "code":          "GOVERNANCE_MATCH",
        "can_execute":   False,
        "detail":        "Governance hash and patch registry match.",
        "server_hash":   server_hash,
        "expected_hash": expected_hash,
        "mismatches":    [],
    }


# ---------------------------------------------------------------------------
# Prop Reliability Freeze helpers
# ---------------------------------------------------------------------------

FREEZE_START = "2026-07-15"
FREEZE_END   = "2026-07-22"


def is_in_prop_reliability_freeze(as_of: str | None = None) -> bool:
    """Return True if as_of (YYYY-MM-DD) falls within the freeze window."""
    try:
        d = as_of or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return FREEZE_START <= d <= FREEZE_END
    except Exception:
        return False
