"""Offline, nonauthoritative crosswalk for PropsMadness research leads.

The input catalog is caller-supplied candidate evidence, NOT an authenticated
official schedule/roster feed. A match here is a proposed identity only: it can
never make a line, source, pregame feature or sporting probability publishable.
No HTTP requests, source rights promotion, model scoring or database writes.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

HANDOFF_SCHEMA = "WOW_PM_SHARED_INTELLIGENCE_RESEARCH_V1"
CROSSWALK_SCHEMA = "WOW_PM_OFFLINE_IDENTITY_CROSSWALK_V1"


class CrosswalkHold(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise CrosswalkHold(code)


def _ts(value: Any) -> datetime:
    _require(isinstance(value, str), "PM_CROSSWALK_TIMESTAMP_INVALID")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise CrosswalkHold("PM_CROSSWALK_TIMESTAMP_INVALID") from exc
    _require(result.utcoffset() is not None, "PM_CROSSWALK_TIMESTAMP_INVALID")
    return result.astimezone(timezone.utc)


def _source(row: Mapping[str, Any]) -> None:
    url = row.get("source_url")
    _require(isinstance(url, str), "PM_CROSSWALK_SOURCE_MISSING")
    parsed = urlparse(url)
    _require(parsed.scheme == "https" and bool(parsed.hostname)
             and parsed.hostname.lower() not in {"propsmadness.com", "www.propsmadness.com"}
             and isinstance(row.get("source_record_id"), str)
             and bool(row["source_record_id"].strip()), "PM_CROSSWALK_SOURCE_MISSING")


def _candidate_index(records: Sequence[Mapping[str, Any]], *, kind: str):
    _require(isinstance(records, (list, tuple)) and len(records) <= 1000,
             "PM_CROSSWALK_CATALOG_INVALID")
    index: dict[tuple[str, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in records:
        _require(isinstance(row, Mapping), "PM_CROSSWALK_CATALOG_INVALID")
        _require(not any(k in row for k in (
            "model_probability", "calibrated_probability", "calibrated_lower_bound",
            "probability_publishable", "can_execute", "identity_attested",
        )), "PM_CROSSWALK_AUTHORITY_INJECTION")
        _source(row)
        _require(row.get("sport") in {"NBA", "NFL", "MLB", "WNBA"},
                 "PM_CROSSWALK_SPORT_INVALID")
        _ts(row.get("retrieved_at"))
        if kind == "event":
            fields = ("sport", "provider_event_alias")
            required = ("official_event_id_candidate", "fixture_text_unverified")
            _ts(row.get("event_start_utc"))
        else:
            fields = ("sport", "provider_event_alias", "provider_player_alias")
            required = ("official_event_id_candidate", "official_player_id_candidate")
        _require(all(isinstance(row.get(f), str) and row[f].strip()
                     for f in (*fields, *required)),
                 "PM_CROSSWALK_CATALOG_ID_INVALID")
        index[tuple(row[f] for f in fields)].append(row)
    return index


def reconcile_research_aliases(
    handoff: Mapping[str, Any], *,
    event_candidates: Sequence[Mapping[str, Any]],
    player_candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reconcile every display-only prop to at most one *suggested* identity.

    Source catalogs are untrusted input; uniqueness and chronology help avoid
    mistakes, but they do not authenticate provenance or create model authority.
    """
    _require(isinstance(handoff, Mapping) and handoff.get("schema") == HANDOFF_SCHEMA,
             "PM_CROSSWALK_HANDOFF_INVALID")
    _require(all(handoff.get(k) is False for k in (
        "source_rights_verified", "historical_training_authorized",
        "prediction_authority", "probability_publishable",
        "production_write_authorized", "can_execute",
    )) and handoff.get("verified_sporting_features") == []
        and handoff.get("model_consumption_receipts") == [],
             "PM_CROSSWALK_HANDOFF_AUTHORITY_CONFLICT")
    rows = handoff.get("prop_discovery_hints")
    _require(isinstance(rows, list) and type(handoff.get("unique_prop_hints")) is int
             and len(rows) == handoff["unique_prop_hints"] and len(rows) <= 1000,
             "PM_CROSSWALK_HANDOFF_RECONCILIATION_INVALID")
    ev = _candidate_index(event_candidates, kind="event")
    players = _candidate_index(player_candidates, kind="player")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        _require(isinstance(row, Mapping), "PM_CROSSWALK_ROW_INVALID")
        _require(row.get("evidence_domain") == "DISCOVERY_RESEARCH_ONLY"
                 and row.get("official_identity_verified") is False
                 and row.get("exact_line_market_authority") is False
                 and row.get("pregame_feature_eligible") is False
                 and row.get("model_probability") is None
                 and row.get("calibrated_lower_bound") is None
                 and row.get("can_execute") is False,
                 "PM_CROSSWALK_ROW_AUTHORITY_CONFLICT")
        sport, eid, pid, ref = (
            row.get("sport"), row.get("provider_event_alias"),
            row.get("provider_player_alias"), row.get("research_reference"),
        )
        _require(sport in {"NBA", "NFL", "MLB", "WNBA"}
                 and all(isinstance(x, str) and bool(x.strip())
                         for x in (eid, pid, ref)),
                 "PM_CROSSWALK_ROW_INVALID")
        _require(ref not in seen, "PM_CROSSWALK_DUPLICATE_REFERENCE")
        seen.add(ref)
        captured = _ts(row.get("captured_at"))
        event_matches = ev.get((sport, eid), [])
        player_matches = players.get((sport, eid, pid), [])
        status = "CANDIDATE_MATCH_UNATTESTED"
        event_id = player_id = None
        if not event_matches or not player_matches:
            status = "CROSSWALK_CANDIDATE_MISSING"
        elif len(event_matches) != 1 or len(player_matches) != 1:
            status = "CROSSWALK_CANDIDATE_AMBIGUOUS"
        else:
            event, player = event_matches[0], player_matches[0]
            if (event["official_event_id_candidate"]
                    != player["official_event_id_candidate"]
                    or row.get("fixture_text_unverified")
                    != event["fixture_text_unverified"]):
                status = "CROSSWALK_EVENT_OR_FIXTURE_CONFLICT"
            elif (_ts(event["retrieved_at"]) > captured
                  or _ts(player["retrieved_at"]) > captured
                  or captured >= _ts(event["event_start_utc"])):
                status = "CROSSWALK_STALE_OR_POSTSTART"
            else:
                event_id = event["official_event_id_candidate"]
                player_id = player["official_player_id_candidate"]
        results.append({
            "research_reference": ref, "sport": sport,
            "provider_event_alias": eid, "provider_player_alias": pid,
            "status": status,
            "suggested_official_event_id_unverified": event_id,
            "suggested_official_player_id_unverified": player_id,
            "identity_attested": False,
            "pregame_feature_eligible": False,
            "market_price_certified": False,
            "model_probability": None, "calibrated_lower_bound": None,
            "can_execute": False,
        })
    linked = sum(r["status"] == "CANDIDATE_MATCH_UNATTESTED" for r in results)
    return {
        "schema": CROSSWALK_SCHEMA, "status": "OFFLINE_CANDIDATES_NOT_IDENTITY_AUTHORITY",
        "received": len(rows), "linked_unattested": linked,
        "held": len(rows) - linked, "rows": results,
        "reconciled_exactly_once": len(results) == len(rows),
        "identity_attested": False, "source_rights_verified": False,
        "model_consumption_receipts": [],
        "probability_publishable": False, "can_execute": False,
    }
