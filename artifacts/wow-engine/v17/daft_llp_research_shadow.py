"""Optional, strictly post-score DAFT annotation on the existing LLP scorer.

Does not call Discord, change probability, modify a model input, or invoke
market-price acquisition. Empty private snapshot is an explicit unavailable
research signal, not a missing model capability.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from v17.daft_external_research import llp_research_annotation

CAN_EXECUTE = False
SHADOW_FLAG = "WOW_DAFT_LLP_SHADOW_ENABLED"
PRIVATE_INDEX_ENV = "WOW_DAFT_PRIVATE_RESEARCH_INDEX"


def _loaded_private_rows() -> list[dict[str, Any]]:
    """Load optional sanitized index outside the public repository; fail closed."""
    location = os.getenv(PRIVATE_INDEX_ENV)
    if not location:
        return []
    path = Path(location)
    try:
        if (not path.is_absolute() or path.is_symlink() or not path.is_file()
                or path.stat().st_size > 2_000_000 or path.stat().st_mode & 0o077):
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list) or len(data) > 5000:
            return []
        return [row for row in data if isinstance(row, dict)
                and row.get("prospective_eligible") is True]
    except (OSError, ValueError, TypeError):
        return []


def _request_contract(req: Any, result: Mapping[str, Any], selection: str) -> dict[str, Any]:
    """Only exact contract semantics match; missing fields remain unknown."""
    outcome_space = (
        "HOME_DRAW_AWAY" if str(getattr(req, "settlement_basis", "")).upper().find("3WAY") >= 0
        else "HOME_AWAY" if "OVERTIME" in str(getattr(req, "settlement_basis", "")).upper()
        else None
    )
    return {
        "sport": getattr(req, "sport", None),
        "league": getattr(req, "league", None),
        "official_event_id": getattr(req, "official_event_id", None),
        "market_family": getattr(req, "market_family", None),
        "period": "FULL_GAME" if getattr(req, "settlement_basis", None) else None,
        "selection": selection,
        "exact_line": "NONE",
        "settlement_rule": getattr(req, "settlement_basis", None),
        "outcome_space": outcome_space,
        "direction": selection,
    }


def install_daft_llp_research_shadow(
    team_event_module: Any,
    *,
    observations: Sequence[Mapping[str, Any]] | None = None,
) -> bool:
    """Install once after existing LLP market adapters. No production input changes."""
    if os.getenv(SHADOW_FLAG, "true").strip().lower() != "true":
        return False
    if getattr(team_event_module, "_v17_daft_shadow_installed", False):
        return True
    prior = getattr(team_event_module, "score_team_event_request", None)
    if not callable(prior):
        return False
    cached = list(observations) if observations is not None else _loaded_private_rows()

    def score_with_dafts_shadow(
        req: Any, *, event_api: Any, canonical_hydration_required: bool = False,
    ) -> Any:
        result = prior(
            req, event_api=event_api,
            canonical_hydration_required=canonical_hydration_required,
        )
        if not isinstance(result, dict):
            return result
        result_copy = dict(result)
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        result_copy["daft_external_research_shadow"] = {
            side.lower(): llp_research_annotation(
                _request_contract(req, result, selection),
                cached, observed_at=now,
            )
            for side, selection in (
                ("HOME", str(getattr(req, "home_team", "") or "")),
                ("AWAY", str(getattr(req, "away_team", "") or "")),
            )
        }
        # No status/terminal/probability fields are assigned here. Never
        # downgrade an upstream can_execute=false; V17 owns that flag.
        return result_copy

    team_event_module.score_team_event_request = score_with_dafts_shadow
    team_event_module._v17_daft_shadow_original = prior
    team_event_module._v17_daft_shadow_installed = True
    return True
