"""Sport adapters for the shared forward-evidence lifecycle.

Each adapter supplies only what is genuinely sport-specific: which candidate
family is evaluated, where upcoming fixtures and official results come from,
and how the frozen artifact scores one event. Freezing, capture, settlement,
grading and failure isolation live in ``v17.forward_evidence``.
"""
from __future__ import annotations

import hashlib
import json
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
    trains on. OpenFootball kickoff times are local and carry no zone, so the
    pregame anchor is the listed local kickoff converted with the league's
    time zone, minus a two-hour safety margin. A capture is only taken before
    the anchor, so a one-hour zone or listing error still leaves every capture
    pregame. Untimed fixtures carry a round placeholder date (the real kickoff
    can be days earlier), so they are blocked, not anchored. A team with a
    past league match that has no recorded result is blocked too: its form
    features would not be as of now.
    """

    LEAGUE_TZ = {"EPL": "Europe/London", "BUNDESLIGA": "Europe/Berlin", "LALIGA": "Europe/Madrid",
                 "SERIE_A": "Europe/Rome", "LIGUE_1": "Europe/Paris"}
    KICKOFF_SAFETY = timedelta(hours=2)

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

    def _listed_anchor(self, match: Mapping[str, Any]) -> datetime | None:
        """Listed local kickoff minus the safety margin, or None if not provable."""
        date_text = str(match.get("date") or "").strip()
        time_text = str(match.get("time") or "").strip()
        zone = self.LEAGUE_TZ.get(self.league)
        if not (zone and len(time_text) == 5 and time_text[2] == ":" and time_text.replace(":", "").isdigit()):
            return None
        try:
            from zoneinfo import ZoneInfo

            local = datetime.fromisoformat(f"{date_text}T{time_text}:00").replace(tzinfo=ZoneInfo(zone))
        except Exception:  # noqa: BLE001 - bad time or no tz database
            return None
        return local.astimezone(timezone.utc) - self.KICKOFF_SAFETY

    def _identity(self, match: Mapping[str, Any]) -> tuple[str, datetime, str, str]:
        date_text = str(match.get("date") or "").strip()
        home, away = str(match.get("team1") or "").strip(), str(match.get("team2") or "").strip()
        if not date_text or not home or not away or home == away:
            raise ForwardEvidenceBlocked("SOCCER_FIXTURE_IDENTITY_INVALID")
        # Untimed: midnight on the listed date only places the fixture in the
        # horizon; score() blocks it because that date is not a kickoff.
        anchor = self._listed_anchor(match) or datetime.fromisoformat(f"{date_text}T00:00:00+00:00")
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

    def load_context(self, db: Any, now: datetime) -> dict[str, Any]:
        soccer = self._soccer
        histories: dict[str, list[dict[str, Any]]] = {}
        unrecorded: set[str] = set()
        today = now.astimezone(timezone.utc).date().isoformat()
        for match in self._season(self.season_for(now)):
            if str(match.get("date") or "") < today and soccer._score(dict(match)) is None:
                unrecorded.update({str(match.get("team1") or "").strip(), str(match.get("team2") or "").strip()})
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
        return {"histories": histories, "unrecorded": unrecorded}

    def score(self, event: EventIdentity, context: Any, candidate: Mapping[str, Any], now: datetime) -> Forecast:
        soccer = self._soccer
        if self._listed_anchor(event.payload) is None:
            raise ForwardEvidenceBlocked("SOCCER_KICKOFF_TIME_UNVERIFIED")
        if {event.home_participant, event.away_participant} & context["unrecorded"]:
            raise ForwardEvidenceBlocked("SOCCER_SOURCE_RESULTS_STALE")
        histories = context["histories"]
        start = soccer._event_time(dict(event.payload))  # the same anchor the candidate trained on
        hs = soccer._summary(histories.get(event.home_participant, []), start)
        aws = soccer._summary(histories.get(event.away_participant, []), start)
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
                    event_id, _, _, _ = self._identity(match)
                except ForwardEvidenceBlocked:
                    continue
                if event_id not in wanted:  # the id already binds league, date and teams
                    continue
                hg, ag = score
                out.append({
                    "official_event_id": event_id,
                    "event_start_time_utc": wanted[event_id].isoformat(),
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


# --- NHL moneyline (official NHL web API timeline) --------------------------

class NHLRegularSeasonAdapter:
    """NHL game winner with the regular-season logistic research candidate.

    Fixtures: the official club schedules the NHL identity resolver already
    parses. Scoring: the fitted specialist's own timeline reconstruction,
    server-persisted timeline receipt and scorer, unchanged. The goalie,
    injury and snapshot inputs gate *publication* (#2629); this lane records
    research evidence only. Official results: NHL finals persisted by NHL
    maintenance in ``wow_d1_source_events``.
    """

    sport = "NHL"
    league = "NHL"
    market_family = "OUTRIGHT_WINNER"
    outcome_space = "BINARY_HOME_AWAY"
    source = "NHL_PUBLIC_WEB_API"

    def __init__(self, fetch_club_schedule: Any = None):
        from v17 import nhl_fitted_specialist as nhl

        self._nhl = nhl
        self.model_family = nhl.MODEL_FAMILY
        self._fetch = fetch_club_schedule or self._fetch_club_schedule

    def _fetch_club_schedule(self, team: str, season_id: int) -> list[Mapping[str, Any]]:
        import requests
        from v17 import nhl_event_identity as ident

        response = requests.get(ident.BASE_URL.format(team=team, season_id=season_id), timeout=12,
                                headers={"User-Agent": "WOW-V17-Free-Core/1.0"})
        if int(getattr(response, "status_code", 0) or 0) != 200:
            raise ForwardEvidenceBlocked(f"NHL_OFFICIAL_SOURCE_HTTP_{getattr(response, 'status_code', 'UNKNOWN')}")
        games = response.json().get("games")
        if not isinstance(games, list):
            raise ForwardEvidenceBlocked("NHL_OFFICIAL_SOURCE_SCHEMA_DRIFT")
        return [g for g in games if isinstance(g, Mapping)]

    def _typed(self, exc: BaseException, default: str) -> ForwardEvidenceBlocked:
        blockers = getattr(exc, "blockers", None) or ()
        return ForwardEvidenceBlocked(str(blockers[0]) if blockers else str(getattr(exc, "code", "") or default))

    def eligible_candidates(self, db: Any, now: datetime) -> Sequence[Mapping[str, Any]]:
        return _rows(
            db.table("wow_d1_candidate_artifacts").select("*")
            .eq("sport", self.sport).eq("league", self.league)
            .eq("market_family", self.market_family).eq("model_family", self.model_family)
            .eq("lifecycle_state", "CANDIDATE").eq("research_screen_pass", True)
            .order("created_at", desc=True).limit(20).execute()
        )

    def verify_candidate(self, candidate: Mapping[str, Any], now: datetime) -> None:
        if _aware(candidate.get("created_at")) > now:
            raise ForwardEvidenceBlocked("NHL_CANDIDATE_POST_AS_OF")
        try:
            self._nhl._verify_candidate(candidate)
        except Exception as exc:  # noqa: BLE001
            raise self._typed(exc, "NHL_CANDIDATE_INVALID") from exc

    def upcoming_events(self, db: Any, now: datetime, horizon: timedelta) -> Sequence[EventIdentity]:
        from v17 import nhl_event_identity as ident

        season_id = self._nhl._season_id(self._nhl._season_start_year(now))
        teams = sorted(set(ident._TEAM_ALIASES.values()))
        found: dict[str, EventIdentity] = {}
        failures = 0
        for team in teams:
            try:
                games = self._fetch(team, season_id)
            except Exception:  # noqa: BLE001 - another club's schedule lists the same game
                failures += 1
                continue
            for game in games:
                game_id = str(game.get("id") or "").strip()
                home, away = game.get("homeTeam"), game.get("awayTeam")
                if (len(game_id) != 10 or not game_id.isdigit() or game.get("gameType") != 2
                        or not isinstance(home, Mapping) or not isinstance(away, Mapping)):
                    continue
                try:
                    start = _aware(game.get("startTimeUTC"))
                except Exception:  # noqa: BLE001
                    continue
                if not now < start <= now + horizon:
                    continue
                neutral = game.get("neutralSite")
                event = EventIdentity(
                    official_event_id=game_id, event_start_time_utc=start,
                    home_participant=str(home.get("abbrev") or "").strip().upper(),
                    away_participant=str(away.get("abbrev") or "").strip().upper(),
                    identity_provider=self.source,
                    payload={"neutral_site": neutral if type(neutral) is bool else None},
                )
                prior = found.get(game_id)
                if prior is not None and (prior.event_start_time_utc, prior.home_participant,
                                          prior.away_participant) != (start, event.home_participant,
                                                                      event.away_participant):
                    raise ForwardEvidenceBlocked("NHL_SCHEDULE_IDENTITY_CONFLICT")
                found[game_id] = event
        if failures == len(teams):
            raise ForwardEvidenceBlocked("NHL_OFFICIAL_SCHEDULE_UNAVAILABLE")
        return sorted(found.values(), key=lambda e: e.event_start_time_utc)

    def load_context(self, db: Any, now: datetime) -> dict[str, Any]:
        return {"db": db, "events": {}, "now": now}

    def score(self, event: EventIdentity, context: Any, candidate: Mapping[str, Any], now: datetime) -> Forecast:
        nhl = self._nhl
        neutral = event.payload.get("neutral_site")
        if type(neutral) is not bool:
            raise ForwardEvidenceBlocked("NHL_NEUTRAL_SITE_UNVERIFIED")
        try:
            fitted = nhl._verify_candidate(candidate)
            season = nhl._validated_target_season(None, event.event_start_time_utc)
            events = context["events"].get(season)
            if events is None:
                events = context["events"][season] = nhl.load_nhl_season_events(context["db"], season=season)
            receipt = nhl.resolve_nhl_timeline_receipt(
                season=season, event_start_time=event.event_start_time_utc,
                home_team=event.home_participant, away_team=event.away_participant,
                db=context["db"], as_of_utc=now)
            recon = nhl.reconstruct_nhl_pregame_features(
                events, official_event_id=event.official_event_id,
                event_start_time=event.event_start_time_utc.isoformat(),
                home_team=event.home_participant, away_team=event.away_participant,
                season=season, neutral_site=neutral, source_timeline_receipt=receipt)
            package = nhl.score_nhl_fitted_candidate(fitted, recon, event_key=f"NHL:{event.official_event_id}",
                                                     evidence={})
        except ForwardEvidenceBlocked:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._typed(exc, "NHL_SCORING_FAILED") from exc
        p_home = float(package["calibrated_home_probability"])
        features = {str(k): float(v) for k, v in recon.features.items()}
        digest = hashlib.sha256(json.dumps({"timeline_hash": recon.timeline_hash,
                                            "prior_event_count": recon.prior_event_count},
                                           sort_keys=True).encode()).hexdigest()
        return Forecast(probabilities={"HOME": p_home, "AWAY": 1.0 - p_home}, features=features,
                        source_history_digest=digest)

    def settlements(self, db: Any, pending: Sequence[Mapping[str, Any]]) -> Sequence[Mapping[str, Any]]:
        if not pending:
            return []
        starts = {str(row["official_event_id"]): _aware(row["event_start_time_utc"]) for row in pending}
        finals = _rows(
            db.table("wow_d1_source_events")
            .select("official_event_id,event_start_time,home_participant,away_participant,outcome_json,"
                    "source_provider,source_retrieved_at")
            .eq("sport", "NHL").in_("official_event_id", sorted(starts)).execute()
        )
        out: list[dict[str, Any]] = []
        for final in finals:
            event_id = str(final.get("official_event_id") or "")
            start = starts.get(event_id)
            outcome = final.get("outcome_json")
            if start is None or not isinstance(outcome, Mapping):
                continue
            hs, as_ = outcome.get("home_score"), outcome.get("away_score")
            if str(outcome.get("game_state") or "").upper() not in ("OFF", "FINAL") or type(hs) is not int or type(as_) is not int:
                continue
            if final.get("source_provider") != self.source:
                out.append({"blocked_code": "NHL_SETTLEMENT_SOURCE_UNVERIFIED"})
                continue
            if abs(_aware(final.get("event_start_time")) - start) > START_MATCH_TOLERANCE or hs == as_:
                out.append({"blocked_code": "NHL_SETTLEMENT_INCONSISTENT"})
                continue
            out.append({
                "official_event_id": event_id,
                "event_start_time_utc": start.isoformat(),
                "outcome": "HOME" if hs > as_ else "AWAY",
                "home_score": hs,
                "away_score": as_,
                "settlement_source": self.source,
                "source_retrieved_at": _aware(final.get("source_retrieved_at")).isoformat(),
            })
        return out


def nhl_forward_evidence_after_maintenance(db: Any) -> dict[str, Any]:
    from v17.forward_evidence import run_forward_evidence

    return run_forward_evidence(db, NHLRegularSeasonAdapter())


# --- NCAAB moneyline (SportsDataverse ESPN team box, CC BY 4.0) --------------

class NCAABResultFormAdapter:
    """NCAA men's basketball winner with the result-form research candidate.

    Fixtures, history and finals all come from the SportsDataverse release the
    candidate trains on: the current-season schedule (ESPN ids, UTC start)
    and validated team-box pairs. Features reuse the training module's own
    ``_history_entry`` and ``_summary``; calibration reuses the binary
    lifecycle's ``_map_calibrator``.
    """

    sport = "NCAAB"
    league = "NCAAB"
    market_family = "OUTRIGHT_WINNER"
    outcome_space = "BINARY_HOME_AWAY"
    source = "SPORTSDATAVERSE:espn_mens_college_basketball"
    schedule_url = ("https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
                    "espn_mens_college_basketball_schedules/mbb_schedule_{season}.csv")

    def __init__(self, fetch_schedule: Any = None, fetch_games: Any = None):
        from v17 import ncaab_sportsdataverse_candidate as ncaab

        self._ncaab = ncaab
        self.model_family = ncaab.MODEL_FAMILY
        self._fetch_schedule = fetch_schedule or self._download_schedule
        self._fetch_games = fetch_games or (lambda seasons: ncaab.fetch_games(seasons=seasons)[0])

    def _download_schedule(self, season: int) -> list[Mapping[str, Any]]:
        import csv
        import io
        import requests

        response = requests.get(self.schedule_url.format(season=season), timeout=60)
        if response.status_code != 200:
            raise ForwardEvidenceBlocked(f"NCAAB_SCHEDULE_HTTP_{response.status_code}")
        return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig", errors="replace"))))

    @staticmethod
    def season_for(now: datetime) -> int:
        # SportsDataverse names a season for the year it ends.
        return now.year + 1 if now.month >= 7 else now.year

    def _games(self, now: datetime) -> list[Mapping[str, Any]]:
        season = self.season_for(now)
        try:
            return list(self._fetch_games((season - 1, season)))
        except ForwardEvidenceBlocked:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ForwardEvidenceBlocked(str(getattr(exc, "code", "") or "NCAAB_HISTORY_SOURCE_UNAVAILABLE")) from exc

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

        ncaab = self._ncaab
        names = list(ncaab.FEATURE_NAMES)
        ok = (candidate.get("sport") == self.sport and candidate.get("league") == self.league
              and candidate.get("market_family") == self.market_family
              and candidate.get("model_family") == self.model_family
              and candidate.get("feature_schema_version") == ncaab.FEATURE_SCHEMA_VERSION
              and candidate.get("source_policy_id") == ncaab.SOURCE_POLICY_ID
              and candidate.get("lifecycle_state") == "CANDIDATE"
              and candidate.get("research_screen_pass") is True
              and all(candidate.get(k) is False for k in ("active", "promoted", "probability_publishable",
                                                          "can_execute", "automatic_certification",
                                                          "automatic_promotion")))
        if not ok:
            raise ForwardEvidenceBlocked("NCAAB_CANDIDATE_LIFECYCLE_INVALID")
        if _aware(candidate.get("created_at")) > now:
            raise ForwardEvidenceBlocked("NCAAB_CANDIDATE_POST_AS_OF")
        artifact, cal = candidate.get("artifact_payload"), candidate.get("calibrator_payload")
        if not isinstance(artifact, Mapping) or ncaab._json_hash(artifact) != candidate.get("artifact_checksum"):
            raise ForwardEvidenceBlocked("NCAAB_ARTIFACT_CHECKSUM_MISMATCH")
        try:
            shapes_ok = (
                artifact.get("artifact_format") == "STANDARDIZED_LOGISTIC_JSON_V1"
                and artifact.get("model_family") == self.model_family
                and list(artifact.get("feature_names") or []) == names
                and len(artifact["coefficients"]) == len(names) == len(artifact["scaler_mean"]) == len(artifact["scaler_scale"])
                and all(float(s) > 0 for s in artifact["scaler_scale"])
                and all(isfinite(float(v)) for v in [*artifact["coefficients"], artifact["intercept"]])
                and isinstance(cal, Mapping)
                and cal.get("method") in ("IDENTITY_RAW_PROBABILITY_V1", "FORWARD_SELECTED_IDENTITY_OR_EMPIRICAL_WILSON_V2",
                                          "EMPIRICAL_WILSON_BINS_V1")
                and (cal.get("method") == "IDENTITY_RAW_PROBABILITY_V1" or bool(cal.get("bins")))
            )
        except (KeyError, TypeError, ValueError):
            shapes_ok = False
        if not shapes_ok:
            raise ForwardEvidenceBlocked("NCAAB_ARTIFACT_SCHEMA_INVALID")

    def upcoming_events(self, db: Any, now: datetime, horizon: timedelta) -> Sequence[EventIdentity]:
        try:
            rows = self._fetch_schedule(self.season_for(now))
        except ForwardEvidenceBlocked:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ForwardEvidenceBlocked("NCAAB_SCHEDULE_SOURCE_UNAVAILABLE") from exc
        found: dict[str, EventIdentity | None] = {}
        for row in rows:
            event_id = str(row.get("id") or row.get("game_id") or "").strip()
            home, away = str(row.get("home_id") or "").strip(), str(row.get("away_id") or "").strip()
            if (not event_id or not home or not away or home == away
                    or str(row.get("time_valid") or "").lower() != "true"
                    or str(row.get("status_type_completed") or "").lower() == "true"):
                continue
            try:
                start = _aware(str(row.get("start_date") or "").replace("Z", "+00:00"))
            except Exception:  # noqa: BLE001
                continue
            if not now < start <= now + horizon:
                continue
            neutral = str(row.get("neutral_site") or "").lower()
            if event_id in found:
                found[event_id] = None
                continue
            found[event_id] = EventIdentity(event_id, start, home, away, self.source,
                                            payload={"neutral_site": {"true": True, "false": False}.get(neutral)})
        return sorted((e for e in found.values() if e is not None), key=lambda e: e.event_start_time_utc)

    def load_context(self, db: Any, now: datetime) -> dict[str, list[dict[str, Any]]]:
        ncaab = self._ncaab
        histories: dict[str, list[dict[str, Any]]] = {}
        for game in sorted(self._games(now), key=lambda g: (g["event_start"], g["game_id"])):
            start = _aware(game["event_start"])
            if start >= now:
                continue
            histories.setdefault(str(game["home_team_id"]), []).append(
                ncaab._history_entry(game["home"], game["away"], game_id=str(game["game_id"]), start=start))
            histories.setdefault(str(game["away_team_id"]), []).append(
                ncaab._history_entry(game["away"], game["home"], game_id=str(game["game_id"]), start=start))
        return histories

    def score(self, event: EventIdentity, context: Any, candidate: Mapping[str, Any], now: datetime) -> Forecast:
        import math

        import numpy as np
        from v17.binary_candidate_lifecycle import _map_calibrator

        ncaab = self._ncaab
        hs = ncaab._summary(context.get(event.home_participant, []), event.event_start_time_utc)
        aws = ncaab._summary(context.get(event.away_participant, []), event.event_start_time_utc)
        if hs is None or aws is None:
            raise ForwardEvidenceBlocked("NCAAB_PRIOR_SAMPLE_INSUFFICIENT")
        features = {}
        for side, s in (("home", hs), ("away", aws)):
            features[f"{side}_recent_win_rate"] = s["win_rate"]
            features[f"{side}_recent_point_diff"] = s["point_diff"]
            features[f"{side}_recent_points_for"] = s["points_for"]
            features[f"{side}_recent_rebound_margin_proxy"] = s["rebound_margin_proxy"]
            features[f"{side}_recent_turnovers"] = s["turnovers"]
            features[f"{side}_recent_fg_pct"] = s["fg_pct"]
            features[f"{side}_recent_3p_pct"] = s["three_pct"]
            features[f"{side}_games_prior_log"] = s["games_prior_log"]
            features[f"{side}_rest_days_capped"] = s["rest_days_capped"]
        artifact = candidate["artifact_payload"]
        z = float(artifact["intercept"])
        for i, name in enumerate(ncaab.FEATURE_NAMES):
            z += float(artifact["coefficients"][i]) * (
                (float(features[name]) - float(artifact["scaler_mean"][i])) / float(artifact["scaler_scale"][i]))
        raw = 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))
        p_home = float(_map_calibrator(np.asarray([raw]), candidate["calibrator_payload"])[0])
        digest = ncaab._json_hash({"home": hs["prior_event_ids"], "away": aws["prior_event_ids"]})
        return Forecast(probabilities={"HOME": p_home, "AWAY": 1.0 - p_home}, features=features,
                        source_history_digest=digest)

    def settlements(self, db: Any, pending: Sequence[Mapping[str, Any]]) -> Sequence[Mapping[str, Any]]:
        if not pending:
            return []
        wanted = {str(row["official_event_id"]): _aware(row["event_start_time_utc"]) for row in pending}
        now = datetime.now(timezone.utc)
        out: list[dict[str, Any]] = []
        for game in self._games(now):
            event_id = str(game.get("game_id") or "")
            start = wanted.get(event_id)
            if start is None:
                continue
            hs, as_ = game.get("home_score"), game.get("away_score")
            if abs(_aware(game["event_start"]) - start) > START_MATCH_TOLERANCE or type(hs) is not int or hs == as_:
                out.append({"blocked_code": "NCAAB_SETTLEMENT_INCONSISTENT"})
                continue
            out.append({
                "official_event_id": event_id,
                "event_start_time_utc": start.isoformat(),
                "outcome": "HOME" if hs > as_ else "AWAY",
                "home_score": hs,
                "away_score": as_,
                "settlement_source": self.source,
                "source_retrieved_at": now.isoformat(),
            })
        return out


def ncaab_forward_evidence_after_maintenance(db: Any) -> dict[str, Any]:
    from v17.forward_evidence import run_forward_evidence

    return run_forward_evidence(db, NCAABResultFormAdapter())
