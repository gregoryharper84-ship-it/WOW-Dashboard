"""NBA candidate forward shadow: research-only pre-tipoff capture and grading."""
from __future__ import annotations

import copy
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from basketball_team_event_specialist import TrainingGame
from v17 import nba_candidate_forward_shadow as lane

# The real registered candidate payload (Supabase, 2026-11-21), so the checksum
# recomputation is tested against production bytes, not a self-made fixture.
PAYLOAD = json.loads(r'''{"means": [0.49953907232261135, 0.5009724519751262, -0.01599622139993845, 0.03171914543518049, 1.264586539806912, 1.1707009934238142, 0.14831397789282216, 0.1821743388834476], "sport": "NBA", "scales": [0.13662658607432554, 0.1371534613133793, 4.472701021470238, 4.463689238721373, 1.194565070534506, 1.1612502482594167, 0.3554109478539564, 0.3859881463670176], "intercept": 0.23323235244908472, "coefficients": [0.20853370797592, -0.17426414491983422, 0.2562361384052041, -0.23779395261305308, 0.03975288054525142, -0.05364709428673143, -0.0721274080239274, 0.0964874292706642], "model_family": "BASKETBALL_TEAM_EVENT_LOGISTIC_V1", "feature_names": ["home_win_rate_prior", "away_win_rate_prior", "home_point_diff_prior", "away_point_diff_prior", "home_rest_days_capped", "away_rest_days_capped", "home_back_to_back", "away_back_to_back"], "holdout_brier": 0.22662897425003362, "train_end_date": "2025-03-20", "holdout_accuracy": 0.6188091263216472, "holdout_log_loss": 0.6443152784767228, "train_start_date": "2019-11-01", "artifact_checksum": "4b53def8ac50417c97571afd1e24326ae81409a804908a069a316a239f3c1e2b", "holdout_row_count": 1797, "training_row_count": 7147, "feature_schema_version": "BASKETBALL_TEAM_EVENT_FEATURES_V2", "model_artifact_version": "NBA_TEAM_EVENT_LOGISTIC_V1_20261116T225206Z"}''')
VERSION = PAYLOAD["model_artifact_version"]
CAL = "NBA_TEAM_EVENT_PLATT_V1_20261116T225206Z"
NOW = datetime(2026, 11, 21, 19, 30, tzinfo=timezone.utc)


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.payload, self.start = db, name, [], None, 0
        self.end = 10 ** 9

    def select(self, _cols):
        return self

    def eq(self, key, value):
        self.filters.append((key, lambda r, v=value, k=key: r.get(k) == v))
        return self

    def in_(self, key, values):
        self.filters.append((key, lambda r, v=set(values), k=key: r.get(k) in v))
        return self

    def order(self, key):
        self.key = key
        return self

    def range(self, start, end):
        self.start, self.end = start, end
        return self

    def insert(self, payload):
        self.payload = payload
        return self

    def execute(self):
        if self.name not in self.db.rows:
            raise RuntimeError("relation does not exist")
        if self.payload is not None:
            self.db.rows[self.name].extend(copy.deepcopy(self.payload))
            self.db.inserts += len(self.payload)
            return SimpleNamespace(data=self.payload)
        rows = [r for r in self.db.rows[self.name] if all(f(r) for _, f in self.filters)]
        return SimpleNamespace(data=rows[self.start:self.end + 1])


class _DB:
    def __init__(self, rows):
        self.rows, self.inserts = rows, 0

    def table(self, name):
        return _Q(self, name)


def _game(gid, day, home, away, hs, as_, stored=datetime(2026, 11, 1, tzinfo=timezone.utc)):
    return {"game_id": gid, "season": 2027, "game_date": day, "home_team_id": home, "away_team_id": away,
            "home_score": hs, "away_score": as_, "source_provider": "SPORTSDATAVERSE_ESPN",
            "created_at": stored.isoformat(), "settled": True}


def _history():
    rows = []
    for i in range(6):  # six prior games each for teams 1 and 2
        rows.append(_game(f"espn-90{i}", f"2026-11-0{i + 1}", "espn-1", "espn-3", 110, 100))
        rows.append(_game(f"espn-80{i}", f"2026-11-0{i + 1}", "espn-4", "espn-2", 100, 105))
    # Team 1 played on local 2026-11-20 (a back-to-back for a game the next local day).
    rows.append(_game("espn-777", "2026-11-20", "espn-1", "espn-5", 99, 101))
    return rows


