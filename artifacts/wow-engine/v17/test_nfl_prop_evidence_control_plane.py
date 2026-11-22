from __future__ import annotations

from datetime import datetime, timezone

import pytest

from v17 import nfl_prop_evidence_control_plane as subject

NOW = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
EVENT = datetime(2026, 9, 28, 23, 0, tzinfo=timezone.utc)


class _Result:
    def __init__(self, data): self.data = data


class _Query:
    def __init__(self, db):
        self.db = db
        self.filters = {}
        self.pending = None
    def select(self, *_a, **_k): return self
    def eq(self, key, value): self.filters[key] = value; return self
    def limit(self, _n): return self
    def order(self, *_args, **_kwargs): return self
    def upsert(self, row, on_conflict=None):
        self.pending = dict(row); self.db.upserts.append((self.pending, on_conflict)); return self
    def execute(self):
        if self.pending is not None: return _Result([self.pending])
        identity = (
            self.filters.get("event_id"), self.filters.get("sport"),
            self.filters.get("player"), self.filters.get("stat_type"),
        )
        if identity not in self.db.existing:
            return _Result([])
        ready = {
            "source_snapshot_id": "existing", "captured_at": NOW.isoformat(),
            "hydration_status": "PASS", "blockers": [],
            "role_status": {
                "availability": {
                    "designation": "AVAILABLE",
                    "source": "OFFICIAL_ESPN_REPORT",
                    "captured_at": NOW.isoformat(), "evidence_status": "RETRIEVED",
                    "fresh": True, "report_complete": True,
                    "athlete_id": "test-athlete", "event_id": "2026_04_CHI_PHI",
                    "team": "PHI",
                },
            },
        }
        return _Result([dict(self.db.existing_row or ready)])


class _DB:
    def __init__(self, existing=None, existing_row=None):
        self.existing=set(existing or []); self.existing_row=existing_row; self.upserts=[]
    def table(self, name):
        assert name == "wow_prop_evidence_snapshots"; return _Query(self)


def _candidate(player="Alpha QB", stat="PASSING_YARDS", pos="QB"):
    return {
        "provider_event_id":"401-test", "canonical_event_id":"2026_04_CHI_PHI",
        "event_start_time":EVENT.isoformat(), "teams":[{"team_id":"1","abbr":"PHI"},{"team_id":"2","abbr":"CHI"}],
        "player":player, "player_id":player.replace(" ","-"), "position":pos,
        "team":"PHI", "opponent":"CHI", "stat_type":stat,
    }


def _raw(stat_type):
    base={"PASSING_YARDS":250.0,"RUSHING_YARDS":45.0,"RECEIVING_YARDS":65.0,"ANYTIME_TD":0.0}[stat_type]
    return {
        "captured_at":NOW.isoformat(),
        "game_log":[base + float(i%3) for i in range(10)],
        "box_score_log":[{"game":i} for i in range(10)],
        "role_status":{"status":"ACTIVE","opponent":"CHI","provider_event_id":"401-test","verified_canonical_event_id":"2026_04_CHI_PHI"},
        "role_timestamp":NOW.isoformat(),
        "opportunity_ledger":{"status":"READY"},
        "source_timestamps":{"ESPN_NFL_IDENTITY_SCOREBOARD":NOW.isoformat(),"NFLVERSE_WEEKLY_PLAYER_STATS":NOW.isoformat()},
        "evidence_version":"PROP_EVIDENCE_V1",
        "rate_provenance":"NFL_ESPN_IDENTITY_NFLVERSE_STATS_V1",
    }


def _install(monkeypatch, candidates):
    monkeypatch.setattr(subject, "_discover_candidates", lambda *_a, **_k: list(candidates))
    monkeypatch.setattr(subject, "auto_hydrate_prop_evidence", lambda **kwargs: _raw(kwargs["stat_type"]))
    monkeypatch.setattr(subject, "_validate_evidence", lambda row, stat: row.evidence.model_dump())
    monkeypatch.setattr(subject, "_snapshot_payload", lambda row, normalized: (
        f"snapshot-{row.player}-{row.stat_type}", "fp",
        {"source_snapshot_id":f"snapshot-{row.player}-{row.stat_type}","event_id":row.event_id,"event_start_time":row.event_start_time,"sport":row.sport,"player":row.player,"stat_type":row.stat_type,"line":row.line},
    ))


