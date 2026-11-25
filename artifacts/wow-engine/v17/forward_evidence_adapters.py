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
                "event_start_time_utc": start.isoformat(),
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


# --- Soccer 1X2 (OpenFootball CC0) ------------------------------------------

def score_multinomial(artifact: Mapping[str, Any], calibrator: Mapping[str, Any],
                      values: Sequence[float]) -> dict[str, float]:
    """Standardized multinomial logit plus the stored temperature calibration.

    Mirrors ``LogisticRegression.predict_proba`` (softmax of the decision
    function) followed by ``multiclass_candidate_lifecycle._temperature_scale``.
    """
    import math

    classes = [str(c) for c in artifact["classes"]]
    mean, scale = artifact["scaler_mean"], artifact["scaler_scale"]
    x = [(float(v) - float(m)) / float(s) for v, m, s in zip(values, mean, scale)]
    logits = [float(b) + sum(float(w) * xi for w, xi in zip(row, x))
              for b, row in zip(artifact["intercepts"], artifact["coefficients"])]
    top = max(logits)
    exps = [math.exp(z - top) for z in logits]
    total = sum(exps)
    raw = [e / total for e in exps]
    temperature = float(calibrator["temperature"])
    scaled = [math.log(min(1.0, max(p, 1e-12))) / temperature for p in raw]
    top = max(scaled)
    exps = [math.exp(z - top) for z in scaled]
    total = sum(exps)
    return {label: e / total for label, e in zip(classes, exps)}