def _db(**extra):
    rows = {
        "wow_basketball_team_event_certification": [{
            "sport": "NBA", "capability_status": "SHADOW", "model_artifact_version": VERSION, "calibrator_version": CAL}],
        "wow_basketball_team_event_model_artifacts": [{
            "sport": "NBA", "model_artifact_version": VERSION, "model_family": PAYLOAD["model_family"],
            "feature_schema_version": PAYLOAD["feature_schema_version"], "artifact_payload": copy.deepcopy(PAYLOAD),
            "artifact_checksum": PAYLOAD["artifact_checksum"]}],
        "wow_basketball_team_event_calibrators": [{
            "sport": "NBA", "calibrator_version": CAL, "model_artifact_version": VERSION,
            "calibration_method": "PLATT_TIME_SPLIT_V1", "coefficients": {"a": -0.0190171411963299, "b": 0.8861012164799363}}],
        "wow_nba_training_games": _history(),
        "wow_nba_candidate_forward_shadow": [],
        "wow_nba_candidate_forward_verified_grades": [],
    }
    rows.update(extra)
    return _DB(rows)


def _sched(gid="401910025", local="2026-11-21", start="2026-11-22T01:00Z", home="1", away="2", completed="false"):
    offset = datetime.fromisoformat(start.replace("Z", "+00:00")) - timedelta(hours=5)
    return {"game_id": gid, "game_date": local, "game_date_time": offset.strftime("%Y-%m-%dT%H:%M:%S.000000-0500"),
            "start_date": start, "home_id": home, "away_id": away, "status_type_name": "STATUS_SCHEDULED",
            "status_type_completed": completed, "_wow_source_observed_at": (NOW - timedelta(minutes=1)).isoformat()}


def test_real_candidate_checksum_verifies_and_tampering_fails():
    candidate = lane.load_candidate(_db())
    assert candidate["model_artifact_version"] == VERSION and candidate["capability_status"] == "SHADOW"
    db = _db()
    db.rows["wow_basketball_team_event_model_artifacts"][0]["artifact_payload"]["intercept"] += 1e-9
    with pytest.raises(lane.NBACandidateForwardShadowError) as exc:
        lane.load_candidate(db)
    assert exc.value.code == "NBA_CANDIDATE_ARTIFACT_INTEGRITY_FAILED"
    assert exc.value.typed_failure == "MODEL_SCORER_FAILED"


@pytest.mark.parametrize("table,code", [
    ("wow_basketball_team_event_certification", "NBA_CANDIDATE_NOT_REGISTERED"),
    ("wow_basketball_team_event_model_artifacts", "NBA_CANDIDATE_ARTIFACT_NOT_FOUND"),
    ("wow_basketball_team_event_calibrators", "NBA_CANDIDATE_CALIBRATOR_NOT_FOUND"),
])
def test_missing_candidate_parts_are_model_unavailable(table, code):
    with pytest.raises(lane.NBACandidateForwardShadowError) as exc:
        lane.load_candidate(_db(**{table: []}))
    assert (exc.value.code, exc.value.typed_failure) == (code, "MODEL_UNAVAILABLE")


def test_unregistered_status_is_not_scored():
    db = _db()
    db.rows["wow_basketball_team_event_certification"][0]["capability_status"] = "REJECTED"
    with pytest.raises(lane.NBACandidateForwardShadowError, match="NBA_CANDIDATE_NOT_REGISTERED"):
        lane.load_candidate(db)


def test_score_is_platt_on_raw_logit_and_in_open_interval():
    candidate = lane.load_candidate(_db())
    values = (0.6, 0.4, 3.0, -2.0, 1.0, 0.0, 0.0, 1.0)
    raw, p = lane.score(candidate, values)
    z = -0.0190171411963299 + 0.8861012164799363 * math.log(raw / (1 - raw))
    assert p == pytest.approx(1 / (1 + math.exp(-z)), abs=1e-15)
    assert 0 < raw < 1 and 0 < p < 1


def test_upcoming_window_keeps_local_date_and_rejects_completed_or_conflicting():
    rows = [
        _sched(),
        _sched(gid="2", start="2026-11-24T01:00Z", local="2026-11-23"),  # beyond 36 h
        _sched(gid="3", completed="true"),
        _sched(gid="4", start="2026-11-21T18:00Z", local="2026-11-21"),  # already started
        dict(_sched(gid="5"), start_date="2026-11-22T02:00Z"),  # local vs UTC start conflict
    ]
    events, holds = lane.upcoming_events(rows, now=NOW)
    assert [e["official_event_id"] for e in events] == ["espn-401910025"]
    assert events[0]["event_game_date"] == date(2026, 11, 21)  # provider local date, not UTC 11-22
    assert holds == {"SCHEDULE_EVENT_AMBIGUOUS": 1, "SCHEDULE_START_CONFLICT": 1}