def test_position_route_map_stays_core_only():
    assert subject.POSITION_STATS["QB"] == ("PASSING_YARDS","RUSHING_YARDS","ANYTIME_TD")
    assert subject.POSITION_STATS["WR"] == ("RECEIVING_YARDS","ANYTIME_TD")
    assert set(subject.STAT_TYPES) == {"PASSING_YARDS","RUSHING_YARDS","RECEIVING_YARDS","ANYTIME_TD"}


def test_anytime_td_discovery_line_is_exact_half():
    assert subject._candidate_line([0,0,0,1,0,1,0,0,1,0], "ANYTIME_TD") == 0.5


def test_bounded_window_and_authority_invariants(monkeypatch: pytest.MonkeyPatch):
    candidates=[
        _candidate("Alpha QB","PASSING_YARDS","QB"),
        _candidate("Alpha QB","RUSHING_YARDS","QB"),
        _candidate("Alpha QB","ANYTIME_TD","QB"),
        _candidate("Beta WR","RECEIVING_YARDS","WR"),
        _candidate("Beta WR","ANYTIME_TD","WR"),
    ]
    _install(monkeypatch,candidates)
    result=subject.acquire_nfl_forward_evidence_batch(
        subject.NFLForwardEvidenceRequest(requested_date="2026-09-28",candidate_offset=1,max_candidates=3),
        db=_DB(), now=NOW, http_get=lambda *_a,**_k: object(),
    )
    assert result["total_candidates"]==5
    assert result["attempted"]==3
    assert result["persisted"]==3
    assert result["next_offset"]==4
    assert result["probability_publishable"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["can_execute"] is False


def test_existing_exact_identity_skips_hydration(monkeypatch: pytest.MonkeyPatch):
    candidate=_candidate()
    _install(monkeypatch,[candidate])
    calls=[]
    monkeypatch.setattr(subject,"auto_hydrate_prop_evidence",lambda **kwargs: calls.append(kwargs) or _raw(kwargs["stat_type"]))
    db=_DB(existing={(candidate["canonical_event_id"],"NFL",candidate["player"],candidate["stat_type"])})
    result=subject.acquire_nfl_forward_evidence_batch(
        subject.NFLForwardEvidenceRequest(requested_date="2026-09-28",max_candidates=1),
        db=db, now=NOW, http_get=lambda *_a,**_k: object(),
    )
    assert result["already_captured"]==1
    assert result["persisted"]==0
    assert calls==[]


def test_typed_identity_hydration_failure_is_row_scoped(monkeypatch: pytest.MonkeyPatch):
    candidates=[_candidate(),_candidate("Beta WR","RECEIVING_YARDS","WR")]
    _install(monkeypatch,candidates)
    def hydrate(**kwargs):
        if kwargs["player"]=="Alpha QB":
            raise subject.PropAutoHydrationError("PROP_PLAYER_IDENTITY_UNRESOLVED","held")
        return _raw(kwargs["stat_type"])
    monkeypatch.setattr(subject,"auto_hydrate_prop_evidence",hydrate)
    result=subject.acquire_nfl_forward_evidence_batch(
        subject.NFLForwardEvidenceRequest(requested_date="2026-09-28",max_candidates=2),
        db=_DB(), now=NOW, http_get=lambda *_a,**_k: object(),
    )
    assert result["persisted"]==1
    assert result["held"]==1
    assert result["status"]=="COMPLETED_WITH_ROW_BLOCKERS"
    assert any("PROP_PLAYER_IDENTITY_UNRESOLVED" in value for value in result["blockers"])


def test_evidence_validation_value_error_is_typed_row_hold_not_write_failure(monkeypatch: pytest.MonkeyPatch):
    """Regression: nightly NFL forward acquisition reported RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE
    with only `ValueError` because governed validation reasons were swallowed by the catch-all."""
    candidates = [_candidate(), _candidate("Beta WR", "RECEIVING_YARDS", "WR")]
    _install(monkeypatch, candidates)

    def validate(row, stat):
        raise ValueError("L10_GAME_LOG_INCOMPLETE")

    monkeypatch.setattr(subject, "_validate_evidence", validate)
    db = _DB()
    result = subject.acquire_nfl_forward_evidence_batch(
        subject.NFLForwardEvidenceRequest(requested_date="2026-09-28", max_candidates=2),
        db=db, now=NOW, http_get=lambda *_a, **_k: object(),
    )
    assert result["status"] == "COMPLETED_WITH_ROW_BLOCKERS"
    assert result["snapshot_write_failed"] == 0 and result["persisted"] == 0 and result["held"] == 2
    assert db.upserts == []  # nothing written for a held row
    assert all("NFL_FORWARD_EVIDENCE_INVALID:L10_GAME_LOG_INCOMPLETE" in b for b in result["blockers"])
    assert result["can_execute"] is False


def test_genuine_snapshot_write_value_error_still_fails_the_run(monkeypatch: pytest.MonkeyPatch):
    _install(monkeypatch, [_candidate()])

    class _BadDB(_DB):
        def table(self, name):
            q = super().table(name)
            def upsert(row, on_conflict=None):
                raise ValueError("bad write")
            q.upsert = upsert
            return q

    result = subject.acquire_nfl_forward_evidence_batch(
        subject.NFLForwardEvidenceRequest(requested_date="2026-09-28", max_candidates=1),
        db=_BadDB(), now=NOW, http_get=lambda *_a, **_k: object(),
    )
    assert result["status"] == "RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE"
    assert result["snapshot_write_failed"] == 1



def test_rotating_forward_acquisition_covers_all_pages_without_blank_windows(monkeypatch: pytest.MonkeyPatch):
    candidates = [_candidate(f"Quarterback {idx}") for idx in range(5)]
    _install(monkeypatch, candidates)
    # Five candidates in pages of two: rotations must cover all three pages,
    # then wrap. The old run_number % 10 strategy silently skipped later pages.
    for rotation, expected_offset, expected_count, expected_next in (
        (0, 0, 2, 2),
        (1, 2, 2, 4),
        (2, 4, 1, None),
        (3, 0, 2, 2),
        (9, 0, 2, 2),
    ):
        db = _DB()
        result = subject.acquire_nfl_forward_evidence_batch(
            subject.NFLForwardEvidenceRequest(
                requested_date="2026-09-28", candidate_rotation=rotation, max_candidates=2,
            ),
            db=db, now=NOW, http_get=lambda *_a, **_k: object(),
        )
        assert result["candidate_offset"] == expected_offset
        assert result["window_candidate_n"] == expected_count
        assert result["persisted"] == expected_count
        assert result["attempted"] == expected_count
        assert result["next_offset"] == expected_next
        assert result["rotation_page_n"] == 3
        assert result["can_execute"] is False
        assert result["probability_publishable"] is False


def test_rotating_forward_acquisition_empty_slate_is_no_events_not_error(monkeypatch: pytest.MonkeyPatch):
    _install(monkeypatch, [])
    result = subject.acquire_nfl_forward_evidence_batch(
        subject.NFLForwardEvidenceRequest(
            requested_date="2026-09-28", candidate_rotation=999_999, max_candidates=2,
        ),
        db=_DB(), now=NOW, http_get=lambda *_a, **_k: object(),
    )
    assert result["total_candidates"] == 0
    assert result["window_candidate_n"] == 0
    assert result["rotation_page_n"] == 0
    assert result["next_offset"] is None
    assert result["status"] == "COMPLETED"


def test_legacy_explicit_offset_still_means_exact_offset_not_rotation(monkeypatch: pytest.MonkeyPatch):
    _install(monkeypatch, [_candidate(f"Quarterback {idx}") for idx in range(5)])
    result = subject.acquire_nfl_forward_evidence_batch(
        subject.NFLForwardEvidenceRequest(
            requested_date="2026-09-28", candidate_offset=3, max_candidates=2,
        ),
        db=_DB(), now=NOW, http_get=lambda *_a, **_k: object(),
    )
    assert result["candidate_offset"] == 3
    assert result["persisted"] == 2


def test_explicit_offset_and_rotation_cannot_silently_override_each_other():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        subject.NFLForwardEvidenceRequest(
            requested_date="2026-09-28",
            candidate_offset=32,
            candidate_rotation=1,
        )


def test_nfl_forward_receipts_reconcile_every_candidate(monkeypatch):
    candidates = [
        _candidate("Alpha QB", "PASSING_YARDS"),
        _candidate("Beta WR", "RECEIVING_YARDS", "WR"),
    ]
    _install(monkeypatch, candidates)
    monkeypatch.setattr(
        subject, "auto_hydrate_prop_evidence",
        lambda **kwargs: (
            _raw(kwargs["stat_type"]) if kwargs["player"] == "Alpha QB"
            else (_ for _ in ()).throw(
                subject.PropAutoHydrationError("PLAYER_AVAILABILITY_UNVERIFIED", "held")
            )
        ),
    )
    result = subject.acquire_nfl_forward_evidence_batch(
        subject.NFLForwardEvidenceRequest(requested_date="2026-09-28", max_candidates=2),
        db=_DB(), now=NOW, http_get=lambda *_a, **_k: object(),
    )
    assert len(result["receipts"]) == result["attempted"] == 2
    assert result["snapshot_write_succeeded"] == 1
    assert result["explicit_prewrite_exclusions"] == 1
    assert result["snapshot_write_failed"] == 0
    assert sorted(receipt["write_status"] for receipt in result["receipts"]) == [
        "PREWRITE_EXCLUDED", "SNAPSHOT_WRITE_SUCCEEDED",
    ]
    assert all(receipt["can_execute"] is False for receipt in result["receipts"])
    assert sum(
        receipt["write_status"] == "SNAPSHOT_WRITE_SUCCEEDED"
        for receipt in result["receipts"]
    ) == result["persisted"]
    assert result["can_execute"] is False


def test_nfl_existing_snapshot_yields_explicit_exclusion_receipt(monkeypatch):
    candidate = _candidate()
    _install(monkeypatch, [candidate])
    db = _DB(existing={(candidate["canonical_event_id"], "NFL", candidate["player"], candidate["stat_type"])})
    result = subject.acquire_nfl_forward_evidence_batch(
        subject.NFLForwardEvidenceRequest(requested_date="2026-09-28", max_candidates=1),
        db=db, now=NOW, http_get=lambda *_a, **_k: object(),
    )
    assert result["already_captured"] == 1
    assert result["explicit_prewrite_exclusions"] == 1
    assert result["receipts"][0]["write_status"] == "ALREADY_CAPTURED"
    assert result["attempted"] == (
        result["snapshot_write_succeeded"] + result["snapshot_write_failed"]
        + result["explicit_prewrite_exclusions"]
    )


def test_stale_ready_snapshot_is_refreshed_not_permanently_skipped(monkeypatch):
    from datetime import timedelta
    candidate = _candidate()
    _install(monkeypatch, [candidate])
    existing = (candidate["canonical_event_id"], "NFL", candidate["player"], candidate["stat_type"])
    old = {
        "source_snapshot_id": "stale", "captured_at": (NOW - timedelta(minutes=31)).isoformat(),
        "hydration_status": "PASS", "blockers": [],
        "role_status": {"availability": {
            "designation": "AVAILABLE", "source": "OFFICIAL_ESPN_REPORT",
            "captured_at": NOW.isoformat(), "evidence_status": "RETRIEVED",
            "fresh": True, "report_complete": True, "athlete_id": "test-athlete",
            "event_id": candidate["canonical_event_id"], "team": "PHI",
        }},
    }
    db = _DB(existing={existing}, existing_row=old)
    result = subject.acquire_nfl_forward_evidence_batch(
        subject.NFLForwardEvidenceRequest(requested_date="2026-09-28", max_candidates=1),
        db=db, now=NOW, http_get=lambda *_a, **_k: object(),
    )
    assert result["already_captured"] == 0
    assert result["persisted"] == 1
    assert result["receipts"][0]["write_status"] == "SNAPSHOT_WRITE_SUCCEEDED"


def test_blocked_or_unverified_latest_snapshot_must_rehydrate(monkeypatch):
    candidate = _candidate()
    _install(monkeypatch, [candidate])
    existing = (candidate["canonical_event_id"], "NFL", candidate["player"], candidate["stat_type"])
    for override in (
        {"hydration_status": "FAILED", "blockers": []},
        {"hydration_status": "PASS", "blockers": ["PLAYER_AVAILABILITY_UNVERIFIED"]},
        {"hydration_status": "PASS", "blockers": [], "role_status": {"status": "ACTIVE"}},
    ):
        row = {
            "source_snapshot_id": "blocked", "captured_at": NOW.isoformat(),
            "hydration_status": "PASS", "blockers": [],
            "role_status": {"availability": {
                "designation": "AVAILABLE", "source": "OFFICIAL_ESPN_REPORT",
                "captured_at": NOW.isoformat(), "evidence_status": "RETRIEVED",
                "fresh": True, "report_complete": True, "athlete_id": "test-athlete",
                "event_id": candidate["canonical_event_id"], "team": "PHI",
            }},
            **override,
        }
        result = subject.acquire_nfl_forward_evidence_batch(
            subject.NFLForwardEvidenceRequest(requested_date="2026-09-28", max_candidates=1),
            db=_DB(existing={existing}, existing_row=row), now=NOW,
            http_get=lambda *_a, **_k: object(),
        )
        assert result["already_captured"] == 0, override
        assert result["persisted"] == 1, override
        assert result["can_execute"] is False


def test_priority_identity_first_and_one_official_discovery_across_pages(monkeypatch):
    candidates = [
        _candidate("Alpha QB", "PASSING_YARDS"),
        _candidate("Beta WR", "RECEIVING_YARDS", "WR"),
        _candidate("Target WR", "RECEIVING_YARDS", "WR"),
    ]
    _install(monkeypatch, candidates)
    discoveries = []

    def discover(*_args, **_kwargs):
        discoveries.append(True)
        if len(discoveries) != 1:
            raise AssertionError("NFL_DISCOVERY_REPEATED_BETWEEN_PAGES")
        return list(candidates)

    monkeypatch.setattr(subject, "_discover_candidates", discover)
    cache = {}
    targets = ((candidates[2]["canonical_event_id"], "Target WR", "RECEIVING_YARDS"),)
    db = _DB()
    page1 = subject.acquire_nfl_forward_evidence_batch(
        subject.NFLForwardEvidenceRequest(requested_date="2026-09-28", max_candidates=1),
        db=db, now=NOW, http_get=lambda *_a, **_kw: object(),
        priority_targets=targets, _discovery_cache=cache,
    )
    page2 = subject.acquire_nfl_forward_evidence_batch(
        subject.NFLForwardEvidenceRequest(requested_date="2026-09-28", candidate_offset=1, max_candidates=2),
        db=db, now=NOW, http_get=lambda *_a, **_kw: object(),
        priority_targets=targets, _discovery_cache=cache,
    )
    assert discoveries == [True]
    assert page1["receipts"][0]["player"] == "Target WR"
    all_receipts = page1["receipts"] + page2["receipts"]
    assert {(row["event_id"], row["player"], row["stat_type"]) for row in all_receipts} == {
        (row["canonical_event_id"], row["player"], row["stat_type"]) for row in candidates
    }
    assert len({row["source_snapshot_id"] for row in all_receipts}) == 3
    assert page1["persisted"] + page2["persisted"] == 3
    assert all(row["can_execute"] is False for row in all_receipts)


def test_embedded_injury_report_age_rechecked_even_if_snapshot_just_captured(monkeypatch):
    from datetime import timedelta
    candidate = _candidate()
    _install(monkeypatch, [candidate])
    existing = (candidate["canonical_event_id"], "NFL", candidate["player"], candidate["stat_type"])
    row = {
        "source_snapshot_id": "recent-with-stale-report",
        "captured_at": NOW.isoformat(), "hydration_status": "PASS", "blockers": [],
        "role_status": {"availability": {
            "designation": "AVAILABLE", "source": "ESPN_NFL_ATHLETE_INJURY_REPORT",
            "captured_at": (NOW - timedelta(hours=73)).isoformat(),
            "evidence_status": "RETRIEVED", "fresh": True, "report_complete": True,
            "athlete_id": "test-athlete", "event_id": candidate["canonical_event_id"],
            "team": "PHI",
        }},
    }
    result = subject.acquire_nfl_forward_evidence_batch(
        subject.NFLForwardEvidenceRequest(requested_date="2026-09-28", max_candidates=1),
        db=_DB(existing={existing}, existing_row=row),
        now=NOW, http_get=lambda *_a, **_kw: object(),
    )
    assert result["already_captured"] == 0
    assert result["persisted"] == 1
    assert result["can_execute"] is False
