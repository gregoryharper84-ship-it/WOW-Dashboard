"""
gate_engine/ml_deduplication.py
WOW-PATCH-2026-07-13 — P0-2: Same-Event Duplicate Entries Count Once

Multiple financial tickets on the same side of the same game are separate
financial exposures but a SINGLE model observation and a SINGLE calibration
observation.

event_key = (league, event_date, away_team, home_team, market_type, selected_side)

Outputs per canonical event:
    financial_entry_count      : int   — total tickets
    model_observation_count    : int   — always 1 per unique event_key
    calibration_observation_count : int — always 1 per unique event_key
    gross_stake                : float — sum of all ticket stakes
    duplicate_exposure         : float — stakes beyond the first ticket
    entries                    : list  — all raw tickets
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


# ---------------------------------------------------------------------------
# event_key helpers
# ---------------------------------------------------------------------------

def build_event_key(entry: dict[str, Any]) -> str:
    """
    Build a deterministic, canonical event key string for an ML pick.

    Normalizes all components to lowercase stripped strings.
    Returns a hex-digest so it can be used as a dict key.
    """
    components = (
        _norm(entry.get("league")),
        _norm(entry.get("event_date")),
        _norm(entry.get("away_team")),
        _norm(entry.get("home_team")),
        _norm(entry.get("market_type") or "ml"),
        _norm(entry.get("selected_side")),
    )
    raw = json.dumps(components, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def build_event_key_dict(entry: dict[str, Any]) -> dict[str, str]:
    """Return the human-readable components of the event key (for display)."""
    return {
        "league":        _norm(entry.get("league")),
        "event_date":    _norm(entry.get("event_date")),
        "away_team":     _norm(entry.get("away_team")),
        "home_team":     _norm(entry.get("home_team")),
        "market_type":   _norm(entry.get("market_type") or "ml"),
        "selected_side": _norm(entry.get("selected_side")),
    }


# ---------------------------------------------------------------------------
# Deduplication engine
# ---------------------------------------------------------------------------

def deduplicate_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Group a list of ML pick entries by canonical event_key.

    Returns:
        {
          canonical_events: {
            "<event_key>": {
              event_key_components    : dict
              financial_entry_count   : int
              model_observation_count : int   (always 1)
              calibration_observation_count : int (always 1)
              gross_stake             : float | None
              duplicate_exposure      : float | None  (stake beyond 1st ticket)
              entries                 : list[dict]
              is_duplicate            : bool
            }
          },
          summary: {
            total_tickets              : int
            unique_events              : int
            duplicate_tickets          : int
            total_financial_stake      : float | None
            total_model_observations   : int
            total_calibration_observations: int
            total_duplicate_exposure   : float | None
          }
        }
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        key = build_event_key(entry)
        groups.setdefault(key, []).append(entry)

    canonical_events: dict[str, dict[str, Any]] = {}
    total_tickets     = 0
    total_stake       = 0.0
    any_stake         = False
    dup_tickets       = 0
    total_dup_exposure = 0.0
    any_dup_exposure  = False

    for key, group in groups.items():
        count = len(group)
        total_tickets += count

        stakes = [_to_float(e.get("stake")) for e in group]
        valid_stakes = [s for s in stakes if s is not None]

        gross_stake = sum(valid_stakes) if valid_stakes else None
        dup_exposure = sum(valid_stakes[1:]) if len(valid_stakes) > 1 else 0.0

        if gross_stake is not None:
            total_stake += gross_stake
            any_stake = True
        if dup_exposure:
            total_dup_exposure += dup_exposure
            any_dup_exposure = True

        if count > 1:
            dup_tickets += count - 1

        canonical_events[key] = {
            "event_key":                  key,
            "event_key_components":       build_event_key_dict(group[0]),
            "financial_entry_count":      count,
            "model_observation_count":    1,
            "calibration_observation_count": 1,
            "gross_stake":                gross_stake,
            "duplicate_exposure":         dup_exposure if count > 1 else 0.0,
            "is_duplicate":               count > 1,
            "entries":                    group,
        }

    return {
        "canonical_events": canonical_events,
        "summary": {
            "total_tickets":                  total_tickets,
            "unique_events":                  len(groups),
            "duplicate_tickets":              dup_tickets,
            "total_financial_stake":          total_stake if any_stake else None,
            "total_model_observations":       len(groups),
            "total_calibration_observations": len(groups),
            "total_duplicate_exposure":       total_dup_exposure if any_dup_exposure else 0.0,
        },
    }


def annotate_entries_with_dedup(
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Return a copy of each entry annotated with its dedup fields.
    Duplicate entries get is_primary=False and duplicate_exposure on the group.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    order:  dict[str, list[int]]             = {}
    for idx, entry in enumerate(entries):
        key = build_event_key(entry)
        groups.setdefault(key, []).append(entry)
        order.setdefault(key, []).append(idx)

    out: list[dict[str, Any]] = [dict(e) for e in entries]
    for key, idxs in order.items():
        group = groups[key]
        count = len(group)
        stakes = [_to_float(entries[i].get("stake")) for i in idxs]
        valid  = [s for s in stakes if s is not None]
        gross  = sum(valid) if valid else None
        dup_ex = sum(valid[1:]) if len(valid) > 1 else 0.0
        for rank, idx in enumerate(idxs):
            out[idx]["event_key"]                   = key
            out[idx]["event_key_components"]        = build_event_key_dict(entries[idx])
            out[idx]["financial_entry_count"]       = count
            out[idx]["model_observation_count"]     = 1
            out[idx]["calibration_observation_count"] = 1
            out[idx]["is_primary_observation"]      = (rank == 0)
            out[idx]["is_duplicate_entry"]          = (rank > 0)
            out[idx]["gross_stake"]                 = gross
            out[idx]["duplicate_exposure"]          = dup_ex
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(v: Any) -> str:
    return (str(v) if v is not None else "").lower().strip()


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
