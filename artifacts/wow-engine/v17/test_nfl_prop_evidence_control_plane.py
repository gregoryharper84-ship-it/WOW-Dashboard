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
    def upsert(self, row, on_conflict=None):
        self.pending = dict(row); self.db.upserts.append((self.pending, on_conflict)); return self
    def execute(self):
        if self.pending is not None: return _Result([self.pending])
        identity = (
            self.filters.get("event_id"), self.filters.get("sport"),
            self.filters.get("player"), self.filters.get("stat_type"),
        )
        return _Result([{"source_snapshot_id":"existing"}] if identity in self.db.existing else [])


class _DB:
    def __init__(self, existing=None): self.existing=set(existing or []); self.upserts=[]
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
