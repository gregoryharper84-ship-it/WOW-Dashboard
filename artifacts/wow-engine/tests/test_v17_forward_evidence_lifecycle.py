"""Shared forward-evidence lifecycle: freeze, capture, settle, grade (#1951).

An in-memory Supabase fake emulates the migration's guards (server clock,
pregame-only, frozen identity, normalized probabilities, uniqueness,
immutability) and the graded view, so the lifecycle is exercised end to end.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from v17 import forward_evidence as fe
from v17.forward_evidence_adapters import NCAAFResultFormAdapter, ncaaf_forward_evidence_after_maintenance

ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations" / "20261125_v17_forward_evidence_lifecycle.sql").read_text()
NOW = datetime(2026, 11, 25, 12, 10, tzinfo=timezone.utc)
KICK = datetime(2026, 11, 28, 20, 0, tzinfo=timezone.utc)
NAMES = ("home_win_rate_prior", "away_win_rate_prior", "home_point_diff_prior", "away_point_diff_prior",
         "home_games_prior_log", "away_games_prior_log", "home_rest_days_capped", "away_rest_days_capped",
         "neutral_site")


class DuplicateError(Exception):
    code = "23505"


class MissingTable(Exception):
    code = "42P01"


class Query:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.op, self.payload = db, table, [], "select", None
        self._order, self._limit, self._range = [], None, None

    def select(self, *_):
        return self

    def eq(self, k, v):
        self.filters.append(lambda r: r.get(k) == v)
        return self

    def lt(self, k, v):
        self.filters.append(lambda r: str(r.get(k)) < v)
        return self

    def gt(self, k, v):
        self.filters.append(lambda r: str(r.get(k)) > v)
        return self

    def in_(self, k, values):
        values = set(values)
        self.filters.append(lambda r: r.get(k) in values)
        return self

    def order(self, k, desc=False):
        self._order.append((k, desc))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def insert(self, row):
        self.op, self.payload = "insert", copy.deepcopy(row)
        return self

    def execute(self):
        if self.table in self.db.missing:
            raise MissingTable("relation does not exist")
        if self.op == "insert":
            self.db.insert(self.table, self.payload)
            return type("R", (), {"data": [self.payload]})()
        rows = [r for r in self.db.rows(self.table) if all(f(r) for f in self.filters)]
        for key, desc in reversed(self._order):
            rows.sort(key=lambda r: str(r.get(key)), reverse=desc)
        if self._range:
            rows = rows[self._range[0]:self._range[1] + 1]
        if self._limit is not None:
            rows = rows[:self._limit]
        return type("R", (), {"data": copy.deepcopy(rows)})()


class FakeDB:
    """Mirrors the migration's guards and view."""

    def __init__(self, clock):
        self.clock = clock
        self.tables = {fe.REGISTRATION_TABLE: [], fe.CAPTURE_TABLE: [], fe.SETTLEMENT_TABLE: [],
                       "wow_d1_candidate_artifacts": [], "wow_ncaaf_training_games": []}
        self.missing = set()
        self.seq = 0

    def table(self, name):
        return Query(self, name)

    def rows(self, name):
        if name == fe.GRADED_VIEW:
            return self.graded()
        return self.tables[name]

    def insert(self, name, row):
        self.seq += 1
        now = self.clock()
        if name == fe.REGISTRATION_TABLE:
            if any(r["sport"] == row["sport"] and r["league"] == row["league"]
                   and r["market_family"] == row["market_family"] and r["cohort_role"] == "EVALUATION"
                   for r in self.tables[name]) and row["cohort_role"] == "EVALUATION":
                raise DuplicateError("duplicate key")
            row.update(registration_id=f"reg-{self.seq}", created_at=now.isoformat())
        elif name == fe.CAPTURE_TABLE:
            reg = next(r for r in self.tables[fe.REGISTRATION_TABLE] if r["registration_id"] == row["registration_id"])
            assert row["artifact_checksum"] == reg["artifact_checksum"], "FORWARD_EVIDENCE_CAPTURE_IDENTITY_MISMATCH"
            fe.validate_probabilities(reg["outcome_space"], row["probabilities"])
            row["created_at"] = now.isoformat()  # server clock wins
            assert now < datetime.fromisoformat(row["event_start_time_utc"]), "pregame check"
            key = (row["registration_id"], row["official_event_id"], row["feature_payload_sha256"])
            if any((r["registration_id"], r["official_event_id"], r["feature_payload_sha256"]) == key
                   for r in self.tables[name]):
                raise DuplicateError("duplicate key")
            row["capture_id"] = f"cap-{self.seq}"
        elif name == fe.SETTLEMENT_TABLE:
            row["created_at"] = now.isoformat()
            assert now > datetime.fromisoformat(row["event_start_time_utc"])
            if any((r["sport"], r["league"], r["official_event_id"]) == (row["sport"], row["league"], row["official_event_id"])
                   for r in self.tables[name]):
                raise DuplicateError("duplicate key")
        self.tables[name].append(row)

    def graded(self):
        out = []
        latest = {}
        for c in self.tables[fe.CAPTURE_TABLE]:
            if c["created_at"] < c["event_start_time_utc"]:
                k = (c["registration_id"], c["official_event_id"])
                if k not in latest or c["created_at"] > latest[k]["created_at"]:
                    latest[k] = c
        for (reg_id, event_id), c in latest.items():
            reg = next(r for r in self.tables[fe.REGISTRATION_TABLE] if r["registration_id"] == reg_id)
            s = next((s for s in self.tables[fe.SETTLEMENT_TABLE]
                      if (s["sport"], s["league"], s["official_event_id"]) == (reg["sport"], reg["league"], event_id)), None)
            if s is None or s["outcome"] == "VOID":
                continue
            p = c["probabilities"]
            out.append({"registration_id": reg_id, "official_event_id": event_id, "outcome": s["outcome"],
                        "probabilities": p, "outcome_probability": p[s["outcome"]],
<<<<<<< HEAD
                        "brier": (p["HOME"] - (s["outcome"] == "HOME")) ** 2,
=======
                        "brier": ((p["HOME"] - (s["outcome"] == "HOME")) ** 2 if reg["outcome_space"] == "BINARY_HOME_AWAY"
                                  else sum((v - (k == s["outcome"])) ** 2 for k, v in p.items())),
>>>>>>> origin/main
                        "log_loss": -__import__("math").log(p[s["outcome"]])})
        return out


