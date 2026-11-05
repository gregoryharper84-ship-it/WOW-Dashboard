"""Daily NFL autonomous prop discovery: accounting, ceilings, fail-closed behavior."""
from datetime import datetime, timezone

import v17.daily_nfl_prop_acquisition as nfl_mod
import v17.daily_prop_acquisition as daily
from prop_auto_hydration import PropAutoHydrationError

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
DATE = "2026-11-08"


def _cand(event, player, stat, start="2026-11-08T18:00:00+00:00"):
    return {
        "canonical_event_id": event, "provider_event_id": "1", "event_start_time": start,
        "player": player, "stat_type": stat, "team": "BAL", "opponent": "JAX", "position": "RB",
    }


class _Query:
    def __init__(self, db):
        self.db = db

    def upsert(self, row, on_conflict=None):
        self.db.writes.append(row)
        if self.db.fail_writes:
            raise RuntimeError("write boom")
        return self

    def execute(self):
        return self


class _DB:
    def __init__(self, fail_writes=False):
        self.writes, self.fail_writes = [], fail_writes

    def table(self, _name):
        return _Query(self)


def _patch(monkeypatch, candidates, hydrate=None, existing=lambda *_: False):
    monkeypatch.setattr(nfl_mod._cp, "_discover_candidates", lambda *_a, **_k: candidates)
    monkeypatch.setattr(nfl_mod._cp, "_existing_snapshot", existing)
    monkeypatch.setattr(nfl_mod, "auto_hydrate_prop_evidence", hydrate or (lambda **k: {"game_log": [60.0] * 10}))
    monkeypatch.setattr(nfl_mod.RawPropEvidence, "model_validate", staticmethod(lambda raw: type("E", (), {"game_log": raw["game_log"]})()))
    monkeypatch.setattr(nfl_mod, "PickRequestRow", lambda **k: type("R", (), k)())
    monkeypatch.setattr(nfl_mod, "_validate_evidence", lambda row, stat: {})
    monkeypatch.setattr(
        nfl_mod, "_snapshot_payload",
        lambda row, n: (f"snap-{row.player}-{row.stat_type}", "fp", {"line": row.line, "player": row.player, "stat_type": row.stat_type}),
    )


def _run(db, max_candidates=50):
    return nfl_mod.acquire_daily_nfl_prop_snapshots(
        db=db, requested_date=DATE, requested_timezone="America/Chicago",
        max_candidates=max_candidates, now=NOW, http_get=lambda *a, **k: None,
    )


def _balanced(r):
    return r["attempted"] == r["snapshot_write_succeeded"] + r["snapshot_write_failed"] + r["explicit_prewrite_exclusions"]


def test_persists_discovery_rows_with_balanced_receipts(monkeypatch):
    _patch(monkeypatch, [_cand("E1", "A", "RUSHING_YARDS"), _cand("E1", "A", "ANYTIME_TD")])
    db = _DB()
    r = _run(db)
    assert r["persisted"] == 2 and _balanced(r) and r["can_execute"] is False
    assert r["probability_publishable"] is False
    assert {w["line"] for w in db.writes} == {60.5, 0.5}
    assert all(x["source_type"] == "AUTONOMOUS_DISCOVERY" and x["write_status"] == "SNAPSHOT_WRITE_SUCCEEDED" for x in r["receipts"])


def test_hydration_failure_is_typed_exclusion_never_fabricated(monkeypatch):
    def hydrate(**k):
        raise PropAutoHydrationError("INSUFFICIENT_PRIOR_GAMES", "short history")

    _patch(monkeypatch, [_cand("E1", "A", "RUSHING_YARDS")], hydrate=hydrate)
    db = _DB()
    r = _run(db)
    assert db.writes == [] and r["persisted"] == 0 and _balanced(r)
    assert r["receipts"][0]["terminal_reason"] == "INSUFFICIENT_PRIOR_GAMES"
    assert r["status"] == "COMPLETED_WITH_ROW_BLOCKERS"


def test_write_failure_is_counted_and_run_invalid_when_total(monkeypatch):
    _patch(monkeypatch, [_cand("E1", "A", "RUSHING_YARDS")])
    r = _run(_DB(fail_writes=True))
    assert r["snapshot_write_failed"] == 1 and _balanced(r)
    assert r["status"] == "RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE"


