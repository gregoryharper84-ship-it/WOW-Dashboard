"""
gate_engine/balldontlie/reconciliation.py
WOW-PATCH-2026-08-08-BALLDONTLIE-TRUSTED-STATS

Cross-source reconciliation for BallDontLie data.

Rules
-----
1. BDL corroborates an existing enrichment value → record CORROBORATED
2. BDL materially disagrees with a higher-priority source → SOURCE_CONFLICT
   (higher-priority source wins; do not silently overwrite or average)
3. BDL is the only source → RETRIEVED; use with provenance tracking
4. L5/L10 construction must use chronological game records, not season averages
5. BALLDONTLIE lineup data cannot override a stronger official contradiction

Material discrepancy threshold: > BDL_CONFLICT_THRESHOLD (15%) relative difference.

Source precedence (highest wins):
  official_feed / official_gamelog  > balldontlie_api > B-grade stat sites

can_execute=False unconditional.
"""
from __future__ import annotations

from typing import Any

can_execute: bool = False  # UNCONDITIONAL

from gate_engine.balldontlie.types import (
    BDLStatus,
    BDL_CONFLICT_THRESHOLD,
    BDLProvenance,
)

# Source type precedence (higher = wins)
_SOURCE_PRECEDENCE: dict[str, int] = {
    "official_feed":         10,
    "official_gamelog":      10,
    "box_score":             9,
    "espn_api":              8,
    "balldontlie_api":       7,
    "statmuse":              5,
    "basketball_reference":  5,
    "bbref":                 5,
    "her_hoop_stats":        5,
    "espn_blurb":            3,
    "article":               2,
    "screenshot":            1,
}

_DEFAULT_PRECEDENCE = 4  # unknown source types


def _precedence(source_type: str) -> int:
    return _SOURCE_PRECEDENCE.get(source_type, _DEFAULT_PRECEDENCE)


def _relative_diff(a: float, b: float) -> float:
    denom = max(abs(a), abs(b), 1e-6)
    return abs(a - b) / denom


# ---------------------------------------------------------------------------
# Per-value reconciliation
# ---------------------------------------------------------------------------

def reconcile_value(
    bdl_value:          float,
    existing_value:     float | None,
    existing_source:    str,
    field_name:         str,
    provenance:         BDLProvenance,
) -> tuple[float, str, BDLProvenance]:
    """
    Reconcile a BDL-derived float value against an existing enrichment value.

    Returns (winner_value, reconciliation_status, updated_provenance).

    reconciliation_status:
      "CORROBORATED"    — values agree (diff ≤ threshold)
      "SOURCE_CONFLICT" — material discrepancy; higher-priority source wins
      "RETRIEVED"       — no existing value; BDL is the only source
      "BDL_LOWER_PREC"  — existing source has higher precedence; BDL annotated only
    """
    if existing_value is None:
        prov = _copy_prov(provenance, conflict_status=BDLStatus.OK,
                         note=f"{field_name}:bdl_only")
        return bdl_value, "RETRIEVED", prov

    diff = _relative_diff(bdl_value, existing_value)
    existing_prec = _precedence(existing_source)
    bdl_prec      = _precedence("balldontlie_api")

    if diff <= BDL_CONFLICT_THRESHOLD:
        # Values agree — corroboration
        prov = _copy_prov(provenance, conflict_status=BDLStatus.CORROBORATED,
                         note=f"{field_name}:corroborated:diff={diff:.3f}")
        return existing_value, "CORROBORATED", prov

    # Material discrepancy — higher-priority source wins
    if existing_prec > bdl_prec:
        # Official/trusted-higher source wins; BDL annotated as conflict
        prov = _copy_prov(provenance, conflict_status=BDLStatus.SOURCE_CONFLICT,
                         note=f"{field_name}:SOURCE_CONFLICT:bdl={bdl_value:.3f}"
                              f":existing={existing_value:.3f}:diff={diff:.3f}"
                              f":winner={existing_source}")
        return existing_value, "SOURCE_CONFLICT", prov

    if bdl_prec > existing_prec:
        # BDL has higher precedence (e.g. BDL > espn_blurb)
        prov = _copy_prov(provenance, conflict_status=BDLStatus.SOURCE_CONFLICT,
                         note=f"{field_name}:SOURCE_CONFLICT:bdl_wins:diff={diff:.3f}"
                              f":displaced={existing_source}")
        return bdl_value, "SOURCE_CONFLICT", prov

    # Equal precedence — keep existing (BDL is secondary annotated)
    prov = _copy_prov(provenance, conflict_status=BDLStatus.SOURCE_CONFLICT,
                     note=f"{field_name}:SOURCE_CONFLICT_EQUAL_PREC:"
                          f"bdl={bdl_value:.3f}:existing={existing_value:.3f}")
    return existing_value, "SOURCE_CONFLICT", prov