def _payload(intercept=0.3):
    return {"intercept": intercept, "coefficients": [0.04] * len(NAMES), "scaler_mean": [0.] * len(NAMES),
            "scaler_scale": [1.] * len(NAMES), "feature_names": list(NAMES), "model_family": "NCAAF_RESULT_FORM_LOGIT_V1",
            "artifact_format": "STANDARDIZED_LOGISTIC_JSON_V1", "split_policy": "CHRONOLOGICAL_60_20_20"}


def candidate(cid, created, intercept=0.3, *, valid=True):
    payload = _payload(intercept)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    return {"candidate_id": cid, "sport": "NCAAF", "league": "NCAAF", "market_family": "OUTRIGHT_WINNER",
            "feature_schema_version": "NCAAF_RESULT_FORM_PRIOR_V1", "model_family": "NCAAF_RESULT_FORM_LOGIT_V1",
            "source_policy_id": "NCAAF_SETTLED_PRIOR_RESULTS_RECONSTRUCTION_V1", "artifact_payload": payload,
            "artifact_checksum": digest if valid else "0" * 64,
            "calibrator_payload": {"method": "IDENTITY_RAW_PROBABILITY_V1", "bins": [],
                                   "selection_version": "FORWARD_CALIBRATION_SELECTION_V2",
                                   "selection": {"test_block_used_for_selection": False}},
            "lifecycle_state": "CANDIDATE", "research_screen_pass": True, "active": False, "promoted": False,
            "probability_publishable": False, "can_execute": False, "automatic_certification": False,
            "automatic_promotion": False, "created_at": created, "training_dataset_hash": "b" * 64,
            "training_code_sha": "abcdef1234567890",
            "model_artifact_version": "NCAAF_RESULT_FORM_LOGIT_V1_" + ("b" * 16) + "_abcdef123456"}


def game(event_id, home, away, when, hp, ap):
    return {"official_event_id": event_id, "event_start_time": when.isoformat(), "home_team": home, "away_team": away,
            "home_points": hp, "away_points": ap, "home_won": hp > ap, "result_source": "CFBD:/games",
            "result_source_timestamp": (when + timedelta(hours=12)).isoformat()}


def history():
    rows = []
    for team in ("Home U", "Away U", "Third U", "Fourth U"):
        for i in range(4):
            rows.append(game(f"{team}-{i}", team, f"Opp {i}", NOW - timedelta(days=10 + 7 * i), 30 - i, 20 + 2 * i))
    return rows


def fixture(event_id, home, away, start, **extra):
    return {"id": event_id, "startDate": start.isoformat(), "homeTeam": home, "awayTeam": away,
            "neutralSite": False, **extra}


def season_rows():
    return [
        fixture("401", "Home U", "Away U", KICK),
        fixture("402", "Third U", "Fourth U", KICK + timedelta(hours=3)),
        fixture("403", "Home U", "Lonely U", KICK + timedelta(hours=1)),       # insufficient history
        fixture("404", "Third U", "Away U", KICK, startTimeTBD=True),           # placeholder kickoff
        fixture("405", "Third U", "Home U", NOW + timedelta(days=20)),          # outside horizon
        fixture("406", "Home U", "Fourth U", NOW - timedelta(hours=1)),         # already started
    ]


def make_db(clock_value):
    state = {"now": clock_value}
    db = FakeDB(lambda: state["now"])
    db.tables["wow_d1_candidate_artifacts"] = [candidate("cand-old", "2026-11-17T00:00:00+00:00")]
    db.tables["wow_ncaaf_training_games"] = history()
    return db, state


def run(db, now, **kw):
    return fe.run_forward_evidence(db, NCAAFResultFormAdapter(season_rows=lambda year: season_rows()), now=now, **kw)


