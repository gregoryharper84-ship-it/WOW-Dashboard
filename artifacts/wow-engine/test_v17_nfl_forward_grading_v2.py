"""NFL forward grading V2: grading eligibility is independent of publication.

Owner decision 2026-11-21 (#1896 / #1939). V1 (latest *publishable*
pre-kickoff forecast) is preserved unchanged as the historical record and the
only certification-admission source. V2 grades the latest *valid* pre-kickoff
forecast into its own immutable ledger, never publishes, never writes health.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from v17 import nfl_forward_shadow as shadow
from v17.nfl_forward_shadow import (
    NFLForwardShadowError,
    grade_forward_predictions,
    select_canonical_forward_predictions,
    select_latest_valid_prekickoff_predictions,
)

KICKOFF = datetime(2026, 11, 16, 1, 15, tzinfo=timezone.utc)


def _row(pid, *, event="2026_10_LAC_BAL", minutes=-60, p=0.62, publishable=False,
         package_valid=True, provenance_complete=True, can_execute=False, kickoff=KICKOFF):
    return {
        "event_prediction_id": pid,
        "official_event_id": event,
        "event_start_time_utc": kickoff.isoformat(),
        "created_at": (KICKOFF + timedelta(minutes=minutes)).isoformat(),
        "model_version": "NFL_EVENT_LOGREG_PLATT_V1_test",
        "home_team": "BAL",
        "away_team": "LAC",
        "selected_participant": "BAL",
        "calibrated_selection_probability": p,
        "calibrated_selection_lower_bound": round(p - 0.05, 6),
        "calibrated_selection_upper_bound": round(p + 0.05, 6),
        "model_package_valid": package_valid,
        "provenance_complete": provenance_complete,
        "model_probability_publishable": publishable,
        "can_execute": can_execute,
    }


def _final(event="2026_10_LAC_BAL", home=27, away=17):
    return {"game_id": event, "home_score": home, "away_score": away,
            "home_win": home > away, "tie": home == away, "can_execute": False}


# (1) Reproduce the flag-coupling defect.
def test_v1_drops_valid_nonpublishable_forecast_and_v2_grades_it():
    rows = [_row("00000000-0000-0000-0000-000000000001", publishable=False)]
    assert select_canonical_forward_predictions(rows) == []  # the defect, preserved in V1
    selected, report = select_latest_valid_prekickoff_predictions(rows)
    assert [s.event_prediction_id for s in selected] == ["00000000-0000-0000-0000-000000000001"]
    assert report["selection_policy"] == "LATEST_VALID_PREKICKOFF_V2"
    grades = grade_forward_predictions(selected, [_final()])
    assert [(g.official_event_id, g.outcome) for g in grades] == [("2026_10_LAC_BAL", 1)]
    # Grading never alters the stored forecast.
    assert grades[0].calibrated_probability == 0.62
    assert grades[0].calibrated_lower_bound == 0.57


def test_v2_selects_latest_valid_not_last_publishable_week10_shape():
    """Week-10 shape: publishable rows stopped days before kickoff."""
    rows = [
        _row("00000000-0000-0000-0000-00000000000a", minutes=-6 * 24 * 60, p=0.70, publishable=True),
        _row("00000000-0000-0000-0000-00000000000b", minutes=-90, p=0.55),
        _row("00000000-0000-0000-0000-00000000000c", minutes=-30, p=0.58),
    ]
    assert [s.event_prediction_id for s in select_canonical_forward_predictions(rows)] == [
        "00000000-0000-0000-0000-00000000000a"]
    selected, _ = select_latest_valid_prekickoff_predictions(rows)
    assert [s.event_prediction_id for s in selected] == ["00000000-0000-0000-0000-00000000000c"]


# (2) Reject invalid, post-kickoff, stale and duplicate rows with typed reasons.
def test_v2_rejects_invalid_and_post_kickoff_rows_with_typed_reasons():
    rows = [
        _row("00000000-0000-0000-0000-000000000001", minutes=-20, package_valid=False),
        _row("00000000-0000-0000-0000-000000000002", minutes=-15, provenance_complete=False),
        _row("00000000-0000-0000-0000-000000000003", minutes=0),
        _row("00000000-0000-0000-0000-000000000004", minutes=5),
        _row("00000000-0000-0000-0000-000000000005", minutes=-45),
        _row("00000000-0000-0000-0000-000000000006", event="2026_11_IND_HOU", minutes=10),
    ]
    selected, report = select_latest_valid_prekickoff_predictions(rows)
    assert [s.event_prediction_id for s in selected] == ["00000000-0000-0000-0000-000000000005"]
    assert report["ineligible_rows"] == {
        "MODEL_PACKAGE_INVALID": 1, "NOT_STRICTLY_PRE_KICKOFF": 3, "PROVENANCE_INCOMPLETE": 1}
    assert report["events_without_eligible_prediction"] == {"2026_11_IND_HOU": ["NOT_STRICTLY_PRE_KICKOFF"]}
    assert report["events_without_eligible_prediction_n"] == 1


def test_v2_uses_earliest_recorded_kickoff_so_a_moved_kickoff_cannot_admit_late_rows():
    early = KICKOFF - timedelta(minutes=20)
    rows = [
        _row("00000000-0000-0000-0000-000000000001", minutes=-60),
        # Created after the earlier recorded kickoff, before its own stale stamp.
        _row("00000000-0000-0000-0000-000000000002", minutes=-10),
        _row("00000000-0000-0000-0000-000000000003", minutes=-90, kickoff=early),
    ]
    selected, report = select_latest_valid_prekickoff_predictions(rows)
    assert [s.event_prediction_id for s in selected] == ["00000000-0000-0000-0000-000000000001"]
    assert report["ineligible_rows"] == {"NOT_STRICTLY_PRE_KICKOFF": 1}


def test_v2_rejects_duplicate_prediction_ids_and_executable_rows():
    row = _row("00000000-0000-0000-0000-000000000001")
    with pytest.raises(NFLForwardShadowError, match="PREDICTION_ID_DUPLICATED"):
        select_latest_valid_prekickoff_predictions([row, dict(row)])
    with pytest.raises(NFLForwardShadowError, match="CAN_EXECUTE_MUST_BE_FALSE"):
        select_latest_valid_prekickoff_predictions([_row("00000000-0000-0000-0000-000000000002", can_execute=True)])
    with pytest.raises(NFLForwardShadowError, match="MODEL_OUTPUT_INVALID"):
        select_latest_valid_prekickoff_predictions([_row("00000000-0000-0000-0000-000000000003", p=1.0)])


# (3) Deterministic ordering, independent of input order and outcomes.
def test_v2_selection_is_deterministic_on_equal_timestamps_and_input_order():
    rows = [
        _row("00000000-0000-0000-0000-00000000000b", minutes=-30, p=0.60),
        _row("00000000-0000-0000-0000-00000000000a", minutes=-30, p=0.61),
        _row("00000000-0000-0000-0000-000000000001", event="2026_11_IND_HOU", minutes=-40),
    ]
    first, _ = select_latest_valid_prekickoff_predictions(rows)
    second, _ = select_latest_valid_prekickoff_predictions(list(reversed(rows)))
    assert first == second
    lac = [s for s in first if s.official_event_id == "2026_10_LAC_BAL"]
    assert [s.event_prediction_id for s in lac] == ["00000000-0000-0000-0000-00000000000b"]


class _Query:
    def __init__(self, db, name):
        self.db, self.name, self.key, self.payload = db, name, None, None

    def select(self, _columns):
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
            # Database default primary key, as gen_random_uuid() in Postgres.
            n = len(self.db.rows[self.name])
            self.payload = [dict(r, grade_id=f"grade-{n + i:04d}") for i, r in enumerate(self.payload)]
            self.db.inserts.setdefault(self.name, []).extend(self.payload)
            self.db.rows[self.name].extend(self.payload)
            return SimpleNamespace(data=self.payload)
        ordered = sorted(self.db.rows[self.name], key=lambda r: str(r.get(self.key) or ""))
        return SimpleNamespace(data=ordered[self.start:self.end + 1])


class _DB:
    def __init__(self, rows):
        self.rows, self.inserts, self.health = rows, {}, []

    def table(self, name):
        if name == "wow_nfl_forward_calibration_health":
            db = self

            class _Health:
                def insert(self, row):
                    db.health.append(dict(row))
                    return self

                def execute(self):
                    return SimpleNamespace(data=[])
            return _Health()
        return _Query(self, name)


def _db(with_v2=True, v2_verified=None):
    predictions = [
        _row("00000000-0000-0000-0000-000000000001", minutes=-30),
        _row("00000000-0000-0000-0000-000000000002", event="2026_11_IND_HOU", minutes=-30),
        _row("00000000-0000-0000-0000-000000000003", event="2026_05_NYG_WAS", minutes=-30),
    ]
    rows = {
        "wow_nfl_event_predictions": predictions,
        "wow_nfl_training_games": [_final(), _final("2026_11_IND_HOU", 20, 23), _final("2026_05_NYG_WAS", 20, 20)],
        "wow_nfl_forward_shadow_grades": [],
        "wow_nfl_forward_verified_grades": [],
    }
    if with_v2:
        rows["wow_nfl_forward_shadow_grades_v2"] = []
        rows["wow_nfl_forward_verified_grades_v2"] = v2_verified if v2_verified is not None else []
    return _DB(rows)


def test_run_grades_v2_without_touching_v1_ledger_health_or_publication():
    db = _db()
    result = shadow.run_forward_shadow(db, settlement_refresh_fn=lambda _: {"status": "COMPLETED"})
    v2 = result["forward_grading_v2"]
    assert v2["status"] == "COMPLETED"
    assert v2["graded_events"] == 2 and v2["new_grades_persisted"] == 2
    assert v2["ungraded_canonical_events"] == {"OUTCOME_NOT_BINARY_FINAL": 1}  # the tie is void
    inserted = db.inserts["wow_nfl_forward_shadow_grades_v2"]
    assert {r["schema_version"] for r in inserted} == {"NFL_FORWARD_SHADOW_GRADE_V2"}
    assert {r["selection_policy"] for r in inserted} == {"LATEST_VALID_PREKICKOFF_V2"}
    assert {r["official_event_id"] for r in inserted} == {"2026_10_LAC_BAL", "2026_11_IND_HOU"}
    # V1 historical ledger and the persisted health receipt are unchanged.
    assert "wow_nfl_forward_shadow_grades" not in db.inserts
    assert len(db.health) == 1 and db.health[0]["graded_n"] == 0
    assert db.health[0]["schema_version"] == "NFL_FORWARD_CALIBRATION_HEALTH_V2"
    assert result["selection_policy"] == "LATEST_PUBLISHABLE_PREKICKOFF_V1"
    assert v2["certification_admission_source"] is False
    assert v2["probability_publishable"] is False
    assert result["can_execute"] is False and v2["can_execute"] is False
    assert result["automatic_certification"] is False and result["automatic_promotion"] is False


def test_rerun_never_duplicates_v2_grades():
    db = _db()
    shadow.run_forward_shadow(db, settlement_refresh_fn=lambda _: {"status": "COMPLETED"})
    again = shadow.run_forward_shadow(db, settlement_refresh_fn=lambda _: {"status": "COMPLETED"})
    assert again["forward_grading_v2"]["new_grades_persisted"] == 0
    assert len(db.rows["wow_nfl_forward_shadow_grades_v2"]) == 2


def test_missing_v2_migration_fails_typed_and_keeps_v1_receipt():
    db = _db(with_v2=False)
    result = shadow.run_forward_shadow(db, settlement_refresh_fn=lambda _: {"status": "COMPLETED"})
    assert result["forward_grading_v2"] == {
        "status": "FAILED", "reason_code": "NFL_FORWARD_V2_RUNTIMEERROR", "can_execute": False}
    assert len(db.health) == 1


def test_v2_verified_rows_with_wrong_schema_fail_closed():
    db = _db(v2_verified=[{
        "official_event_id": "x", "event_prediction_id": "p", "event_start_time_utc": "t",
        "prediction_created_at": "t", "model_version": "m", "selected_participant": "BAL",
        "calibrated_probability": .6, "calibrated_lower_bound": .55, "calibrated_upper_bound": .65,
        "outcome": 1, "brier": .16, "log_loss": .51, "source_game_id": "x",
        "schema_version": "NFL_FORWARD_SHADOW_GRADE_V1", "can_execute": False}])
    result = shadow.run_forward_shadow(db, settlement_refresh_fn=lambda _: {"status": "COMPLETED"})
    assert result["forward_grading_v2"]["reason_code"] == "NFL_FORWARD_V2_NFL_FORWARD_VERIFIED_GRADE_INVALID"


# Migration contract: execute the real V2 view SELECT against SQLite.
MIGRATION = Path(__file__).parent / "migrations" / "20261121_nfl_forward_grades_v2_latest_valid_prekickoff.sql"
SQL_RAW = MIGRATION.read_text(encoding="utf-8")
SQL = SQL_RAW.lower()


def test_migration_is_additive_private_append_only_and_leaves_v1_untouched():
    assert "create table if not exists public.wow_nfl_forward_shadow_grades_v2" in SQL
    assert "create or replace view public.wow_nfl_forward_verified_grades_v2" in SQL
    assert "unique (selection_policy, official_event_id)" in SQL
    assert "event_prediction_id uuid not null unique" in SQL
    assert "before update or delete on public.wow_nfl_forward_shadow_grades_v2" in SQL
    assert "before truncate on public.wow_nfl_forward_shadow_grades_v2" in SQL
    assert "check (can_execute = false)" in SQL
    assert "with (security_invoker = true, security_barrier = true)" in SQL
    assert "revoke all on public.wow_nfl_forward_verified_grades_v2 from public, anon, authenticated" in SQL
    assert "revoke all on public.wow_nfl_forward_shadow_grades_v2 from public, anon, authenticated" in SQL
    assert "model_probability_publishable" not in SQL
    for forbidden in (
        "public.wow_nfl_forward_verified_grades\n", "view public.wow_nfl_forward_verified_grades ",
        "alter table public.wow_nfl_forward_shadow_grades ", "alter table public.wow_nfl_event_predictions",
        "update public.", "delete from", "truncate table", "probability_publishable =", "grant insert on public.wow_nfl_forward_verified",
    ):
        assert forbidden not in SQL, forbidden


def _sqlite():
    db = sqlite3.connect(":memory:")
    db.create_function("ln", 1, math.log)
    db.create_function("power", 2, math.pow)
    db.create_function("greatest", -1, max)
    db.create_function("least", -1, min)
    db.execute("attach database ':memory:' as public")
    db.execute("""create table public.wow_nfl_forward_shadow_grades_v2 (
        schema_version text, selection_policy text, event_prediction_id text, official_event_id text,
        source_game_id text, model_version text, selected_participant text,
        prediction_created_at text, event_start_time_utc text, calibrated_probability real,
        calibrated_lower_bound real, calibrated_upper_bound real, outcome integer, brier real,
        log_loss real, can_execute integer)""")
    db.execute("""create table public.wow_nfl_event_predictions (
        event_prediction_id text, official_event_id text, model_version text,
        selected_participant text, home_team text, away_team text, created_at text,
        event_start_time_utc text, model_package_valid integer, provenance_complete integer,
        model_probability_publishable integer, can_execute integer,
        calibrated_selection_probability real, calibrated_selection_lower_bound real,
        calibrated_selection_upper_bound real, model_artifact_id text, model_output_snapshot text)""")
    db.execute("""create table public.wow_nfl_training_games (
        game_id text, home_score integer, away_score integer, home_win integer, tie integer,
        can_execute integer)""")
    return db


def _insert_prediction(db, pid, event, created, kickoff="2026-11-16T01:15:00Z", *,
                       valid=1, provenance=1, publishable=0, p=0.62):
    db.execute("insert into public.wow_nfl_event_predictions values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (pid, event, "M", "BAL", "BAL", "LAC", created, kickoff, valid, provenance,
                publishable, 0, p, p - 0.05, p + 0.05, "artifact", '{"artifact_checksum":"c"}'))


def _insert_grade(db, pid, event, created, kickoff="2026-11-16T01:15:00Z", *, p=0.62, outcome=1):
    db.execute("insert into public.wow_nfl_forward_shadow_grades_v2 values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               ("NFL_FORWARD_SHADOW_GRADE_V2", "LATEST_VALID_PREKICKOFF_V2", pid, event, event, "M",
                "BAL", created, kickoff, p, p - 0.05, p + 0.05, outcome, (p - outcome) ** 2,
                -math.log(p if outcome else 1 - p), 0))


def test_v2_view_accepts_latest_valid_nonpublishable_and_drops_stale_or_post_kickoff():
    db = _sqlite()
    # valid: latest valid row, non-publishable, is graded.
    _insert_prediction(db, "a1", "valid", "2026-11-10T09:00:00Z", publishable=1)
    _insert_prediction(db, "a2", "valid", "2026-11-16T00:45:00Z")
    _insert_grade(db, "a2", "valid", "2026-11-16T00:45:00Z")
    # stale: graded row is not the latest valid pre-kickoff row.
    _insert_prediction(db, "b1", "stale", "2026-11-10T09:00:00Z", publishable=1)
    _insert_prediction(db, "b2", "stale", "2026-11-16T00:45:00Z")
    _insert_grade(db, "b1", "stale", "2026-11-10T09:00:00Z")
    # a later INVALID row does not make the latest valid row stale.
    _insert_prediction(db, "c1", "later_invalid", "2026-11-16T00:30:00Z")
    _insert_prediction(db, "c2", "later_invalid", "2026-11-16T00:50:00Z", valid=0)
    _insert_grade(db, "c1", "later_invalid", "2026-11-16T00:30:00Z")
    # moved kickoff: graded row is after the earliest recorded kickoff.
    _insert_prediction(db, "d1", "moved", "2026-11-16T01:00:00Z")
    _insert_prediction(db, "d2", "moved", "2026-11-15T20:00:00Z", kickoff="2026-11-16T00:55:00Z")
    _insert_grade(db, "d1", "moved", "2026-11-16T01:00:00Z")
    # tie is never a binary grade.
    _insert_prediction(db, "e1", "tie", "2026-11-16T00:45:00Z")
    _insert_grade(db, "e1", "tie", "2026-11-16T00:45:00Z")
    # equal timestamps: only the higher prediction id is canonical.
    _insert_prediction(db, "f1", "tied_clock", "2026-11-16T00:45:00Z")
    _insert_prediction(db, "f2", "tied_clock", "2026-11-16T00:45:00Z")
    _insert_grade(db, "f1", "tied_clock", "2026-11-16T00:45:00Z")
    for event in ("valid", "stale", "later_invalid", "moved", "tied_clock"):
        db.execute("insert into public.wow_nfl_training_games values(?,?,?,?,?,?)", (event, 27, 17, 1, 0, 0))
    db.execute("insert into public.wow_nfl_training_games values(?,?,?,?,?,?)", ("tie", 20, 20, 0, 1, 0))
    selected = "select" + SQL_RAW.split("as\nselect", 1)[1].split(";\n\nrevoke", 1)[0]
    selected = selected.replace("::double precision", "")
    rows = db.execute(selected).fetchall()
    assert sorted(row[0] for row in rows) == ["later_invalid", "valid"]