def test_features_use_local_date_rest_and_exclude_finals_stored_after_cutoff():
    db = _db()
    candidate = lane.load_candidate(db)
    events, _ = lane.upcoming_events([_sched()], now=NOW)
    settled = lane.load_settled_games(db)
    rows, holds = lane.build_capture_rows(candidate, events, settled, knowledge_cutoff=NOW)
    assert holds == {}
    f = rows[0]["features"]
    # Team 1 played local 11-20 and plays local 11-21: back-to-back, rest 0.
    assert f["home_rest_days_capped"] == 0.0 and f["home_back_to_back"] == 1.0
    # A final stored after the cutoff cannot enter the features.
    late = settled + [(TrainingGame("espn-999", "NBA", 2027, date(2026, 11, 20), "espn-2", "espn-6", 90, 120),
                       NOW + timedelta(seconds=1))]
    rows2, _ = lane.build_capture_rows(candidate, events, late, knowledge_cutoff=NOW)
    assert rows2[0]["features"] == f and rows2[0]["dataset_id"] == rows[0]["dataset_id"]
    assert rows[0]["probability_publishable"] is False and rows[0]["can_execute"] is False


def test_insufficient_history_and_post_tipoff_cutoff_hold_typed():
    db = _db(wow_nba_training_games=[])
    candidate = lane.load_candidate(db)
    events, _ = lane.upcoming_events([_sched()], now=NOW)
    assert lane.build_capture_rows(candidate, events, [], knowledge_cutoff=NOW)[1] == {"MODEL_INPUTS_INSUFFICIENT": 1}
    late_cutoff = events[0]["event_start_time_utc"]
    assert lane.build_capture_rows(candidate, events, [], knowledge_cutoff=late_cutoff)[1] == {"NOT_PRE_TIPOFF": 1}


def test_capture_inserts_once_and_reruns_are_idempotent():
    db = _db()
    kwargs = {"schedule_rows_fn": lambda year: [_sched()], "clock": lambda: NOW}
    first = lane.capture(db, **kwargs)
    second = lane.capture(db, **kwargs)
    assert (first["rows_inserted"], second["rows_inserted"], second["rows_already_captured"]) == (1, 0, 1)
    row = db.rows["wow_nba_candidate_forward_shadow"][0]
    assert row["official_event_id"] == "espn-401910025" and row["schema_version"] == "NBA_CANDIDATE_FORWARD_SHADOW_V1"
    assert row["source_observed_at"] <= row["knowledge_cutoff"] < row["event_start_time_utc"]


def test_run_isolates_failures_and_never_publishes():
    out = lane.run_nba_candidate_forward_shadow(_db(wow_basketball_team_event_certification=[]),
                                                schedule_rows_fn=lambda y: [], clock=lambda: NOW)
    assert out["capture"] == {"status": "FAILED", "reason_code": "NBA_CANDIDATE_NOT_REGISTERED",
                              "typed_failure": "MODEL_UNAVAILABLE"}
    assert out["scorecard"] == {"selection_policy": "LATEST_VALID_PRETIPOFF_V1", "graded_n": 0}
    assert out["probability_publishable"] is False and out["can_execute"] is False
    assert out["certification_admission_source"] is False and out["automatic_promotion"] is False


def test_maintenance_runs_capture_even_when_nba_refit_blocked(monkeypatch):
    from v17 import basketball_model_maintenance as maintenance
    calls = []
    monkeypatch.setattr(lane, "run_nba_candidate_forward_shadow", lambda db: calls.append(db) or {"status": "X"})
    hook = maintenance.nba_candidate_forward_shadow_after_refresh
    assert hook("db", [{"sport": "NBA", "status": "SHADOW_EVIDENCE_UPDATED"}]) == {"status": "X"} and calls == ["db"]
    recovered = hook("db", [{"sport": "NBA", "status": "BLOCKED"}])
    assert recovered == {"status": "X"} and calls == ["db", "db"]
    assert hook("db", [{"sport": "WNBA", "status": "SHADOW_EVIDENCE_UPDATED"}]) is None
    monkeypatch.setattr(lane, "run_nba_candidate_forward_shadow", lambda db: (_ for _ in ()).throw(ValueError("x")))
    failed = hook("db", [{"sport": "NBA", "status": "SHADOW_EVIDENCE_UPDATED"}])
    assert failed["reason_code"] == "NBA_CANDIDATE_FORWARD_SHADOW_VALUEERROR" and failed["can_execute"] is False
    # Maintenance reports the research lane independently of the refit result.
    out = maintenance.run_basketball_model_maintenance(object(), sports=["NBA"], now=NOW)
    assert "nba_candidate_forward_shadow" in out and out["can_execute"] is False