def test_first_run_freezes_captures_and_isolates_event_failures():
    db, _ = make_db(NOW)
    receipt = run(db, NOW)
    assert receipt["status"] == "FORWARD_EVIDENCE_UPDATED", receipt
    assert receipt["captured"] == 2 and receipt["events_considered"] == 3
    assert receipt["blocked_by_code"] == {"NCAAF_SHADOW_PRIOR_SAMPLE_INSUFFICIENT": 1}
    reg = db.tables[fe.REGISTRATION_TABLE][0]
    assert reg["candidate_id"] == "cand-old" and reg["freeze_policy"] == fe.FREEZE_POLICY
    for row in db.tables[fe.CAPTURE_TABLE]:
        assert set(row["probabilities"]) == {"HOME", "AWAY"}
        assert abs(sum(row["probabilities"].values()) - 1) < 1e-12
        assert row["artifact_checksum"] == reg["artifact_checksum"]
        assert set(row["features"]) == set(NAMES)
        assert row["probability_publishable"] is False and row["rank_eligible"] is False and row["can_execute"] is False
    for flag in ("probability_publishable", "rank_eligible", "automatic_certification", "automatic_promotion", "can_execute"):
        assert receipt[flag] is False


def test_refit_never_moves_frozen_candidate_and_reruns_are_idempotent():
    db, state = make_db(NOW)
    run(db, NOW)
    frozen = db.tables[fe.REGISTRATION_TABLE][0]["artifact_checksum"]
    db.tables["wow_d1_candidate_artifacts"].append(candidate("cand-new", "2026-11-24T00:00:00+00:00", intercept=-1.0))
    later = NOW + timedelta(hours=6)
    state["now"] = later
    receipt = run(db, later)
    assert len(db.tables[fe.REGISTRATION_TABLE]) == 1
    assert receipt["frozen_artifact_checksum"] == frozen
    assert receipt["captured"] == 0 and receipt["duplicate"] == 2
    assert {r["artifact_checksum"] for r in db.tables[fe.CAPTURE_TABLE]} == {frozen}
    # New results change the features, so a new capture is taken; its probability
    # must come from the frozen artifact, not the newer refit.
    db.tables["wow_ncaaf_training_games"].append(game("new-result", "Home U", "Opp 9", later - timedelta(hours=20), 10, 40))
    state["now"] = later + timedelta(hours=1)
    receipt = run(db, later + timedelta(hours=1))
    assert receipt["captured"] >= 1
    newest = max((r for r in db.tables[fe.CAPTURE_TABLE] if r["official_event_id"] == "401"), key=lambda r: r["created_at"])
    adapter = NCAAFResultFormAdapter(season_rows=lambda year: season_rows())
    event = next(e for e in adapter.upcoming_events(db, later + timedelta(hours=1), timedelta(days=8)) if e.official_event_id == "401")
    frozen_p = adapter.score(event, db.tables["wow_ncaaf_training_games"], candidate("cand-old", "2026-11-17T00:00:00+00:00"),
                             later + timedelta(hours=1)).probabilities["HOME"]
    refit_p = adapter.score(event, db.tables["wow_ncaaf_training_games"], candidate("cand-new", "2026-11-24T00:00:00+00:00", intercept=-1.0),
                            later + timedelta(hours=1)).probabilities["HOME"]
    assert newest["probabilities"]["HOME"] == frozen_p != refit_p


def test_invalid_newest_candidate_is_skipped_not_frozen():
    db, _ = make_db(NOW)
    db.tables["wow_d1_candidate_artifacts"].append(candidate("cand-bad", "2026-11-24T00:00:00+00:00", valid=False))
    run(db, NOW)
    assert db.tables[fe.REGISTRATION_TABLE][0]["candidate_id"] == "cand-old"


def test_settlement_after_kickoff_grades_the_latest_pregame_capture():
    db, state = make_db(NOW)
    run(db, NOW)
    after = KICK + timedelta(hours=8)
    state["now"] = after
    db.tables["wow_ncaaf_training_games"].append(game("401", "Home U", "Away U", KICK, 24, 27))
    receipt = run(db, after)
    assert receipt["settled"] == 1
    settlement = db.tables[fe.SETTLEMENT_TABLE][0]
    assert settlement["outcome"] == "AWAY" and settlement["settlement_source"] == "CFBD:/games"
    card = receipt["scorecard"]
    assert card["graded_n"] == 1 and 0 < card["brier"] < 1 and card["log_loss"] > 0
<<<<<<< HEAD
=======
    assert card["captured_events"] == 2 and card["observed_home_rate"] == 0.0
    assert card["home_calibration_bias"] == card["mean_home_probability"]
    assert card["lower_bound"] == "NOT_AVAILABLE_RESEARCH_CANDIDATE"
>>>>>>> origin/main
    assert run(db, after)["settled"] == 0  # idempotent


def test_settlement_start_mismatch_is_typed_and_not_graded():
    db, state = make_db(NOW)
    run(db, NOW)
    after = KICK + timedelta(hours=8)
    state["now"] = after
    db.tables["wow_ncaaf_training_games"].append(game("401", "Home U", "Away U", KICK + timedelta(days=1), 24, 27))
    receipt = run(db, after + timedelta(days=1))
    assert receipt["settled"] == 0
    assert receipt["settlement_blocked_by_code"] == {"NCAAF_SETTLEMENT_START_MISMATCH": 1}