def test_row_ceiling_round_robins_events_and_reports_unattempted(monkeypatch):
    cands = [_cand("E1", f"P{i}", "RUSHING_YARDS") for i in range(3)]
    cands.append(_cand("E2", "Q", "RUSHING_YARDS", "2026-11-08T21:00:00+00:00"))
    _patch(monkeypatch, cands)
    r = _run(_DB(), max_candidates=2)
    assert {x["event_id"] for x in r["receipts"]} == {"E1", "E2"}
    assert r["attempted"] == 2 and r["unattempted_due_to_row_ceiling"] == 2


def test_local_slate_date_filter_excludes_other_days(monkeypatch):
    _patch(monkeypatch, [_cand("E1", "A", "RUSHING_YARDS", "2026-11-12T18:00:00+00:00")])
    r = _run(_DB())
    assert r["attempted"] == 0 and r["discovered_candidates"] == 0


def test_already_captured_is_not_charged_to_attempt_budget(monkeypatch):
    _patch(monkeypatch, [_cand("E1", "A", "RUSHING_YARDS")], existing=lambda *_: True)
    r = _run(_DB())
    assert _balanced(r) and r["already_captured_candidates"] == 1
    assert r["attempted"] == 0 and r["receipts"] == []
    assert r["unattempted_due_to_row_ceiling"] == 0 and r["can_execute"] is False


