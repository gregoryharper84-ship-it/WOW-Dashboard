"""Append-only NHL provenance re-ingest and genuine 2026-27 replay proof."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

import nhl_candidate_pipeline as pipeline
import v17.nhl_fitted_specialist as fitted
import v17.nhl_source_reingest as reingest
from v17.d1_candidate_registry import D1RegistryError, source_outcome_json

FIXTURE = Path(__file__).parent / "fixtures" / "nhl_2026_27_real_timeline.json"
CANDIDATE = Path(__file__).parent / "fixtures" / "nhl_fitted_candidate_20261107.json"
SEASON = "20262027"
INGEST_SHA = "a" * 40


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _candidate_row() -> dict:
    return json.loads(CANDIDATE.read_text(encoding="utf-8"))


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table_name = table
        self.filters: list[tuple[str, object]] = []
        self.orders: list[tuple[str, bool]] = []
        self.limit_n: int | None = None
        self.range_: tuple[int, int] | None = None
        self.mode = "select"
        self.payload: list[dict] = []
        self.on_conflict = ""
        self.ignore_duplicates = False

    def select(self, *args):
        self.mode = "select"
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def order(self, field, desc=False):
        self.orders.append((field, bool(desc)))
        return self

    def limit(self, value):
        self.limit_n = int(value)
        return self

    def range(self, start, end):
        self.range_ = (int(start), int(end))
        return self

    def upsert(self, payload, *, on_conflict=None, ignore_duplicates=False):
        self.mode = "upsert"
        self.payload = [dict(item) for item in payload]
        self.on_conflict = str(on_conflict or "")
        self.ignore_duplicates = bool(ignore_duplicates)
        return self

    def execute(self):
        rows = self.db.rows.setdefault(self.table_name, [])
        if self.mode == "upsert":
            with self.db.lock:
                keys = tuple(key for key in self.on_conflict.split(",") if key)
                inserted: list[dict] = []
                for payload in self.payload:
                    if payload.get("official_event_id") in self.db.ignored_event_ids:
                        continue
                    if (
                        self.db.fail_after_upserts is not None
                        and self.db.upsert_attempts >= self.db.fail_after_upserts
                    ):
                        raise RuntimeError("simulated write failure")
                    duplicate = any(
                        all(existing.get(key) == payload.get(key) for key in keys)
                        for existing in rows
                    ) if keys else False
                    if duplicate and self.ignore_duplicates:
                        continue
                    row = dict(payload)
                    row.setdefault("source_event_id", f"source-{len(rows)}")
                    if self.table_name == fitted.CANDIDATE_TABLE:
                        row.setdefault("candidate_id", f"candidate-{len(rows)}")
                    rows.append(row)
                    inserted.append(row)
                self.db.upsert_attempts += 1
                self.db.operations.append((self.table_name, "upsert", len(inserted)))
                return SimpleNamespace(data=inserted)

        matched = [
            row for row in rows
            if all(row.get(key) == value for key, value in self.filters)
        ]
        for field, desc in reversed(self.orders):
            matched.sort(key=lambda row: str(row.get(field) or ""), reverse=desc)
        if self.range_ is not None:
            matched = matched[self.range_[0]: self.range_[1] + 1]
        if self.limit_n is not None:
            matched = matched[: self.limit_n]
        self.db.operations.append((self.table_name, "select", len(matched)))
        return SimpleNamespace(data=matched)


class _FakeDB:
    def __init__(self):
        self.rows: dict[str, list[dict]] = {}
        self.operations: list[tuple[str, str, int]] = []
        self.fail_after_upserts: int | None = None
        self.upsert_attempts = 0
        self.ignored_event_ids: set[str] = set()
        self.lock = threading.Lock()

    def table(self, name):
        return _Query(self, name)


class _Response:
    def __init__(self, body):
        self.status_code = 200
        self._body = body

    def json(self):
        return self._body


class _Session:
    def __init__(self, by_team):
        self.by_team = by_team
        self.calls: list[str] = []

    def get(self, url, timeout=20, headers=None):
        self.calls.append(url)
        team = url.rstrip("/").rsplit("/", 2)[-2]
        return _Response({"games": self.by_team.get(team, [])})


def _by_team(fixture: dict) -> dict[str, list[dict]]:
    mapping: dict[str, list[dict]] = {}
    for game in fixture["games"]:
        for side in ("homeTeam", "awayTeam"):
            team = game[side]["abbrev"]
            mapping.setdefault(team, []).append(game)
    return mapping


def _normalized_games(fixture: dict) -> list[pipeline.NHLGame]:
    by_id: dict[str, pipeline.NHLGame] = {}
    for raw in fixture["games"]:
        home = raw["homeTeam"]["abbrev"]
        uri = f"https://api-web.nhle.com/v1/club-schedule-season/{home}/{SEASON}"
        game = pipeline.normalize_game(
            raw, source_uri=uri, retrieved_at="2026-11-07T00:00:00+00:00"
        )
        assert game is not None
        by_id[game.game_id] = game
    return [
        by_id[key]
        for key in sorted(by_id, key=lambda item: (by_id[item].event_start_time, item))
    ]


def _run(db=None, session=None, *, dry_run=False, teams=("BOS", "PIT")):
    return reingest.run_nhl_source_reingest(
        db or _FakeDB(),
        season=int(SEASON),
        teams=teams,
        session=session or _Session(_by_team(_fixture())),
        ingest_code_sha=INGEST_SHA,
        dry_run=dry_run,
    )


def _legacy_row(game: pipeline.NHLGame) -> dict:
    """A pre-V2 source row as it exists historically in the shared table."""
    return {
        "sport": "NHL",
        "league": "NHL",
        "official_event_id": game.game_id,
        "season": str(game.season_id),
        "event_start_time": game.event_start_time,
        "home_participant": game.home_team,
        "away_participant": game.away_team,
        "outcome_json": {
            "home_score": game.home_score,
            "away_score": game.away_score,
            "positive_outcome": game.home_score > game.away_score,
        },
        "source_provider": pipeline.SOURCE_ID,
        "source_uri": game.source_uri,
        "source_retrieved_at": game.source_retrieved_at,
        "source_payload_sha256": game.source_payload_sha256,
        "historical_reconstruction": True,
        "can_execute": False,
    }


def _target_start() -> datetime:
    fixture = _fixture()
    target = next(
        game for game in fixture["games"]
        if str(game["id"]) == str(fixture["target_event_id"])
    )
    return datetime.fromisoformat(str(target["startTimeUTC"]).replace("Z", "+00:00"))


def _reconstruct_target(rows: list[dict]):
    fixture = _fixture()
    target = next(
        game for game in fixture["games"]
        if str(game["id"]) == str(fixture["target_event_id"])
    )
    return fitted.reconstruct_nhl_pregame_features(
        rows,
        official_event_id=str(target["id"]),
        event_start_time=target["startTimeUTC"],
        home_team=target["homeTeam"]["abbrev"],
        away_team=target["awayTeam"]["abbrev"],
        season=SEASON,
        neutral_site=bool(target["neutralSite"]),
    )


def test_reingest_appends_genuine_2026_27_provenance_idempotently():
    fixture = _fixture()
    db = _FakeDB()
    db.rows[fitted.CANDIDATE_TABLE] = [_candidate_row()]
    first = _run(db)
    assert first["status"] == "NHL_SOURCE_REINGEST_PERSISTED"
    assert first["source_event_n"] == len(fixture["games"]) == 32
    assert first["distinct_source_event_n"] == 32
    assert first["provenanced_event_n"] == 32
    assert first["provenance_complete"] is True
    assert first["blockers"] == []
    assert first["can_execute"] is False

    rows = db.rows["wow_d1_source_events"]
    assert len(rows) == 32
    for row in rows:
        outcome = row["outcome_json"]
        assert outcome["game_type"] == 2
        assert type(outcome["neutral_site"]) is bool
        assert outcome["game_state"] in {"FINAL", "OFF"}
        assert outcome["provenance_version"] == pipeline.SOURCE_PROVENANCE_VERSION
        assert outcome["ingest_code_sha"] == INGEST_SHA
        assert outcome["payload_hash_scope"] == pipeline.SOURCE_PAYLOAD_HASH_SCOPE
        assert len(outcome["source_payload_commitment"]) == 64
        assert row["source_payload_sha256"] == pipeline.provenance_envelope_hash(
            official_event_id=row["official_event_id"],
            season=row["season"],
            event_start_time=row["event_start_time"],
            home_team=row["home_participant"],
            away_team=row["away_participant"],
            home_score=outcome["home_score"],
            away_score=outcome["away_score"],
            game_type=outcome["game_type"],
            game_state=outcome["game_state"],
            neutral_site=outcome["neutral_site"],
        )
        assert row["sport"] == "NHL" and row["league"] == "NHL"
        assert row["can_execute"] is False
    assert all(mode in {"upsert", "select"} for _, mode, _ in db.operations)

    second = _run(db)
    assert second["status"] == "NHL_SOURCE_REINGEST_PERSISTED"
    assert second["provenance_complete"] is True
    assert len(db.rows["wow_d1_source_events"]) == 32


def test_reingest_dry_run_does_not_write():
    db = _FakeDB()
    result = _run(db, dry_run=True)
    assert result["status"] == "NHL_SOURCE_REINGEST_DRY_RUN_READY"
    assert result["source_event_n"] == 32
    assert db.rows.get("wow_d1_source_events", []) == []
    assert all(mode == "select" for _, mode, _ in db.operations)
    assert db.upsert_attempts == 0


def test_reingest_treats_final_and_off_as_one_settled_identity():
    fixture = _fixture()
    db = _FakeDB()
    first = _run(db)
    assert first["status"] == "NHL_SOURCE_REINGEST_PERSISTED"
    rows = db.rows["wow_d1_source_events"]
    assert len(rows) == 32
    original_states = {
        row["official_event_id"]: row["outcome_json"]["game_state"] for row in rows
    }

    # The official lifecycle can report the same completed game as FINAL and
    # later as OFF. Both are settled, so a later re-ingest must not append a
    # second provenance identity for the event nor rewrite the first row.
    flipped = {"games": []}
    for game in fixture["games"]:
        copy = dict(game)
        copy["gameState"] = "OFF" if game["gameState"] == "FINAL" else "FINAL"
        flipped["games"].append(copy)

    second = _run(db, session=_Session(_by_team(flipped)))
    assert second["status"] == "NHL_SOURCE_REINGEST_PERSISTED"
    assert second["provenance_complete"] is True
    assert len(db.rows["wow_d1_source_events"]) == 32
    assert {
        row["official_event_id"]: row["outcome_json"]["game_state"]
        for row in db.rows["wow_d1_source_events"]
    } == original_states


def test_strict_normalization_rejects_malformed_start_time_as_typed_block():
    raw = dict(_fixture()["games"][0])
    raw["startTimeUTC"] = "not-a-date"
    uri = f"https://api-web.nhle.com/v1/club-schedule-season/BOS/{SEASON}"
    assert pipeline.normalize_game(
        raw, source_uri=uri, retrieved_at="2026-11-07T00:00:00+00:00"
    ) is None
    with pytest.raises(pipeline.NHLCandidateError) as caught:
        pipeline.normalize_game(
            raw,
            source_uri=uri,
            retrieved_at="2026-11-07T00:00:00+00:00",
            strict=True,
        )
    assert caught.value.code == "NHL_SOURCE_EVENT_TIME_INVALID"


def test_reingest_blocks_when_the_official_source_has_no_settled_games():
    db = _FakeDB()
    result = _run(db, session=_Session({}))
    assert result["status"] == "BLOCKED"
    assert result["code"] == "NHL_REINGEST_NO_SETTLED_GAMES"
    assert result["can_execute"] is False
    assert db.rows.get("wow_d1_source_events", []) == []


def test_source_outcome_rejects_missing_or_unscoped_v2_commitment():
    base = {
        "official_event_id": "2026020257",
        "home_score": 2,
        "away_score": 6,
        "positive_outcome": False,
        "provenance_version": pipeline.SOURCE_PROVENANCE_VERSION,
        "game_type": 2,
        "neutral_site": False,
        "game_state": "FINAL",
        "ingest_code_sha": INGEST_SHA,
        "payload_hash_scope": pipeline.SOURCE_PAYLOAD_HASH_SCOPE,
    }
    # Missing commitment is incomplete V2, not a malformed commitment value.
    with pytest.raises(D1RegistryError) as caught:
        source_outcome_json(base)
    assert caught.value.code == "D1_SOURCE_PROVENANCE_INCOMPLETE"

    with pytest.raises(D1RegistryError) as caught:
        source_outcome_json(
            {**base, "source_payload_commitment": "zz" * 32}
        )
    assert caught.value.code == "D1_SOURCE_PAYLOAD_COMMITMENT_INVALID"

    with pytest.raises(D1RegistryError) as caught:
        source_outcome_json(
            {
                **base,
                "source_payload_commitment": "a" * 64,
                "payload_hash_scope": "WRONG_SCOPE",
            }
        )
    assert caught.value.code == "D1_SOURCE_PAYLOAD_HASH_SCOPE_INVALID"

    with pytest.raises(D1RegistryError) as caught:
        source_outcome_json(
            {
                **base,
                "source_payload_commitment": "a" * 64,
                "ingest_code_sha": "not-a-sha",
            }
        )
    assert caught.value.code == "D1_SOURCE_INGEST_CODE_SHA_INVALID"


def test_source_outcome_rejects_partial_v2_provenance():
    with pytest.raises(D1RegistryError) as caught:
        source_outcome_json(
            {
                "official_event_id": "2026020257",
                "home_score": 2,
                "away_score": 6,
                "positive_outcome": False,
                "provenance_version": pipeline.SOURCE_PROVENANCE_VERSION,
                "neutral_site": False,
                "game_state": "FINAL",
            }
        )
    assert caught.value.code == "D1_SOURCE_PROVENANCE_INCOMPLETE"

    # Any lone V2 field (without provenance_version) also requires the full set.
    with pytest.raises(D1RegistryError) as caught:
        source_outcome_json(
            {
                "official_event_id": "2026020257",
                "home_score": 2,
                "away_score": 6,
                "positive_outcome": False,
                "game_type": 2,
            }
        )
    assert caught.value.code == "D1_SOURCE_PROVENANCE_INCOMPLETE"


def test_reingest_rows_reject_non_regular_season_games():
    games = _normalized_games(_fixture())
    with pytest.raises(reingest.NHLSourceReingestError) as caught:
        reingest.build_reingest_rows(
            [replace(games[0], game_type=1)], ingest_code_sha=INGEST_SHA
        )
    assert caught.value.code == "NHL_SOURCE_GAME_TYPE_UNVERIFIED"


def test_genuine_2026_27_event_replay_matches_training_and_scores():
    fixture = _fixture()
    target_id = str(fixture["target_event_id"])
    raw_by_id = {str(game["id"]): game for game in fixture["games"]}
    target = raw_by_id[target_id]
    target_start = target["startTimeUTC"]
    home = target["homeTeam"]["abbrev"]
    away = target["awayTeam"]["abbrev"]
    assert home == "PIT" and away == "BOS"

    games = _normalized_games(fixture)
    db = _FakeDB()
    db.rows[fitted.CANDIDATE_TABLE] = [_candidate_row()]
    ingested = _run(db)
    assert ingested["status"] == "NHL_SOURCE_REINGEST_PERSISTED"
    assert ingested["provenance_complete"] is True
    target_start_dt = datetime.fromisoformat(target_start.replace("Z", "+00:00"))
    prior = [
        row for row in db.rows["wow_d1_source_events"]
        if datetime.fromisoformat(row["event_start_time"].replace("Z", "+00:00"))
        < target_start_dt
    ]
    assert len(prior) == len(games) - 1 == 31

    reconstruction = fitted.reconstruct_nhl_pregame_features(
        prior,
        official_event_id=target_id,
        event_start_time=target_start,
        home_team=home,
        away_team=away,
        season=SEASON,
        neutral_site=bool(target["neutralSite"]),
    )
    _, feature_records = pipeline.reconstruct_training_rows(games)
    training = next(
        record for record in feature_records
        if record["official_event_id"] == target_id
    )
    assert reconstruction.features == pytest.approx(
        training["features"], rel=0, abs=1e-12
    )
    home_prior = sum(
        1 for row in prior
        if home in {row["home_participant"], row["away_participant"]}
    )
    away_prior = sum(
        1 for row in prior
        if away in {row["home_participant"], row["away_participant"]}
    )
    assert reconstruction.home_prior_games == home_prior == 15
    assert reconstruction.away_prior_games == away_prior == 16
    assert reconstruction.prior_event_count == 31

    candidate = fitted.load_nhl_fitted_candidate(
        db, as_of_utc=datetime(2026, 11, 19, tzinfo=timezone.utc)
    )
    package = fitted.score_nhl_fitted_candidate(
        candidate, reconstruction, event_key=f"NHL:{target_id}", evidence={}
    )
    assert package["model_version"] == "NHL_RS_LOGISTIC_V1-5528ab8891df79fd-372bef19a04a"
    assert 0.0 < package["calibrated_probability"] < 1.0
    assert package["selected_participant"] in {home, away}
    assert package["official_event_id"] == target_id
    assert package["calibrated_lower_bound"] is None
    assert "NHL_CALIBRATED_LOWER_BOUND_UNAVAILABLE" in package["blockers"]
    assert package["probability_publishable"] is False
    assert package["rank_eligible"] is False
    assert package["can_execute"] is False


def test_reingest_route_install_is_idempotent():
    app = FastAPI()
    reingest.install_nhl_source_reingest_route(
        app, auth_dependency=lambda: None, db_client_fn=_FakeDB
    )
    reingest.install_nhl_source_reingest_route(
        app, auth_dependency=lambda: None, db_client_fn=_FakeDB
    )
    paths = [route.path for route in app.router.routes]
    assert paths.count("/internal/v17/nhl-source-reingest") == 1
    route = next(
        route for route in app.router.routes
        if getattr(route, "path", None) == "/internal/v17/nhl-source-reingest"
    )
    assert route.methods == {"POST"}


def test_reingest_avoids_legacy_hash_collision_and_replays_all_events():
    games = _normalized_games(_fixture())
    legacy_game = games[0]
    db = _FakeDB()
    db.rows[fitted.CANDIDATE_TABLE] = [_candidate_row()]
    db.rows["wow_d1_source_events"] = [_legacy_row(legacy_game)]

    result = _run(db)
    assert result["status"] == "NHL_SOURCE_REINGEST_PERSISTED"
    assert result["provenance_complete"] is True

    rows = db.rows["wow_d1_source_events"]
    assert len(rows) == 33  # one immutable legacy row plus 32 V2 rows
    matched = [
        row for row in rows
        if row["official_event_id"] == legacy_game.game_id
    ]
    assert len(matched) == 2
    v2 = next(
        row for row in matched
        if row["outcome_json"].get("provenance_version") == pipeline.SOURCE_PROVENANCE_VERSION
    )
    assert v2["source_payload_sha256"] != legacy_game.source_payload_sha256
    assert v2["source_payload_sha256"] == pipeline.provenance_envelope_hash(
        official_event_id=v2["official_event_id"],
        season=v2["season"],
        event_start_time=v2["event_start_time"],
        home_team=v2["home_participant"],
        away_team=v2["away_participant"],
        home_score=v2["outcome_json"]["home_score"],
        away_score=v2["outcome_json"]["away_score"],
        game_type=v2["outcome_json"]["game_type"],
        game_state=v2["outcome_json"]["game_state"],
        neutral_site=v2["outcome_json"]["neutral_site"],
    )
    assert v2["outcome_json"]["source_payload_commitment"] == legacy_game.source_payload_sha256

    prior = [
        row for row in rows
        if datetime.fromisoformat(row["event_start_time"].replace("Z", "+00:00"))
        < _target_start()
    ]
    assert len(prior) == 32
    reconstruction = _reconstruct_target(prior)
    assert reconstruction.prior_event_count == 31

    second = _run(db)
    assert second["provenance_complete"] is True
    assert len(db.rows["wow_d1_source_events"]) == 33


def test_reingest_exact_hash_collision_fails_closed_without_overwrite():
    games = _normalized_games(_fixture())
    planned = reingest.build_reingest_rows(games, ingest_code_sha=INGEST_SHA)
    first = planned[0]
    legacy = _legacy_row(games[0])
    legacy["source_payload_sha256"] = first["source_payload_sha256"]
    db = _FakeDB()
    db.rows["wow_d1_source_events"] = [legacy]

    result = _run(db)
    assert result["status"] == "NHL_SOURCE_REINGEST_PROVENANCE_INCOMPLETE"
    assert result["provenance_complete"] is False
    assert "NHL_SOURCE_PROVENANCE_INCOMPLETE" in result["blockers"]
    rows = db.rows["wow_d1_source_events"]
    assert len(rows) == 32
    assert rows[0] == legacy
    assert len([
        row for row in rows
        if row["official_event_id"] == legacy["official_event_id"]
    ]) == 1


def test_reingest_rejects_conflicting_v2_rows_for_the_same_event():
    games = _normalized_games(_fixture())
    conflicted = replace(games[0], home_score=games[0].home_score + 1)
    with pytest.raises(reingest.NHLSourceReingestError) as caught:
        reingest.build_reingest_rows([games[0], conflicted], ingest_code_sha=INGEST_SHA)
    assert caught.value.code == "NHL_INGEST_EVENT_IDENTITY_CONFLICT"


def test_reingest_rejects_invalid_neutral_site_evidence():
    games = _normalized_games(_fixture())
    with pytest.raises(reingest.NHLSourceReingestError) as caught:
        reingest.build_reingest_rows(
            [replace(games[0], neutral_site=None)], ingest_code_sha=INGEST_SHA
        )
    assert caught.value.code == "NHL_SOURCE_NEUTRAL_SITE_UNVERIFIED"


@pytest.mark.parametrize(
    ("field", "expected_code"),
    [("score", "NHL_SOURCE_SCORE_INVALID"), ("id", "NHL_SOURCE_EVENT_INVALID")],
)
def test_reingest_blocks_when_an_eligible_game_fails_normalization(
    field, expected_code
):
    fixture = _fixture()
    game = next(
        raw for raw in fixture["games"]
        if raw["homeTeam"]["abbrev"] in {"BOS", "PIT"}
        or raw["awayTeam"]["abbrev"] in {"BOS", "PIT"}
    )
    if field == "score":
        game["homeTeam"]["score"] = None
    else:
        game["id"] = ""

    result = _run(session=_Session(_by_team(fixture)), teams=("BOS", "PIT"))

    assert result["status"] == "BLOCKED"
    assert result["code"] == expected_code


def test_strict_normalization_still_excludes_unsettled_and_non_regular_games():
    raw = json.loads(json.dumps(_fixture()["games"][0]))
    raw["gameType"] = 3
    assert pipeline.normalize_game(
        raw, source_uri="u", retrieved_at="2026-11-07T00:00:00+00:00", strict=True
    ) is None

    raw = json.loads(json.dumps(_fixture()["games"][0]))
    raw["gameState"] = "FUT"
    assert pipeline.normalize_game(
        raw, source_uri="u", retrieved_at="2026-11-07T00:00:00+00:00", strict=True
    ) is None


@pytest.mark.parametrize("raw_neutral", ["__missing__", None, "false", 0, 1])
def test_reingest_rejects_coerced_raw_neutral_site_evidence(raw_neutral):
    raw = json.loads(json.dumps(_fixture()["games"][0]))
    if raw_neutral == "__missing__":
        raw.pop("neutralSite", None)
    else:
        raw["neutralSite"] = raw_neutral
    game = pipeline.normalize_game(
        raw,
        source_uri=(
            "https://api-web.nhle.com/v1/club-schedule-season/"
            f"{raw['homeTeam']['abbrev']}/{SEASON}"
        ),
        retrieved_at="2026-11-07T00:00:00+00:00",
    )
    assert game is not None
    with pytest.raises(reingest.NHLSourceReingestError) as caught:
        reingest.build_reingest_rows([game], ingest_code_sha=INGEST_SHA)
    assert caught.value.code == "NHL_SOURCE_NEUTRAL_SITE_UNVERIFIED"


def test_reingest_retry_recovers_from_a_partial_write(monkeypatch):
    import v17.d1_bulk_candidate_registry as bulk

    monkeypatch.setattr(
        bulk,
        "_chunks",
        lambda rows, size=250: (
            rows[offset:offset + 10] for offset in range(0, len(rows), 10)
        ),
    )
    db = _FakeDB()
    db.fail_after_upserts = 1
    first = _run(db)
    assert first["status"] == "BLOCKED"
    assert first["code"] == "NHL_SOURCE_PERSISTENCE_FAILED"
    assert first["detail"]["retryable"] is True
    assert first["detail"]["partial_write_possible"] is True
    partial = len(db.rows["wow_d1_source_events"])
    assert 0 < partial < 32

    db.fail_after_upserts = None
    second = _run(db)
    assert second["status"] == "NHL_SOURCE_REINGEST_PERSISTED"
    assert second["provenance_complete"] is True
    rows = db.rows["wow_d1_source_events"]
    assert len(rows) == 32
    assert len({row["official_event_id"] for row in rows}) == 32


def test_reingest_reconciles_an_ignored_source_event_as_incomplete():
    fixture = _fixture()
    db = _FakeDB()
    db.ignored_event_ids = {str(fixture["games"][0]["id"])}
    result = _run(db)
    assert result["status"] == "NHL_SOURCE_REINGEST_PROVENANCE_INCOMPLETE"
    assert result["provenance_complete"] is False
    assert "NHL_SOURCE_PROVENANCE_INCOMPLETE" in result["blockers"]
    assert result["distinct_source_event_n"] == 31


def test_reingest_db_tamper_is_rejected_by_envelope_replay():
    db = _FakeDB()
    assert _run(db)["status"] == "NHL_SOURCE_REINGEST_PERSISTED"
    rows = db.rows["wow_d1_source_events"]
    rows[0]["outcome_json"] = {
        **rows[0]["outcome_json"],
        "home_score": rows[0]["outcome_json"]["home_score"] + 1,
    }
    prior = [
        row for row in rows
        if datetime.fromisoformat(row["event_start_time"].replace("Z", "+00:00"))
        < _target_start()
    ]
    with pytest.raises(fitted.NHLFittedSpecialistError) as caught:
        _reconstruct_target(prior)
    assert "NHL_SOURCE_PAYLOAD_HASH_MISMATCH" in caught.value.blockers


def test_reingest_pre_write_source_failure_leaves_no_partial_rows():
    class _RaisingSession:
        def get(self, *args, **kwargs):
            raise RuntimeError("offline")

    db = _FakeDB()
    result = _run(db, session=_RaisingSession())
    assert result["status"] == "BLOCKED"
    assert result["code"].startswith("NHL_SOURCE_")
    assert db.rows == {}
    assert db.operations == []


def test_concurrent_reingest_attempts_are_idempotent():
    db = _FakeDB()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _run(db), range(2)))
    assert all(
        result["status"] == "NHL_SOURCE_REINGEST_PERSISTED"
        for result in results
    )
    assert all(result["provenance_complete"] is True for result in results)
    rows = db.rows["wow_d1_source_events"]
    assert len(rows) == 32
    assert len({row["official_event_id"] for row in rows}) == 32


def test_duplicate_acquisition_keeps_one_stable_source_identity():
    games = _normalized_games(_fixture())
    forward = reingest.build_reingest_rows(games, ingest_code_sha=INGEST_SHA)
    reverse = reingest.build_reingest_rows(
        list(reversed(games + games)), ingest_code_sha=INGEST_SHA
    )
    assert [(row["official_event_id"], row["source_payload_sha256"]) for row in forward] == [
        (row["official_event_id"], row["source_payload_sha256"]) for row in reverse
    ]
    keys = [
        (row["sport"], row["official_event_id"], row["source_payload_sha256"])
        for row in forward
    ]
    assert len(keys) == len(set(keys)) == 32


def test_internal_reingest_route_rejects_anonymous_requests():
    app = FastAPI()

    def deny():
        raise HTTPException(status_code=401, detail="missing auth")

    db = _FakeDB()
    reingest.install_nhl_source_reingest_route(
        app, auth_dependency=deny, db_client_fn=lambda: db
    )
    with TestClient(app) as client:
        response = client.post(
            "/internal/v17/nhl-source-reingest", params={"dry_run": "true"}
        )
    assert response.status_code == 401
    assert db.rows == {}


def _install_corrected_acquisition(monkeypatch, games, **changes):
    corrected = [
        replace(game, **changes) if game.game_id == games[0].game_id else game
        for game in games
    ]
    monkeypatch.setattr(
        pipeline, "fetch_historical_games", lambda *args, **kwargs: corrected
    )


def test_reingest_blocks_a_corrected_identity_instead_of_appending_a_second_row(
    monkeypatch,
):
    games = _normalized_games(_fixture())
    db = _FakeDB()
    assert _run(db)["status"] == "NHL_SOURCE_REINGEST_PERSISTED"
    persisted = [dict(row) for row in db.rows["wow_d1_source_events"]]

    # A later official read reports a corrected score for a settled game. The
    # corrected envelope is a new unique key, so appending it would leave two
    # immutable identities that the reader must reject for the whole season.
    _install_corrected_acquisition(monkeypatch, games, home_score=games[0].home_score + 1)
    result = _run(db)

    assert result["status"] == "BLOCKED"
    assert result["code"] == "NHL_SOURCE_IDENTITY_CONFLICT"
    assert result["provenance_complete"] is False
    assert result["detail"]["conflicting_event_ids"] == [games[0].game_id]
    assert result["detail"]["retryable"] is False
    assert result["can_execute"] is False
    assert db.rows["wow_d1_source_events"] == persisted


def test_reingest_dry_run_reports_the_identity_conflict_without_writing(monkeypatch):
    games = _normalized_games(_fixture())
    db = _FakeDB()
    assert _run(db)["status"] == "NHL_SOURCE_REINGEST_PERSISTED"
    persisted = [dict(row) for row in db.rows["wow_d1_source_events"]]

    _install_corrected_acquisition(monkeypatch, games, home_score=games[0].home_score + 1)
    result = _run(db, dry_run=True)

    assert result["status"] == "BLOCKED"
    assert result["code"] == "NHL_SOURCE_IDENTITY_CONFLICT"
    assert db.rows["wow_d1_source_events"] == persisted


def test_reingest_never_reports_complete_for_an_already_conflicted_season():
    games = _normalized_games(_fixture())
    db = _FakeDB()
    assert _run(db)["status"] == "NHL_SOURCE_REINGEST_PERSISTED"
    rows = db.rows["wow_d1_source_events"]
    target = games[0].game_id

    forged = dict(next(row for row in rows if row["official_event_id"] == target))
    forged["source_event_id"] = "source-forged-conflict"
    forged["source_payload_sha256"] = "f" * 64
    rows.append(forged)

    result = _run(db)

    assert result["status"] == "BLOCKED"
    assert result["code"] == "NHL_SOURCE_IDENTITY_CONFLICT"
    assert result["detail"]["conflicting_event_ids"] == [target]
    assert len(rows) == 33


@pytest.mark.parametrize("season", [20262027.5, True, "20262027.0", "2026-2027"])
def test_reingest_rejects_inexact_season_evidence(season):
    db = _FakeDB()
    result = reingest.run_nhl_source_reingest(
        db,
        season=season,
        teams=("BOS", "PIT"),
        session=_Session(_by_team(_fixture())),
        ingest_code_sha=INGEST_SHA,
    )
    assert result["status"] == "BLOCKED"
    assert result["code"] == "NHL_SEASON_ID_INVALID"
    assert result["provenance_complete"] is False
    assert db.rows == {}


def test_reingest_detects_a_conflict_that_lands_after_the_preflight(monkeypatch):
    games = _normalized_games(_fixture())
    db = _FakeDB()
    assert _run(db)["status"] == "NHL_SOURCE_REINGEST_PERSISTED"
    rows = db.rows["wow_d1_source_events"]
    target = games[0].game_id

    # A concurrent writer lands a different V2 identity after our preflight read
    # but before our own write, so only the post-write check can see it.
    monkeypatch.setattr(
        reingest, "_persisted_v2_identities", lambda db, *, season, expected: {}
    )
    forged = dict(next(row for row in rows if row["official_event_id"] == target))
    forged["source_event_id"] = "source-concurrent-conflict"
    forged["source_payload_sha256"] = "f" * 64
    rows.append(forged)

    result = _run(db)

    assert result["status"] == "BLOCKED"
    assert result["code"] == "NHL_SOURCE_IDENTITY_CONFLICT"
    assert result["blocked_stage"] == "SOURCE_VERIFICATION"
    assert result["provenance_complete"] is False
    assert result["detail"]["conflicting_event_ids"] == [target]
    assert len(rows) == 33


def test_reingest_maps_an_unexpected_preflight_failure_to_a_typed_block(monkeypatch):
    def offline(db, *, season):
        raise RuntimeError("client offline")

    monkeypatch.setattr(reingest, "load_nhl_season_events", offline)
    db = _FakeDB()
    result = _run(db)

    assert result["status"] == "BLOCKED"
    assert result["code"] == "NHL_SOURCE_IDENTITY_UNVERIFIABLE"
    assert result["detail"]["error_type"] == "RuntimeError"
    assert result["detail"]["retryable"] is True
    assert result["can_execute"] is False
    assert db.rows == {}