<<<<<<< HEAD
def test_settlement_records_the_finals_own_kickoff_within_tolerance():
    db, state = make_db(NOW)
    run(db, NOW)
    after = KICK + timedelta(hours=8)
    state["now"] = after
    moved = KICK - timedelta(minutes=60)  # schedule moved earlier after capture
    db.tables["wow_ncaaf_training_games"].append(game("401", "Home U", "Away U", moved, 24, 27))
    assert run(db, after)["settled"] == 1
    settlement = db.tables[fe.SETTLEMENT_TABLE][0]
    assert datetime.fromisoformat(settlement["event_start_time_utc"]) == moved


def test_graded_view_requires_capture_before_settlement_kickoff():
    fix = (Path(__file__).resolve().parents[1] / "migrations"
           / "20261125b_v17_forward_evidence_graded_settlement_kickoff.sql").read_text()
    assert "l.created_at < s.event_start_time_utc" in fix
    assert "create or replace view public.wow_v17_forward_evidence_graded" in fix


=======
>>>>>>> origin/main
def test_capture_skipped_without_fresh_acquisition_but_settlement_still_runs():
    db, _ = make_db(NOW)
    receipt = run(db, NOW, capture=False, capture_skip_reason="NCAAF_ACQUISITION_NOT_FRESH")
    assert receipt["status"] == "FORWARD_EVIDENCE_UPDATED"
    assert receipt["captured"] == 0 and receipt["capture_skipped_reason"] == "NCAAF_ACQUISITION_NOT_FRESH"
    assert db.tables[fe.CAPTURE_TABLE] == []


def test_missing_tables_fail_closed_without_raising():
    db, _ = make_db(NOW)
    db.missing.add(fe.REGISTRATION_TABLE)
    receipt = run(db, NOW)
    assert receipt["status"] == "FAILED" and receipt["code"] == "FORWARD_EVIDENCE_DB_42P01"
    assert receipt["can_execute"] is False


def test_fixture_source_failure_is_typed_and_isolated():
    db, _ = make_db(NOW)

    def broken(year):
        raise RuntimeError("cfbd down")

    receipt = fe.run_forward_evidence(db, NCAAFResultFormAdapter(season_rows=broken), now=NOW)
    assert receipt["status"] == "BLOCKED" and receipt["code"] == "NCAAF_FIXTURE_SOURCE_UNAVAILABLE"


def test_maintenance_hook_never_raises():
    class Exploding:
        def table(self, name):
            raise RuntimeError("boom")

    receipt = ncaaf_forward_evidence_after_maintenance(Exploding(), fresh_acquisition_complete=True)
    assert receipt["status"] == "FAILED" and receipt["can_execute"] is False


@pytest.mark.parametrize("space,probs,code", [
    ("BINARY_HOME_AWAY", {"HOME": 0.6}, "FORWARD_EVIDENCE_PROBABILITY_KEYS_INVALID"),
    ("BINARY_HOME_AWAY", {"HOME": 1.0, "AWAY": 0.0}, "FORWARD_EVIDENCE_PROBABILITY_DOMAIN_INVALID"),
    ("BINARY_HOME_AWAY", {"HOME": 0.6, "AWAY": 0.5}, "FORWARD_EVIDENCE_PROBABILITIES_NOT_NORMALIZED"),
    ("THREE_WAY_1X2", {"HOME": 0.5, "AWAY": 0.5}, "FORWARD_EVIDENCE_PROBABILITY_KEYS_INVALID"),
    ("BINARY_HOME_AWAY", {"HOME": "0.5", "AWAY": 0.5}, "FORWARD_EVIDENCE_PROBABILITY_DOMAIN_INVALID"),
])
def test_probability_vectors_are_validated_before_write(space, probs, code):
    with pytest.raises(fe.ForwardEvidenceBlocked) as err:
        fe.validate_probabilities(space, probs)
    assert err.value.code == code


def test_ncaaf_season_name_handles_january_bowls():
    assert NCAAFResultFormAdapter.season_for(datetime(2027, 1, 10, tzinfo=timezone.utc)) == 2026
    assert NCAAFResultFormAdapter.season_for(datetime(2026, 11, 25, tzinfo=timezone.utc)) == 2026


# --- migration structure -------------------------------------------------

def test_migration_tables_are_immutable_server_clocked_and_inert():
    for table in ("registrations", "captures", "settlements"):
        assert f"trg_wow_v17_forward_evidence_{table}_immutable" in SQL
        assert f"before update or delete on public.wow_v17_forward_evidence_{table}" in SQL
        assert f"enable row level security" in SQL
        assert (f"revoke update, delete, truncate, references, trigger\n  on table public.wow_v17_forward_evidence_{table} "
                f"from service_role;") in SQL
        assert f"grant select, insert on table public.wow_v17_forward_evidence_{table} to service_role;" in SQL
    assert SQL.count("new.created_at := pg_catalog.clock_timestamp();") == 3
    assert "check (created_at < event_start_time_utc)" in SQL
    assert "check (created_at > event_start_time_utc)" in SQL
    assert "FORWARD_EVIDENCE_CAPTURE_IDENTITY_MISMATCH" in SQL
    assert "FORWARD_EVIDENCE_PROBABILITIES_NOT_NORMALIZED" in SQL
    for flag in ("probability_publishable", "rank_eligible", "can_execute"):
        assert re.search(rf"{flag} boolean not null default false check \({flag} is false\)", SQL)
    assert "with (security_invoker = true)" in SQL
    assert "where cohort_role = 'EVALUATION'" in SQL
    assert "migration candidate, NOT APPLIED" in SQL