MIGRATION = (Path(__file__).parent / "migrations" / "20261121_nba_candidate_forward_shadow.sql").read_text()


def test_migration_is_additive_private_and_append_only():
    sql = MIGRATION.lower()
    for needle in (
        "create table if not exists public.wow_nba_candidate_forward_shadow",
        "new.created_at := statement_timestamp()",
        "nba_candidate_capture_not_pre_tipoff",
        "before update or delete on public.wow_nba_candidate_forward_shadow",
        "before truncate on public.wow_nba_candidate_forward_shadow",
        "check (probability_publishable = false)", "check (can_execute = false)",
        "with (security_invoker = true, security_barrier = true)",
        "revoke all on public.wow_nba_candidate_forward_shadow from public, anon, authenticated",
        "revoke all on public.wow_nba_candidate_forward_verified_grades from public, anon, authenticated",
    ):
        assert needle in sql, needle
    for forbidden in ("alter table public.wow_nba_training_games", "alter table public.wow_basketball",
                      "update public.", "delete from", "drop table", "probability_publishable = true"):
        assert forbidden not in sql, forbidden


def test_verified_view_grades_latest_pretipoff_capture_once_per_event():
    db = sqlite3.connect(":memory:")
    db.create_function("ln", 1, math.log)
    db.create_function("power", 2, math.pow)
    db.execute("attach database ':memory:' as public")
    db.execute("""create table public.wow_nba_candidate_forward_shadow (capture_id text, schema_version text,
        official_event_id text, created_at text, event_start_time_utc text, home_team_id text, away_team_id text,
        model_artifact_version text, model_artifact_checksum text, calibrator_version text,
        home_win_probability real, can_execute integer)""")
    db.execute("""create table public.wow_nba_training_games (game_id text, settled integer, home_team_id text,
        away_team_id text, home_score integer, away_score integer, home_win integer, scheduled_start_utc text)""")

    def cap(cid, ev, created, p, start="2026-11-22T01:00:00Z"):
        db.execute("insert into public.wow_nba_candidate_forward_shadow values (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (cid, "NBA_CANDIDATE_FORWARD_SHADOW_V1", ev, created, start, "espn-1", "espn-2", "V", "c", "C", p, 0))

    def final(ev, hs, as_, start="2026-11-22T01:00:00Z", home="espn-1"):
        db.execute("insert into public.wow_nba_training_games values (?,?,?,?,?,?,?,?)",
                   (ev, 1, home, "espn-2", hs, as_, int(hs > as_), start))

    cap("a1", "espn-1", "2026-11-21T19:00:00Z", 0.55)
    cap("a2", "espn-1", "2026-11-21T23:00:00Z", 0.60)   # latest pre-tipoff: graded
    cap("a3", "espn-1", "2026-11-22T01:30:00Z", 0.90)   # post-tipoff: never graded
    cap("b1", "espn-2", "2026-11-21T19:00:00Z", 0.70, start="2026-11-22T02:00:00Z")
    final("espn-1", 110, 100)
    final("espn-2", 100, 100)                            # tie: never graded
    cap("c1", "espn-3", "2026-11-21T19:00:00Z", 0.65)
    final("espn-3", 110, 100, home="espn-9")             # team mismatch: never graded
    cap("d1", "espn-4", "2026-11-21T23:30:00Z", 0.65)
    final("espn-4", 110, 100, start="2026-11-21T23:00:00Z")  # tipoff moved earlier
    select = "select" + MIGRATION.split("as\nselect", 1)[1].split(";\n\nrevoke", 1)[0]
    select = select.replace("::double precision", "").replace("::text", "")
    rows = db.execute(select).fetchall()
    assert [(r[0], r[1], r[8]) for r in rows] == [("espn-1", "a2", 1)]
