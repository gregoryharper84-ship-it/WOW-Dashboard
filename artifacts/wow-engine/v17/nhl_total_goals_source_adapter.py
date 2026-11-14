"""Read-only adapter for source-hashed WOW NHL D1 persisted game records.

Keeps all version hashes in the audit. Conflicting versions reject by default;
explicit research-only override permits selection when score/identity match.
Never authorizes model publication or mutation of Supabase records.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from v17.nhl_total_goals_challenger import _timestamp, NHLTotalsResearchError

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False


def adapt_persisted_nhl_games(
    source_rows: Sequence[Mapping[str, Any]], *,
    allow_matching_source_versions: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not source_rows:
        raise NHLTotalsResearchError("NHL_TOTALS_SOURCE_ROWS_EMPTY", "no records")
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for rec in source_rows:
        try:
            season = str(rec["season"])
            event_id = str(rec["official_event_id"])
            outcome = rec["outcome_json"]
            h, a = outcome["home_score"], outcome["away_score"]
            if type(h) is not int or type(a) is not int or min(h, a) < 0 or h == a:
                raise ValueError("scores invalid")
            home, away = str(rec["home_participant"]).strip().upper(), str(rec["away_participant"]).strip().upper()
            if not home or not away or home == away:
                raise ValueError("teams invalid")
            if rec.get("source_provider") != "NHL_PUBLIC_WEB_API":
                raise ValueError("source invalid")
            if rec.get("historical_reconstruction") is not True or rec.get("can_execute") is not False:
                raise ValueError("governance fields invalid")
            source_hash = str(rec["source_payload_sha256"]).lower()
            if len(source_hash) != 64 or any(c not in "0123456789abcdef" for c in source_hash):
                raise ValueError("source hash invalid")
            start = _timestamp(rec["event_start_time"])
            retrieved = _timestamp(rec["source_retrieved_at"])
            groups[(season, event_id)].append({
                "season": season, "id": event_id, "start": start.isoformat(),
                "home": home, "away": away, "h": h, "a": a,
                "sha": source_hash, "retrieved": retrieved.isoformat(),
            })
        except (TypeError, KeyError, ValueError, AttributeError) as exc:
            raise NHLTotalsResearchError("NHL_TOTALS_PERSISTED_ROW_INVALID", str(exc)) from exc
    result: list[dict[str, Any]] = []
    version_drift: list[dict[str, Any]] = []
    for (season, event_id), versions in sorted(groups.items()):
        identities = {(v["season"], v["id"], v["start"], v["home"], v["away"], v["h"], v["a"]) for v in versions}
        if len(identities) != 1:
            raise NHLTotalsResearchError("NHL_TOTALS_SOURCE_IDENTITY_CONFLICT", event_id)
        hashes = sorted({v["sha"] for v in versions})
        if len(hashes) > 1 or len(versions) > 1:
            version_drift.append({"season": season, "official_event_id": event_id, "source_hashes": hashes, "source_instances": len(versions)})
            if not allow_matching_source_versions:
                raise NHLTotalsResearchError("NHL_TOTALS_SOURCE_VERSION_REVIEW_REQUIRED", event_id)
        chosen = max(versions, key=lambda v: (v["retrieved"], v["sha"]))
        result.append({
            "sport": "NHL", "source_provider": "NHL_PUBLIC_WEB_API",
            "historical_reconstruction": True, "can_execute": False,
            "official_event_id": event_id, "season": season,
            "event_start_time": chosen["start"],
            "home_team": chosen["home"], "away_team": chosen["away"],
            "home_score": chosen["h"], "away_score": chosen["a"],
            "source_payload_sha256": chosen["sha"],
        })
    audit = {"source_instances": len(source_rows), "canonical_games": len(result),
             "version_drift_events": len(version_drift), "version_drift_ledger": version_drift,
             "research_only": True, "source_versions_explicitly_reconciled": bool(allow_matching_source_versions),
             "probability_publishable": False, "can_execute": False}
    return result, audit