def test_migration_grants_nothing_to_client_roles_and_touches_no_reducer():
    lowered = SQL.lower()
    assert re.search(r"grant [^;]* to (public|anon|authenticated)", lowered) is None
    for forbidden in ("wow_v17_reduce_", "terminal_label", "drop table", "security definer",
                      "wow_team_event_specialist_certifications", "update public.", "delete from"):
        assert forbidden not in lowered, forbidden
<<<<<<< HEAD
=======


# --- soccer 1X2 adapter --------------------------------------------------

from v17 import soccer_openfootball_candidate as soccer_mod
from v17.forward_evidence_adapters import (SoccerOpenFootballAdapter, score_multinomial,
                                           soccer_forward_evidence_after_maintenance)


def test_multinomial_scorer_matches_sklearn_with_temperature():
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from v17.multiclass_candidate_lifecycle import _temperature_scale

    rng = np.random.default_rng(7)
    X = rng.normal(size=(300, 4)) * [1, 3, 0.5, 2] + [0, 5, -1, 2]
    y = np.array(["HOME", "DRAW", "AWAY"])[rng.integers(0, 3, size=300)]
    scaler = StandardScaler().fit(X)
    model = LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000, random_state=0).fit(scaler.transform(X), y)
    artifact = {"classes": [str(c) for c in model.classes_], "scaler_mean": scaler.mean_.tolist(),
                "scaler_scale": scaler.scale_.tolist(), "intercepts": model.intercept_.tolist(),
                "coefficients": model.coef_.tolist()}
    sample = X[:5]
    expected = _temperature_scale(model.predict_proba(scaler.transform(sample)), 1.37)
    for row, exp in zip(sample, expected):
        got = score_multinomial(artifact, {"temperature": 1.37}, row.tolist())
        for label, value in zip(artifact["classes"], exp):
            assert abs(got[label] - value) < 1e-12


SOCCER_NOW = datetime(2026, 11, 25, 12, 10, tzinfo=timezone.utc)


def soccer_candidate(created="2026-11-20T00:00:00+00:00"):
    names = list(soccer_mod.FEATURE_NAMES)
    artifact = {"model_family": "SOCCER_1X2_MULTINOMIAL_LOGIT_V1_EPL",
                "artifact_format": "STANDARDIZED_MULTINOMIAL_LOGISTIC_JSON_V1", "feature_names": names,
                "classes": ["AWAY", "DRAW", "HOME"], "scaler_mean": [0.5] * len(names), "scaler_scale": [1.0] * len(names),
                "intercepts": [-0.2, -0.4, 0.3],
                "coefficients": [[0.1 * ((i + k) % 3 - 1) for i in range(len(names))] for k in range(3)],
                "split_policy": "CHRONOLOGICAL_60_20_20"}
    return {"candidate_id": "soc-1", "sport": "SOCCER", "league": "EPL", "market_family": "OUTRIGHT_WINNER",
            "model_family": "SOCCER_1X2_MULTINOMIAL_LOGIT_V1_EPL",
            "feature_schema_version": soccer_mod.FEATURE_SCHEMA_VERSION, "source_policy_id": soccer_mod.SOURCE_POLICY_ID,
            "artifact_payload": artifact, "artifact_checksum": soccer_mod._hash_json(artifact),
            "calibrator_payload": {"method": "TEMPERATURE_SCALE_CHRONOLOGICAL_V1", "temperature": 1.1},
            "lifecycle_state": "CANDIDATE", "research_screen_pass": True, "active": False, "promoted": False,
            "probability_publishable": False, "can_execute": False, "automatic_certification": False,
            "automatic_promotion": False, "created_at": created, "training_dataset_hash": "e" * 64,
            "model_artifact_version": "SOCCER_1X2_MULTINOMIAL_LOGIT_V1_EPL_" + "e" * 16 + "_abcdef123456"}


def soccer_season(scores=None):
    teams = ["Alpha FC", "Beta FC", "Gamma FC", "Delta FC"]
    matches = []
    day = datetime(2026, 8, 15, tzinfo=timezone.utc)
    for week in range(12):
        for i in range(0, 4, 2):
            home, away = teams[(i + week) % 4], teams[(i + 1 + week) % 4]
            matches.append({"date": (day + timedelta(days=7 * week)).date().isoformat(), "time": "15:00",
                            "team1": home, "team2": away, "score": {"ft": [week % 3, (week + i) % 2]}})
    matches.append({"date": "2026-11-28", "time": "20:00", "team1": "Alpha FC", "team2": "Beta FC"})
    matches.append({"date": "2026-11-29", "time": "16:30", "team1": "Gamma FC", "team2": "New FC"})  # no history
    matches.append({"date": "2026-12-20", "time": "15:00", "team1": "Delta FC", "team2": "Gamma FC"})  # outside horizon
    for m in matches:
        key = (m["date"], m["team1"], m["team2"])
        if scores and key in scores:
            m["score"] = {"ft": list(scores[key])}
    return matches