def test_discovery_outage_is_data_unobtainable(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("HTTP_503")

    monkeypatch.setattr(nfl_mod._cp, "_discover_candidates", boom)
    r = _run(_DB())
    assert r["status"] == "DATA_UNOBTAINABLE" and r["attempted"] == 0
    assert r["blockers"] == ["NFL_PROP_DISCOVERY_FAILED:RuntimeError"]


def _leg(sport, status="COMPLETED", attempted=0, persisted=0, **extra):
    return {
        "status": status, "attempted": attempted, "persisted": persisted, "held": 0,
        "hydrated": persisted, "snapshot_write_succeeded": persisted, "snapshot_write_failed": 0,
        "explicit_prewrite_exclusions": 0, "receipts": [], "blockers": [], "stat_types": [sport],
        "candidate_source": sport,
        "line_source": "PRIOR10_MEDIAN_HALF_POINT_DISCOVERY_ONLY", **extra,
    }


def _composite():
    return daily.acquire_daily_prop_snapshots(db=None, requested_date=DATE, requested_timezone="UTC", max_candidates=10)


def test_composite_shares_one_ceiling_and_reports_per_sport(monkeypatch):
    seen = {}

    def mlb(**k):
        seen["mlb"] = k["max_candidates"]
        return _leg("MLB", attempted=4, persisted=4)

    def nfl(**k):
        seen["nfl"] = k["max_candidates"]
        return _leg("NFL", attempted=6, persisted=6)

    monkeypatch.setattr(daily, "acquire_daily_mlb_prop_snapshots", mlb)
    monkeypatch.setattr(nfl_mod, "acquire_daily_nfl_prop_snapshots", nfl)
    r = _composite()
    assert seen == {"mlb": 10, "nfl": 6}
    assert r["attempted"] == 10 and r["persisted"] == 10 and r["status"] == "COMPLETED"
    assert r["line_source"] == "PRIOR10_MEDIAN_HALF_POINT_DISCOVERY_ONLY"
    assert r["probability_publishable"] is False
    assert [c["sport"] for c in r["sport_coverage"]] == ["MLB", "NFL"]
    assert {"NBA", "NHL"} <= set(r["sports_without_autonomous_discovery"])
    assert r["can_execute"] is False


def test_composite_one_sport_outage_is_visible_not_fatal(monkeypatch):
    monkeypatch.setattr(
        daily, "acquire_daily_mlb_prop_snapshots",
        lambda **k: _leg("MLB", "DATA_UNOBTAINABLE", blockers=["MLB_PROP_SCHEDULE_ACQUISITION_FAILED:X"]),
    )
    monkeypatch.setattr(nfl_mod, "acquire_daily_nfl_prop_snapshots", lambda **k: _leg("NFL", attempted=2, persisted=2))
    r = _composite()
    assert r["status"] == "COMPLETED_WITH_ROW_BLOCKERS" and r["persisted"] == 2
    assert "MLB_PROP_SCHEDULE_ACQUISITION_FAILED:X" in r["blockers"]


def test_composite_all_outage_stays_data_unobtainable(monkeypatch):
    monkeypatch.setattr(daily, "acquire_daily_mlb_prop_snapshots", lambda **k: _leg("MLB", "DATA_UNOBTAINABLE"))
    monkeypatch.setattr(nfl_mod, "acquire_daily_nfl_prop_snapshots", lambda **k: _leg("NFL", "DATA_UNOBTAINABLE"))
    assert _composite()["status"] == "DATA_UNOBTAINABLE"


def test_nfl_zero_budget_does_not_claim_a_proven_empty_slate(monkeypatch):
    def unexpected_discovery(*_args, **_kwargs):
        raise AssertionError("No discovery source call is permitted at zero budget")

    monkeypatch.setattr(nfl_mod._cp, "_discover_candidates", unexpected_discovery)
    r = _run(_DB(), max_candidates=0)
    assert r["status"] == "NOT_ATTEMPTED_ROW_CEILING"
    assert r["discovered_candidates"] is None
    assert r["unattempted_due_to_row_ceiling"] is None
    assert r["blockers"] == ["NFL_PROP_ROW_CEILING_EXHAUSTED"]
    assert r["attempted"] == 0 and r["receipts"] == [] and r["can_execute"] is False


def test_mlb_exhaustion_keeps_nfl_discovery_unknown_in_daily_receipt(monkeypatch):
    from v17.prop_sport_parity import prop_sport_coverage_receipt

    monkeypatch.setattr(daily, "acquire_daily_mlb_prop_snapshots",
                        lambda **k: _leg("MLB", attempted=10, persisted=10))
    def unexpected_discovery(*_args, **_kwargs):
        raise AssertionError("NFL discovery was not attempted")
    monkeypatch.setattr(nfl_mod._cp, "_discover_candidates", unexpected_discovery)
    combined = _composite()
    nfl = next(leg for leg in combined["sport_coverage"] if leg["sport"] == "NFL")
    assert combined["status"] == "COMPLETED_WITH_ROW_BLOCKERS"
    assert nfl["status"] == "NOT_ATTEMPTED_ROW_CEILING"
    assert nfl["unattempted_due_to_row_ceiling"] is None
    assert "NFL_PROP_ROW_CEILING_EXHAUSTED" in combined["blockers"]
    receipt = prop_sport_coverage_receipt(
        requested_sports=["NFL"], rows=[], acquisition=combined,
    )
    row = receipt["per_sport"]["NFL"]
    assert row["coverage_status"] == "NOT_ATTEMPTED_ROW_CEILING"
    assert row["discovery_attempt_status"] == "NOT_ATTEMPTED_ROW_CEILING"
    assert row["unattempted_due_to_row_ceiling"] is None
    assert row["zero_rows_is_not_empty_slate_proof"] is True
    assert row["can_execute"] is False and receipt["can_execute"] is False


def test_rerun_preflight_reaches_next_uncaptured_candidate(monkeypatch):
    """Previously captured row-prefixes cannot starve fresh NFL snapshots."""
    cands = [_cand("E1", "A", "RUSHING_YARDS"), _cand("E1", "B", "RUSHING_YARDS"),
             _cand("E1", "C", "RUSHING_YARDS")]
    _patch(monkeypatch, cands, existing=lambda _db, c: c["player"] in ("A", "B"))
    r = _run(_DB(), max_candidates=1)
    assert r["already_captured_candidates"] == 2 and r["discovered_candidates"] == 3
    assert r["attempted"] == 1 and r["persisted"] == 1 and _balanced(r)
    assert [receipt["player"] for receipt in r["receipts"]] == ["C"]
    assert r["unattempted_due_to_row_ceiling"] == 0 and r["can_execute"] is False


def test_preflight_error_halts_nfl_writes_and_preserves_unknown_counts(monkeypatch):
    def broken(_db, _candidate):
        raise RuntimeError("DB_DOWN")
    _patch(monkeypatch, [_cand("E1", "A", "RUSHING_YARDS")], existing=broken)
    db = _DB()
    r = _run(db, max_candidates=1)
    assert r["status"] == "DATA_UNOBTAINABLE"
    assert r["attempted"] == 0 and not db.writes
    assert r["unattempted_due_to_row_ceiling"] is None
    assert r["blockers"] == ["NFL_PROP_SNAPSHOT_PREFLIGHT_FAILED:RuntimeError"]