class SoccerOpenFootballAdapter:
    """Soccer 1X2 for one competition with the OpenFootball result-form candidate.

    Fixtures and official results come from the same CC0 source the candidate
    trains on. OpenFootball kickoff times are local, so the pregame anchor is
    00:00 UTC on the listed match date: a capture is only taken before that
    instant, which precedes every kickoff on that date in Europe.
    """

    sport = "SOCCER"
    market_family = "OUTRIGHT_WINNER"
    outcome_space = "THREE_WAY_1X2"
    source = "OPENFOOTBALL:football.json"

    def __init__(self, league: str, fetch_season: Any = None):
        from v17 import soccer_openfootball_candidate as soccer

        if league not in soccer.COMPETITIONS:
            raise ValueError(league)
        self._soccer = soccer
        self.league = league
        self.code = soccer.COMPETITIONS[league]
        self.model_family = f"{soccer.MODEL_FAMILY}_{league}"
        self._fetch = fetch_season or self._fetch_season
        self._cache: dict[str, list[Mapping[str, Any]]] = {}

    def _fetch_season(self, code: str, season: str) -> list[Mapping[str, Any]]:
        import requests

        url = f"{self._soccer.BASE_URL}/{season}/{code}.json"
        response = requests.get(url, timeout=30)
        if response.status_code == 404:
            return []
        if response.status_code != 200:
            raise ForwardEvidenceBlocked("SOCCER_OPENFOOTBALL_HTTP_FAILED")
        body = response.json()
        matches = body.get("matches") if isinstance(body, dict) else None
        if not isinstance(matches, list):
            raise ForwardEvidenceBlocked("SOCCER_OPENFOOTBALL_SCHEMA_INVALID")
        return [m for m in matches if isinstance(m, Mapping)]

    def _season(self, season: str) -> list[Mapping[str, Any]]:
        if season not in self._cache:
            self._cache[season] = list(self._fetch(self.code, season))
        return self._cache[season]

    @staticmethod
    def season_for(now: datetime) -> str:
        year = now.year if now.month >= 7 else now.year - 1
        return f"{year}-{(year + 1) % 100:02d}"

    def _identity(self, match: Mapping[str, Any]) -> tuple[str, datetime, str, str]:
        date_text = str(match.get("date") or "").strip()
        home, away = str(match.get("team1") or "").strip(), str(match.get("team2") or "").strip()
        if not date_text or not home or not away or home == away:
            raise ForwardEvidenceBlocked("SOCCER_FIXTURE_IDENTITY_INVALID")
        anchor = datetime.fromisoformat(f"{date_text}T00:00:00+00:00")
        digest = self._soccer._hash_json({"competition": self.league, "date": date_text, "home": home, "away": away})
        return f"{self.league}:{digest[:32]}", anchor, home, away

    def eligible_candidates(self, db: Any, now: datetime) -> Sequence[Mapping[str, Any]]:
        return _rows(
            db.table("wow_d1_candidate_artifacts").select("*")
            .eq("sport", self.sport).eq("league", self.league)
            .eq("market_family", self.market_family).eq("model_family", self.model_family)
            .eq("lifecycle_state", "CANDIDATE").eq("research_screen_pass", True)
            .order("created_at", desc=True).limit(20).execute()
        )

    def verify_candidate(self, candidate: Mapping[str, Any], now: datetime) -> None:
        from math import isfinite

        soccer = self._soccer
        names = list(soccer.FEATURE_NAMES)
        ok = (candidate.get("sport") == self.sport and candidate.get("league") == self.league
              and candidate.get("market_family") == self.market_family
              and candidate.get("model_family") == self.model_family
              and candidate.get("feature_schema_version") == soccer.FEATURE_SCHEMA_VERSION
              and candidate.get("source_policy_id") == soccer.SOURCE_POLICY_ID
              and candidate.get("lifecycle_state") == "CANDIDATE"
              and candidate.get("research_screen_pass") is True
              and all(candidate.get(k) is False for k in ("active", "promoted", "probability_publishable",
                                                          "can_execute", "automatic_certification",
                                                          "automatic_promotion")))
        if not ok:
            raise ForwardEvidenceBlocked("SOCCER_CANDIDATE_LIFECYCLE_INVALID")
        if _aware(candidate.get("created_at")) > now:
            raise ForwardEvidenceBlocked("SOCCER_CANDIDATE_POST_AS_OF")
        artifact, cal = candidate.get("artifact_payload"), candidate.get("calibrator_payload")
        if not isinstance(artifact, Mapping) or soccer._hash_json(artifact) != candidate.get("artifact_checksum"):
            raise ForwardEvidenceBlocked("SOCCER_ARTIFACT_CHECKSUM_MISMATCH")
        try:
            shapes_ok = (
                artifact.get("artifact_format") == "STANDARDIZED_MULTINOMIAL_LOGISTIC_JSON_V1"
                and artifact.get("model_family") == self.model_family
                and list(artifact.get("feature_names") or []) == names
                and sorted(artifact.get("classes") or []) == ["AWAY", "DRAW", "HOME"]
                and len(artifact["scaler_mean"]) == len(names) == len(artifact["scaler_scale"])
                and all(float(s) > 0 for s in artifact["scaler_scale"])
                and len(artifact["intercepts"]) == 3 == len(artifact["coefficients"])
                and all(len(row) == len(names) for row in artifact["coefficients"])
                and all(isfinite(float(v)) for row in artifact["coefficients"] for v in row)
                and isinstance(cal, Mapping)
                and cal.get("method") == "TEMPERATURE_SCALE_CHRONOLOGICAL_V1"
                and isfinite(float(cal.get("temperature"))) and float(cal["temperature"]) > 0
            )
        except (KeyError, TypeError, ValueError):
            shapes_ok = False
        if not shapes_ok:
            raise ForwardEvidenceBlocked("SOCCER_ARTIFACT_SCHEMA_INVALID")

    def upcoming_events(self, db: Any, now: datetime, horizon: timedelta) -> Sequence[EventIdentity]:
        found: dict[str, EventIdentity | None] = {}
        for match in self._season(self.season_for(now)):
            if self._soccer._score(dict(match)) is not None:
                continue
            try:
                event_id, anchor, home, away = self._identity(match)
            except ForwardEvidenceBlocked:
                continue
            if not now < anchor <= now + horizon:
                continue
            if event_id in found:
                found[event_id] = None
                continue
            found[event_id] = EventIdentity(event_id, anchor, home, away, self.source, payload=dict(match))
        return sorted((e for e in found.values() if e is not None), key=lambda e: e.event_start_time_utc)

    def load_context(self, db: Any, now: datetime) -> dict[str, list[dict[str, Any]]]:
        soccer = self._soccer
        histories: dict[str, list[dict[str, Any]]] = {}
        scored = []
        for season in soccer.SEASONS:
            for match in self._season(season):
                score = soccer._score(dict(match))
                if score is None:
                    continue
                start = soccer._event_time(dict(match))
                if start >= now:
                    continue
                scored.append((start, str(match.get("team1") or "").strip(), str(match.get("team2") or "").strip(),
                               score, str(match.get("date") or "")))
        for start, home, away, (hg, ag), date_text in sorted(scored):
            hp, ap = (1, 1) if hg == ag else ((3, 0) if hg > ag else (0, 3))
            event_id = soccer._hash_json({"competition": self.league, "date": date_text, "home": home, "away": away})[:32]
            histories.setdefault(home, []).append({"event_id": event_id, "start": start, "points": hp, "goal_diff": hg - ag})
            histories.setdefault(away, []).append({"event_id": event_id, "start": start, "points": ap, "goal_diff": ag - hg})
        return histories

    def score(self, event: EventIdentity, context: Any, candidate: Mapping[str, Any], now: datetime) -> Forecast:
        soccer = self._soccer
        start = soccer._event_time(dict(event.payload))  # the same anchor the candidate trained on
        hs = soccer._summary(context.get(event.home_participant, []), start)
        aws = soccer._summary(context.get(event.away_participant, []), start)
        if hs is None or aws is None:
            raise ForwardEvidenceBlocked("SOCCER_PRIOR_SAMPLE_INSUFFICIENT")
        features = {}
        for side, summary in (("home", hs), ("away", aws)):
            features[f"{side}_recent_points_per_game"] = summary["points_per_game"]
            features[f"{side}_recent_goal_diff"] = summary["goal_diff"]
            features[f"{side}_recent_win_rate"] = summary["win_rate"]
            features[f"{side}_recent_draw_rate"] = summary["draw_rate"]
            features[f"{side}_games_prior_log"] = summary["games_prior_log"]
            features[f"{side}_rest_days_capped"] = summary["rest_days_capped"]
        vector = [float(features[name]) for name in soccer.FEATURE_NAMES]
        probabilities = score_multinomial(candidate["artifact_payload"], candidate["calibrator_payload"], vector)
        digest = soccer._hash_json({"home": hs["prior_event_ids"], "away": aws["prior_event_ids"]})
        return Forecast(probabilities=probabilities, features=features, source_history_digest=digest)

    def settlements(self, db: Any, pending: Sequence[Mapping[str, Any]]) -> Sequence[Mapping[str, Any]]:
        if not pending:
            return []
        wanted = {str(row["official_event_id"]): _aware(row["event_start_time_utc"]) for row in pending}
        now = datetime.now(timezone.utc)
        seasons = {self.season_for(start) for start in wanted.values()}
        out: list[dict[str, Any]] = []
        for season in sorted(seasons):
            self._cache.pop(season, None)  # results must be re-read, not served from the capture fetch
            for match in self._season(season):
                score = self._soccer._score(dict(match))
                if score is None:
                    continue
                try:
                    event_id, anchor, _, _ = self._identity(match)
                except ForwardEvidenceBlocked:
                    continue
                if event_id not in wanted or anchor != wanted[event_id]:
                    continue
                hg, ag = score
                out.append({
                    "official_event_id": event_id,
                    "event_start_time_utc": anchor.isoformat(),
                    "outcome": "DRAW" if hg == ag else ("HOME" if hg > ag else "AWAY"),
                    "home_score": hg,
                    "away_score": ag,
                    "settlement_source": self.source,
                    "source_retrieved_at": now.isoformat(),
                })
        return out


def soccer_forward_evidence_after_maintenance(db: Any) -> dict[str, Any]:
    """Soccer maintenance hook: one isolated receipt per competition."""
    from v17.forward_evidence import run_forward_evidence
    from v17.soccer_openfootball_candidate import COMPETITIONS

    receipts = {}
    for league in COMPETITIONS:
        try:
            receipts[league] = run_forward_evidence(db, SoccerOpenFootballAdapter(league))
        except Exception as exc:  # noqa: BLE001 - one league never blocks another
            receipts[league] = {"status": "FAILED", "code": f"FORWARD_EVIDENCE_HOOK_{type(exc).__name__.upper()}",
                                "probability_publishable": False, "can_execute": False}
    return {"status": "COMPLETED", "leagues": receipts, "probability_publishable": False, "can_execute": False}