def soccer_db():
    db = FakeDB(lambda: SOCCER_NOW)
    db.tables["wow_d1_candidate_artifacts"] = [soccer_candidate()]
    return db


def test_soccer_captures_three_way_with_conservative_pregame_anchor():
    db = soccer_db()
    adapter = SoccerOpenFootballAdapter("EPL", fetch_season=lambda code, season: soccer_season() if season == "2026-27" else [])
    receipt = fe.run_forward_evidence(db, adapter, now=SOCCER_NOW)
    assert receipt["status"] == "FORWARD_EVIDENCE_UPDATED", receipt
    assert receipt["captured"] == 1 and receipt["blocked_by_code"] == {"SOCCER_PRIOR_SAMPLE_INSUFFICIENT": 1}
    row = db.tables[fe.CAPTURE_TABLE][0]
    assert set(row["probabilities"]) == {"HOME", "DRAW", "AWAY"}
    assert abs(sum(row["probabilities"].values()) - 1) < 1e-9
    # 20:00 London (GMT in November) = 20:00Z kickoff; anchor keeps a two-hour safety margin.
    assert row["event_start_time_utc"] == "2026-11-28T18:00:00+00:00"
    assert db.tables[fe.REGISTRATION_TABLE][0]["outcome_space"] == "THREE_WAY_1X2"


def test_soccer_settles_draw_and_grades_multiclass_brier():
    db = soccer_db()
    adapter = SoccerOpenFootballAdapter("EPL", fetch_season=lambda code, season: soccer_season() if season == "2026-27" else [])
    fe.run_forward_evidence(db, adapter, now=SOCCER_NOW)
    later = datetime(2026, 11, 29, 12, 10, tzinfo=timezone.utc)
    db.clock = lambda: later
    scored = SoccerOpenFootballAdapter(
        "EPL", fetch_season=lambda code, season: soccer_season({("2026-11-28", "Alpha FC", "Beta FC"): (1, 1)})
        if season == "2026-27" else [])
    receipt = fe.run_forward_evidence(db, scored, now=later)
    assert receipt["settled"] == 1
    assert db.tables[fe.SETTLEMENT_TABLE][0]["outcome"] == "DRAW"
    p = db.tables[fe.CAPTURE_TABLE][0]["probabilities"]
    expected = (p["HOME"]) ** 2 + (p["AWAY"]) ** 2 + (p["DRAW"] - 1) ** 2
    assert abs(receipt["scorecard"]["brier"] - expected) < 1e-12


def test_soccer_anchor_uses_league_zone_and_falls_back_to_midnight():
    laliga = SoccerOpenFootballAdapter("LALIGA")
    event_id, anchor, _, _ = laliga._identity({"date": "2026-11-28", "time": "21:00", "team1": "A", "team2": "B"})
    assert anchor.isoformat() == "2026-11-28T18:00:00+00:00"  # 21:00 CET = 20:00Z, minus 2 h
    _, no_time, _, _ = laliga._identity({"date": "2026-11-28", "team1": "A", "team2": "B"})
    assert no_time.isoformat() == "2026-11-28T00:00:00+00:00"
    same_id, _, _, _ = laliga._identity({"date": "2026-11-28", "time": "18:30", "team1": "A", "team2": "B"})
    assert same_id == event_id  # a kickoff-time change keeps identity, so settlement still binds


def test_soccer_rejects_tampered_artifact():
    bad = soccer_candidate()
    bad["artifact_payload"]["intercepts"] = [0, 0, 0]
    with pytest.raises(fe.ForwardEvidenceBlocked) as err:
        SoccerOpenFootballAdapter("EPL").verify_candidate(bad, SOCCER_NOW)
    assert err.value.code == "SOCCER_ARTIFACT_CHECKSUM_MISMATCH"


def test_soccer_hook_isolates_each_league(monkeypatch):
    def fetch(self, code, season):
        if self.league != "EPL":
            raise fe.ForwardEvidenceBlocked("SOCCER_OPENFOOTBALL_HTTP_FAILED")
        return soccer_season() if season == "2026-27" else []

    monkeypatch.setattr(SoccerOpenFootballAdapter, "_fetch_season", fetch)
    db = soccer_db()
    real_run = fe.run_forward_evidence
    monkeypatch.setattr(fe, "run_forward_evidence", lambda db_, adapter: real_run(db_, adapter, now=SOCCER_NOW))
    out = soccer_forward_evidence_after_maintenance(db)
    assert out["status"] == "COMPLETED" and out["can_execute"] is False
    assert out["leagues"]["EPL"]["captured"] == 1
    others = {k: v for k, v in out["leagues"].items() if k != "EPL"}
    assert others and all(v["status"] in ("BLOCKED", "FAILED") for v in others.values())


# --- NHL adapter (reuses the fitted-specialist test fixtures) -------------

import sys as _sys

_sys.path.insert(0, str(Path(__file__).parent))
import test_v17_nhl_fitted_specialist as nhlt  # noqa: E402
import v17.nhl_fitted_specialist as nhl_mod  # noqa: E402
from v17.forward_evidence_adapters import NHLRegularSeasonAdapter  # noqa: E402


