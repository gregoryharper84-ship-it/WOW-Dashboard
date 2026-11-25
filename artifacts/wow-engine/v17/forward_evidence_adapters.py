"""Sport adapters for the shared forward-evidence lifecycle.

Each adapter supplies only what is genuinely sport-specific: which candidate
family is evaluated, where upcoming fixtures and official results come from,
and how the frozen artifact scores one event. Freezing, capture, settlement,
grading and failure isolation live in ``v17.forward_evidence``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from v17.forward_evidence import EventIdentity, Forecast, ForwardEvidenceBlocked

CAN_EXECUTE = False
START_MATCH_TOLERANCE = timedelta(minutes=90)


def _rows(response: Any) -> list[Mapping[str, Any]]:
    data = getattr(response, "data", None)
    if not isinstance(data, list) or not all(isinstance(row, Mapping) for row in data):
        raise ForwardEvidenceBlocked("FORWARD_EVIDENCE_READ_INVALID")
    return data


def _paginate(query_fn: Any, page: int = 1000, limit: int = 200_000) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    start = 0
    while start < limit:
        batch = _rows(query_fn().range(start, start + page - 1).execute())
        rows.extend(batch)
        if len(batch) < page:
            return rows
        start += page
    raise ForwardEvidenceBlocked("FORWARD_EVIDENCE_HISTORY_TOO_LARGE")


def _aware(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ForwardEvidenceBlocked("FORWARD_EVIDENCE_TIME_NOT_TIMEZONE_AWARE")
    return parsed.astimezone(timezone.utc)


class NCAAFResultFormAdapter:
    """NCAAF moneyline (outright winner) with the result-form research candidate.

    Fixtures: CFBD ``/games`` (FBS) for the current season. Official results:
    CFBD finals already persisted by NCAAF maintenance in
    ``wow_ncaaf_training_games``. Scoring: ``score_forward_shadow`` unchanged.
    """

    sport = "NCAAF"
    league = "NCAAF"
    market_family = "OUTRIGHT_WINNER"
    outcome_space = "BINARY_HOME_AWAY"
    history_table = "wow_ncaaf_training_games"
    history_columns = ("official_event_id,event_start_time,home_team,away_team,home_points,"
                       "away_points,home_won,result_source,result_source_timestamp")

    def __init__(self, season_rows: Any = None):
        from v17 import ncaaf_forward_candidate_shadow as shadow

        self._shadow = shadow
        self.model_family = shadow.MODEL_FAMILY
        self._season_rows = season_rows

    @staticmethod
    def season_for(now: datetime) -> int:
        # CFBD seasons are named for their autumn; January bowls belong to the prior year.
        return now.year if now.month >= 7 else now.year - 1

    def eligible_candidates(self, db: Any, now: datetime) -> Sequence[Mapping[str, Any]]:
        return _rows(
            db.table("wow_d1_candidate_artifacts").select("*")
            .eq("sport", self.sport).eq("league", self.league)
            .eq("market_family", self.market_family).eq("model_family", self.model_family)
            .eq("lifecycle_state", "CANDIDATE").eq("research_screen_pass", True)
            .order("created_at", desc=True).limit(20).execute()
        )

    def verify_candidate(self, candidate: Mapping[str, Any], now: datetime) -> None:
        self._shadow._candidate(candidate, now)  # raises ShadowScoringBlocked with a typed code

    def upcoming_events(self, db: Any, now: datetime, horizon: timedelta) -> Sequence[EventIdentity]:
        from v17 import ncaaf_event_identity as ident

        try:
            rows = (self._season_rows or ident._season_rows)(self.season_for(now))
        except Exception as exc:  # noqa: BLE001
            raise ForwardEvidenceBlocked(str(getattr(exc, "code", "") or "NCAAF_FIXTURE_SOURCE_UNAVAILABLE")) from exc
        found: dict[str, EventIdentity | None] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            event_id = str(row.get("id") or row.get("event_id") or "").strip()
            if not event_id:
                continue
            if row.get("startTimeTBD") is True or row.get("start_time_tbd") is True:
                continue  # placeholder kickoff times cannot anchor a pregame capture
            try:
                start = ident._aware(ident._start_value(row))
            except Exception:  # noqa: BLE001
                continue
            if not now < start <= now + horizon:
                continue
            home, away = str(ident._home_name(row) or "").strip(), str(ident._away_name(row) or "").strip()
            neutral = row.get("neutralSite", row.get("neutral_site"))
            if event_id in found:
                found[event_id] = None  # duplicated provider id: ambiguous, never captured
                continue
            found[event_id] = EventIdentity(
                official_event_id=event_id,
                event_start_time_utc=start,
                home_participant=home,
                away_participant=away,
                identity_provider=ident.SOURCE_PROVIDER,
                payload={
                    "event_id": event_id,
                    "event_start_time": start.isoformat(),
                    "home_team": home,
                    "away_team": away,
                    "neutral_site": neutral if type(neutral) is bool else None,
                    "identity_provider": ident.SOURCE_PROVIDER,
                    "identity_resolution": "CFBD_EXACT_PARTICIPANTS_START_MATCH",
                },
            )
        return sorted((e for e in found.values() if e is not None), key=lambda e: e.event_start_time_utc)

    def load_context(self, db: Any, now: datetime) -> list[Mapping[str, Any]]:
        return _paginate(lambda: db.table(self.history_table).select(self.history_columns).order("event_start_time")
                         .order("official_event_id"))

    def score(self, event: EventIdentity, context: Any, candidate: Mapping[str, Any], now: datetime) -> Forecast:
        result = self._shadow.score_forward_shadow(event.payload, context, candidate, as_of_utc=now)
        p_home = float(result["research_probability_home"])
        return Forecast(
            probabilities={"HOME": p_home, "AWAY": 1.0 - p_home},
            features=dict(result["features"]),
            source_history_digest=str(result["source_history_digest"]),
        )

    def settlements(self, db: Any, pending: Sequence[Mapping[str, Any]]) -> Sequence[Mapping[str, Any]]:
        if not pending:
            return []
        starts = {str(row["official_event_id"]): _aware(row["event_start_time_utc"]) for row in pending}
        finals = _rows(
            db.table(self.history_table).select(self.history_columns)
            .in_("official_event_id", sorted(starts)).execute()
        )
        out: list[dict[str, Any]] = []
        for final in finals:
            event_id = str(final.get("official_event_id") or "")
            start = starts.get(event_id)
            if start is None:
                continue
            hp, ap = final.get("home_points"), final.get("away_points")
            if type(hp) is not int or type(ap) is not int:
                continue  # not final yet
            if final.get("result_source") != "CFBD:/games":
                out.append({"blocked_code": "NCAAF_SETTLEMENT_SOURCE_UNVERIFIED"})
                continue
            if abs(_aware(final.get("event_start_time")) - start) > START_MATCH_TOLERANCE:
                out.append({"blocked_code": "NCAAF_SETTLEMENT_START_MISMATCH"})
                continue
            if type(final.get("home_won")) is not bool or (hp != ap and final["home_won"] != (hp > ap)):
                out.append({"blocked_code": "NCAAF_SETTLEMENT_OUTCOME_INCONSISTENT"})
                continue
            out.append({
                "official_event_id": event_id,
                "event_start_time_utc": _aware(final.get("event_start_time")).isoformat(),  # final's own kickoff
                "outcome": "VOID" if hp == ap else ("HOME" if hp > ap else "AWAY"),
                "home_score": hp,
                "away_score": ap,
                "settlement_source": "CFBD:/games",
                "source_retrieved_at": _aware(final.get("result_source_timestamp")).isoformat(),
            })
        return out


def ncaaf_forward_evidence_after_maintenance(db: Any, *, fresh_acquisition_complete: bool) -> dict[str, Any]:
    """NCAAF maintenance hook. Capture only on a fresh CFBD acquisition; always settle."""
    from v17.forward_evidence import run_forward_evidence

    return run_forward_evidence(
        db,
        NCAAFResultFormAdapter(),
        capture=bool(fresh_acquisition_complete),
        capture_skip_reason=None if fresh_acquisition_complete else "NCAAF_ACQUISITION_NOT_FRESH",
    )