def _copy_prov(prov: BDLProvenance, conflict_status: str, note: str) -> BDLProvenance:
    from dataclasses import replace
    notes = list(prov.acquisition_notes) + [note]
    return BDLProvenance(
        source             = prov.source,
        source_type        = prov.source_type,
        source_grade       = prov.source_grade,
        endpoint           = prov.endpoint,
        sport              = prov.sport,
        player_id          = prov.player_id,
        player_name        = prov.player_name,
        game_id            = prov.game_id,
        team_id            = prov.team_id,
        retrieved_at       = prov.retrieved_at,
        effective_date     = prov.effective_date,
        freshness_hours    = prov.freshness_hours,
        bdl_tier_detected  = prov.bdl_tier_detected,
        null_fields        = list(prov.null_fields),
        conflict_status    = conflict_status,
        endpoint_available = prov.endpoint_available,
        acquisition_status = prov.acquisition_status,
        acquisition_notes  = notes,
    )


# ---------------------------------------------------------------------------
# Game-log reconciliation
# ---------------------------------------------------------------------------

def reconcile_game_log(
    bdl_values:       list[float],
    existing_values:  list[float] | None,
    existing_source:  str,
    stat_key:         str,
) -> tuple[list[float], str, list[str]]:
    """
    Reconcile a BDL-derived game log against an existing enrichment game_log.

    Returns (winner_values, status, conflict_notes).

    Uses element-wise mean comparison for overall agreement.
    If overall diff > threshold → SOURCE_CONFLICT (existing higher-priority wins).
    """
    notes: list[str] = []

    if not bdl_values:
        return existing_values or [], "BDL_EMPTY", notes

    if not existing_values:
        notes.append(f"{stat_key}:bdl_game_log_only:{len(bdl_values)}_games")
        return bdl_values, "RETRIEVED", notes

    # Compare means
    n = min(len(bdl_values), len(existing_values))
    bdl_mean = sum(bdl_values[:n]) / n if n else 0.0
    ext_mean = sum(existing_values[:n]) / n if n else 0.0
    diff     = _relative_diff(bdl_mean, ext_mean) if ext_mean or bdl_mean else 0.0

    existing_prec = _precedence(existing_source)
    bdl_prec      = _precedence("balldontlie_api")

    if diff <= BDL_CONFLICT_THRESHOLD:
        notes.append(f"{stat_key}:game_log_corroborated:mean_diff={diff:.3f}")
        return existing_values, "CORROBORATED", notes

    # Material discrepancy
    notes.append(
        f"{stat_key}:GAME_LOG_SOURCE_CONFLICT:"
        f"bdl_mean={bdl_mean:.2f}:ext_mean={ext_mean:.2f}:diff={diff:.2f}"
    )
    if existing_prec >= bdl_prec:
        return existing_values, "SOURCE_CONFLICT", notes
    return bdl_values, "SOURCE_CONFLICT", notes


# ---------------------------------------------------------------------------
# Lineup reconciliation
# ---------------------------------------------------------------------------

def reconcile_lineup(
    bdl_lineup:        list[dict],
    official_lineup:   list[dict] | None,
    official_source:   str | None,
) -> tuple[list[dict], str, list[str]]:
    """
    BDL lineup data CANNOT override a stronger official contradiction.

    If official lineup data exists → always use it (BDL annotated as secondary).
    If no official data → use BDL with RETRIEVED status and provenance note.
    """
    notes: list[str] = []

    if official_lineup is not None:
        notes.append(
            f"bdl_lineup_secondary:official_source={official_source}"
            f":bdl_annotated_not_controlling"
        )
        return official_lineup, "OFFICIAL_CONTROLS", notes

    notes.append(
        "bdl_lineup_only:must_reconcile_against_official_before_model_entry"
    )
    return bdl_lineup, "RETRIEVED", notes


# ---------------------------------------------------------------------------
# Enrichment-level reconciliation
# ---------------------------------------------------------------------------

def reconcile_enrichment_game_log(
    enrichment:      dict[str, Any],
    bdl_values:      list[float],
    stat_key:        str,
    bdl_provenance:  BDLProvenance,
) -> dict[str, Any]:
    """
    Top-level reconciliation of a BDL game log against what's already in enrichment.

    Updates enrichment in place only when BDL wins or corroborates.
    Adds 'bdl_game_log_provenance' and 'bdl_reconciliation_status' keys.
    Always surfaces SOURCE_CONFLICT explicitly — never silently averages.
    Returns updated enrichment.
    """
    existing = enrichment.get("game_log") or []
    existing_source = enrichment.get("game_log_source") or "unknown"

    winner, status, notes = reconcile_game_log(
        bdl_values=bdl_values,
        existing_values=existing,
        existing_source=existing_source,
        stat_key=stat_key,
    )

    enrichment["bdl_game_log_provenance"]  = bdl_provenance.to_dict()
    enrichment["bdl_reconciliation_status"] = status
    enrichment["bdl_reconciliation_notes"]  = notes

    if status in ("RETRIEVED",):
        # BDL is the only source — use it
        enrichment["game_log"]        = winner
        enrichment["game_log_source"] = BDL_SOURCE_NAME
    elif status == "CORROBORATED":
        # Existing values corroborated — keep existing, note corroboration
        pass   # existing game_log unchanged
    elif status == "SOURCE_CONFLICT":
        # Conflict surfaced — keep the winner but mark conflict
        enrichment["game_log"]                  = winner
        enrichment["game_log_source_conflict"]  = True
        enrichment["game_log_conflict_detail"]  = " | ".join(notes)

    return enrichment