@pytest.fixture
def nhl_receipts():
    def provider(*, season, event_start_time, home_team="", away_team="", session=None, db=None, as_of_utc=None, **_):
        return nhlt._timeline_receipt(nhlt._events(), target_start=event_start_time, season=str(season))

    nhl_mod.install_nhl_timeline_receipt_provider(provider)
    yield
    nhl_mod.reset_nhl_timeline_receipt_provider()


def nhl_schedule(team, season_id):
    return [{"id": 2026029001, "gameType": 2, "startTimeUTC": nhlt.START.isoformat().replace("+00:00", "Z"),
             "homeTeam": {"abbrev": "BOS"}, "awayTeam": {"abbrev": "NYR"}, "neutralSite": False},
            {"id": 2026019002, "gameType": 1, "startTimeUTC": nhlt.START.isoformat(),  # preseason: excluded
             "homeTeam": {"abbrev": "TOR"}, "awayTeam": {"abbrev": "MTL"}, "neutralSite": False}]


def nhl_db():
    db = FakeDB(lambda: nhlt.NOW)
    db.tables["wow_d1_candidate_artifacts"] = [nhlt._candidate_row()]
    db.tables["wow_d1_source_events"] = nhlt._events()
    return db


def test_nhl_capture_equals_specialist_probability(nhl_receipts):
    db = nhl_db()
    receipt = fe.run_forward_evidence(db, NHLRegularSeasonAdapter(fetch_club_schedule=nhl_schedule), now=nhlt.NOW)
    assert receipt["status"] == "FORWARD_EVIDENCE_UPDATED", receipt
    assert receipt["captured"] == 1 and receipt["events_considered"] == 1
    row = db.tables[fe.CAPTURE_TABLE][0]
    expected = nhl_mod.score_nhl_fitted_candidate(
        nhl_mod._verify_candidate(nhlt._candidate_row()), nhlt._reconstruction(), event_key="NHL:2026029001",
        evidence={})["calibrated_home_probability"]
    assert row["official_event_id"] == "2026029001"
    assert row["probabilities"]["HOME"] == expected
    assert row["home_participant"] == "BOS" and row["away_participant"] == "NYR"


def test_nhl_settles_from_official_finals(nhl_receipts):
    db = nhl_db()
    adapter = NHLRegularSeasonAdapter(fetch_club_schedule=nhl_schedule)
    fe.run_forward_evidence(db, adapter, now=nhlt.NOW)
    later = nhlt.START + timedelta(hours=6)
    db.clock = lambda: later
    final = nhlt._event(50, "BOS", "NYR", 1, 4)
    final.update(official_event_id="2026029001", event_start_time=nhlt.START.isoformat(),
                 source_retrieved_at=(nhlt.START + timedelta(hours=4)).isoformat())
    db.tables["wow_d1_source_events"].append(final)
    receipt = fe.run_forward_evidence(db, adapter, now=later)
    assert receipt["settled"] == 1 and db.tables[fe.SETTLEMENT_TABLE][0]["outcome"] == "AWAY"
    assert receipt["scorecard"]["graded_n"] == 1


def test_nhl_schedule_outage_is_typed():
    def down(team, season_id):
        raise RuntimeError("503")

    receipt = fe.run_forward_evidence(nhl_db(), NHLRegularSeasonAdapter(fetch_club_schedule=down), now=nhlt.NOW)
    assert receipt["status"] == "BLOCKED" and receipt["code"] == "NHL_OFFICIAL_SCHEDULE_UNAVAILABLE"


# --- NCAAB adapter -------------------------------------------------------

from v17 import ncaab_sportsdataverse_candidate as ncaab_mod  # noqa: E402
from v17.forward_evidence_adapters import NCAABResultFormAdapter  # noqa: E402

NCAAB_NOW = datetime(2026, 11, 25, 12, 10, tzinfo=timezone.utc)
NCAAB_TIP = datetime(2026, 11, 26, 0, 30, tzinfo=timezone.utc)


def ncaab_candidate():
    names = list(ncaab_mod.FEATURE_NAMES)
    artifact = {"model_family": "NCAAB_RESULT_FORM_LOGIT_V1", "artifact_format": "STANDARDIZED_LOGISTIC_JSON_V1",
                "feature_names": names, "scaler_mean": [1.0] * len(names), "scaler_scale": [2.0] * len(names),
                "intercept": 0.2, "coefficients": [0.05 * ((i % 3) - 1) for i in range(len(names))],
                "split_policy": "CHRONOLOGICAL_60_20_20"}
    return {"candidate_id": "ncaab-1", "sport": "NCAAB", "league": "NCAAB", "market_family": "OUTRIGHT_WINNER",
            "model_family": "NCAAB_RESULT_FORM_LOGIT_V1", "feature_schema_version": ncaab_mod.FEATURE_SCHEMA_VERSION,
            "source_policy_id": ncaab_mod.SOURCE_POLICY_ID, "artifact_payload": artifact,
            "artifact_checksum": ncaab_mod._json_hash(artifact),
            "calibrator_payload": {"method": "EMPIRICAL_WILSON_BINS_V1",
                                   "bins": [{"raw_mean": 0.3, "calibrated_probability": 0.35},
                                            {"raw_mean": 0.6, "calibrated_probability": 0.58}]},
            "lifecycle_state": "CANDIDATE", "research_screen_pass": True, "active": False, "promoted": False,
            "probability_publishable": False, "can_execute": False, "automatic_certification": False,
            "automatic_promotion": False, "created_at": "2026-11-20T00:00:00+00:00", "training_dataset_hash": "f" * 64,
            "model_artifact_version": "NCAAB_RESULT_FORM_LOGIT_V1_" + "f" * 16 + "_abcdef123456"}


