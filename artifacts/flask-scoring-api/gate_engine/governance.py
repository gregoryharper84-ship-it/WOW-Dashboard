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
ENGINE_CODE_VERSION  = "v16.2"

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
_LOADED_AT: str = datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Governance status
# ---------------------------------------------------------------------------

def get_governance_status() -> dict[str, Any]:
    """Return the full governance status object for the GET endpoint."""
    return {
        "master_spec_version":  MASTER_SPEC_VERSION,
        "engine_code_version":  ENGINE_CODE_VERSION,
        "active_patch_ids":     list(_ACTIVE_PATCH_IDS),
        "governance_hash":      _GOVERNANCE_HASH,
        "loaded_at":            _LOADED_AT,
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