def ncaab_games(extra=()):
    def side(team, score, reb):
        return {"team_id": team, "team_score": str(score), "total_rebounds": str(reb), "turnovers": "11",
                "field_goal_pct": "45.0", "three_point_field_goal_pct": "33.0"}
    games = []
    for i in range(8):
        for home, away in (("2250", "9"), ("2", "52")):
            start = NCAAB_NOW - timedelta(days=30 - 3 * i)
            hs, as_ = 70 + i, 65 + (i % 4) * 3
            games.append({"game_id": f"g{home}-{i}", "event_start": start, "home_team_id": home, "away_team_id": away,
                          "home_score": hs, "away_score": as_, "home": side(home, hs, 35), "away": side(away, as_, 31)})
    return games + list(extra)


def ncaab_schedule(season):
    assert season == 2027
    base = {"time_valid": "true", "status_type_completed": "false", "neutral_site": "false"}
    return [{**base, "id": "401900001", "start_date": "2026-11-26T00:30Z", "home_id": "2250", "away_id": "2"},
            {**base, "id": "401900002", "start_date": "2026-11-26T02:00Z", "home_id": "2250", "away_id": "777"},
            {**base, "id": "401900003", "start_date": "2026-11-27T02:00Z", "home_id": "9", "away_id": "52",
             "time_valid": "false"},
            {**base, "id": "401900004", "start_date": "2026-11-20T02:00Z", "home_id": "9", "away_id": "52"}]


def ncaab_db():
    db = FakeDB(lambda: NCAAB_NOW)
    db.tables["wow_d1_candidate_artifacts"] = [ncaab_candidate()]
    return db


def test_ncaab_captures_with_calibrated_probability_and_settles():
    from v17.binary_candidate_lifecycle import _map_calibrator
    import numpy as np

    db = ncaab_db()
    adapter = NCAABResultFormAdapter(fetch_schedule=ncaab_schedule, fetch_games=lambda seasons: ncaab_games())
    receipt = fe.run_forward_evidence(db, adapter, now=NCAAB_NOW)
    assert receipt["status"] == "FORWARD_EVIDENCE_UPDATED", receipt
    assert receipt["captured"] == 1 and receipt["blocked_by_code"] == {"NCAAB_PRIOR_SAMPLE_INSUFFICIENT": 1}
    row = db.tables[fe.CAPTURE_TABLE][0]
    assert row["official_event_id"] == "401900001"
    assert row["probabilities"]["HOME"] in (0.35, 0.58)  # an empirical bin value, so calibration was applied
    assert set(row["features"]) == set(ncaab_mod.FEATURE_NAMES)

    later = NCAAB_TIP + timedelta(hours=5)
    db.clock = lambda: later
    final = {"game_id": "401900001", "event_start": NCAAB_TIP, "home_team_id": "2250", "away_team_id": "2",
             "home_score": 61, "away_score": 66, "home": {}, "away": {}}
    settled = NCAABResultFormAdapter(fetch_schedule=ncaab_schedule, fetch_games=lambda seasons: ncaab_games([final]))
    receipt = fe.run_forward_evidence(db, settled, now=later)
    assert receipt["settled"] == 1 and db.tables[fe.SETTLEMENT_TABLE][0]["outcome"] == "AWAY"


def test_ncaab_history_source_failure_is_typed():
    def boom(seasons):
        raise ncaab_mod.NCAABCandidateUnavailable("NCAAB_SPORTSDATAVERSE_HTTP_FAILED", "503")

    receipt = fe.run_forward_evidence(ncaab_db(), NCAABResultFormAdapter(fetch_schedule=ncaab_schedule, fetch_games=boom),
                                      now=NCAAB_NOW)
    assert receipt["status"] == "BLOCKED" and receipt["code"] == "NCAAB_SPORTSDATAVERSE_HTTP_FAILED"


def test_ncaab_season_naming():
    assert NCAABResultFormAdapter.season_for(datetime(2026, 11, 25, tzinfo=timezone.utc)) == 2027
    assert NCAABResultFormAdapter.season_for(datetime(2027, 3, 1, tzinfo=timezone.utc)) == 2027


def test_view_least_privilege_migration_leaves_service_role_select_only():
    sql = (ROOT / "migrations" / "20261125_v17_forward_evidence_view_least_privilege.sql").read_text().lower()
    assert ("revoke insert, update, delete, truncate, references, trigger\n"
            "  on table public.wow_v17_forward_evidence_graded from service_role;") in sql
    assert "grant select on table public.wow_v17_forward_evidence_graded to service_role;" in sql
    assert re.search(r"grant [^;]* to (public|anon|authenticated)", sql) is None
>>>>>>> origin/main
