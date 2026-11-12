from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from v17.daily_snapshot_runtime import DailySnapshotRequest, _prop_manifest_rows, run_daily_snapshot

FUTURE_DT = datetime.now(timezone.utc) + timedelta(hours=12)
FUTURE = FUTURE_DT.isoformat()
SLATE_DATE = FUTURE_DT.astimezone(ZoneInfo("America/Chicago")).date().isoformat()


class Result:
    def __init__(self, data): self.data = data


class Query:
    def __init__(self, data): self.data = data
    def select(self, *_): return self
    def eq(self, *_): return self
    def gte(self, *_): return self
    def lte(self, *_): return self
    def order(self, *_, **__): return self
    def limit(self, *_): return self
    def upsert(self, payload, *_args, **_kwargs):
        self.data[:] = [dict(row) for row in payload]
        return self
    def range(self, start, end):
        self.data = self.data[int(start):int(end) + 1]
        return self
    def execute(self): return Result(self.data)


class DB:
    def __init__(self):
        self.acquisition_rows = []
        # Real row-detail readback must see rows written by a prior Query.
        # This test fake intentionally preserves the upsert across table() calls.
        self.row_detail_rows = []
        # Append-only exact-spread market ledger observations.
        self.market_rows = []
        # Governed per-event certified spread receipts.
        self.spread_receipts = []
    def table(self, name):
        if name == "wow_v17_daily_run_row_detail":
            return Query(self.row_detail_rows)
        if name == "wow_market_price_observations":
            return Query(self.market_rows)
        if name == "wow_v17_spread_card_receipts":
            return Query(self.spread_receipts)
        if name == "wow_prop_evidence_snapshots":
            return Query([{"source_snapshot_id":"snap-prop","event_id":"p1","event_start_time":FUTURE,"sport":"MLB","player":"P","stat_type":"strikeouts","line":4.5,"hydration_status":"PASS","blockers":[]}])
        if name == "wow_v17_daily_run_acquisition_detail":
            return Query(self.acquisition_rows)
        return Query([{"official_event_id":"1","official_date":SLATE_DATE,"event_start_time":FUTURE,"home_team":"Home","away_team":"Away","venue_name":"Park","home_probable_pitcher":"H","away_probable_pitcher":"A","snapshot_id":"snap-event","snapshot_timestamp":FUTURE,"feature_hydration_status":"PASS"}])


class Market:
    class ScorePropRequest:
        def __init__(self, **kwargs): self.kwargs = kwargs
    @staticmethod
    def score_prop(*_):
        raise HTTPException(status_code=422, detail={"code":"PROP_MODEL_NOT_PUBLISHABLE","probability_publishable":False,"can_execute":False})


class Event: pass


def governed_team_result():
    return {
        "code":"FINAL_APPROVED",
        "terminal_label":"FINAL_APPROVED",
        "probability_publishable":True,
        "rank_eligible":True,
        "can_execute":False,
        "global_terminal_authority":"V17_TERMINAL_REDUCER",
        "llp_probability_audit_result":"PASS_PROBABILITY_AUDIT",
        "event_mutex_status":"PASS",
        "calibrated_probability":0.62,
        "calibrated_lower_bound":0.58,
        "llp_governance":{
            "probability_publishable":True,
            "rank_eligible":True,
            "global_terminal_reducer":"V17_TERMINAL_REDUCER",
            "can_execute":False,
            "probability_audit_result":"PASS_PROBABILITY_AUDIT",
            "event_mutex_status":"PASS",
            "postmodel_gates_status":"PASS",
            "final_gates_status":"PASS",
            "terminal_label":"FINAL_APPROVED",
        },
    }


def test_daily_snapshot_returns_one_terminal_receipt_per_selected_row(monkeypatch):
    # The fixture is a canonical two-row test: an independently scheduled
    # cross-sport provider scan is not a third synthetic candidate.
    monkeypatch.setattr("v17.daily_snapshot_runtime.discovery_feed.enabled", lambda: False)
    monkeypatch.setattr("v17.daily_snapshot_runtime.score_team_event_request", lambda *_args, **_kwargs: governed_team_result())
    # Keep this row/terminal contract test independent of the process-global
    # production installer that can force a fresh provider acquisition when the
    # full governed suite is collected in one interpreter.
    monkeypatch.setattr(
        "v17.daily_snapshot_runtime.acquire_daily_prop_snapshots",
        lambda **_: {
            "status": "COMPLETED",
            "attempted": 0,
            "persisted": 0,
            "snapshot_write_succeeded": 0,
            "snapshot_write_failed": 0,
            "explicit_prewrite_exclusions": 0,
            "receipts": [],
            "blockers": [],
            "can_execute": False,
        },
    )
    result=run_daily_snapshot(DailySnapshotRequest(requested_slate_date=SLATE_DATE,requested_timezone="America/Chicago",response_mode="FULL"),db=DB(),market_api=Market(),event_api=Event())
    assert result["run_status"]=="COMPLETED"
    assert len(result["rows"])==2
    assert result["reconciliation"] == {
        "rows_in":2,"rows_completed":1,"rows_held":1,"rows_rejected":0,
        "rows_purged":0,"rows_invalid":0,"rows_unclassified":0,"balanced":True,
    }
    assert result["rows"][1]["result"]["official_publication_guard"]["status"]=="PASS"


def test_moneyline_normal_hold_is_not_completed(monkeypatch):
    monkeypatch.setattr("v17.daily_snapshot_runtime.score_team_event_request", lambda *_args, **_kwargs: {"code":"LLP_EVENT_GOVERNANCE_NOT_PROVEN","probability_publishable":False,"rank_eligible":False,"can_execute":False})
    result=run_daily_snapshot(DailySnapshotRequest(requested_slate_date=SLATE_DATE,requested_timezone="America/Chicago",lanes=["MONEYLINE"]),db=DB(),market_api=Market(),event_api=Event())
    assert result["rows"][0]["row_status"]=="HELD"


def test_moneyline_shadow_style_boolean_leak_is_held_and_depublished(monkeypatch):
    shadow = {"source_mode":"FORWARD_SHADOW","probability_publishable":True,"rank_eligible":True,"can_execute":False,"calibrated_probability":0.563781,"calibrated_lower_bound":0.563781}
    monkeypatch.setattr("v17.daily_snapshot_runtime.score_team_event_request", lambda *_args, **_kwargs: shadow)
    result=run_daily_snapshot(DailySnapshotRequest(requested_slate_date=SLATE_DATE,requested_timezone="America/Chicago",lanes=["MONEYLINE"],response_mode="FULL"),db=DB(),market_api=Market(),event_api=Event())
    row=result["rows"][0]
    assert row["row_status"]=="HELD"
    assert row["probability_publishable"] is False
    assert row["result"]["probability_publishable"] is False
    assert row["result"]["rank_eligible"] is False
    assert row["result"]["prepublication_claim"]=={"probability_publishable":True,"rank_eligible":True}
    assert row["result"]["calibrated_probability"]==0.563781
    assert any("TEAM_EVENT_RESEARCH_ARTIFACT_NOT_OFFICIAL" in blocker for blocker in row["result"]["official_publication_guard"]["blockers"])


def test_props_research_only_normal_return_is_held():
    class ResearchOnlyMarket(Market):
        @staticmethod
        def score_prop(*_): return {"probability_publishable":False,"governed_publishable":False,"research_only":True,"research_model_output":{"raw_specialist_probability":0.55}}
    result=run_daily_snapshot(DailySnapshotRequest(requested_slate_date=SLATE_DATE,requested_timezone="America/Chicago",lanes=["PROPS"]),db=DB(),market_api=ResearchOnlyMarket(),event_api=Event())
    assert result["rows"][0]["row_status"]=="HELD"


def test_reconciliation_fails_closed_on_unclassified_row():
    from v17.daily_snapshot_runtime import _reconcile
    result=_reconcile([{"row_status":"COMPLETED"},{"row_status":"NEW"}])
    assert result["rows_unclassified"]==1 and result["balanced"] is False


def test_reconciliation_includes_purged_and_invalid_terminal_buckets():
    from v17.daily_snapshot_runtime import _reconcile
    result=_reconcile([{"row_status":"PURGED"},{"row_status":"INVALID"}])
    assert result["rows_purged"] == 1
    assert result["rows_invalid"] == 1
    assert result["rows_unclassified"] == 0
    assert result["balanced"] is True


def test_props_publishable_without_rank_is_held():
    class NonRankMarket(Market):
        @staticmethod
        def score_prop(*_): return {"probability_publishable":True,"rank_eligible":False,"can_execute":False}
    result=run_daily_snapshot(DailySnapshotRequest(requested_slate_date=SLATE_DATE,requested_timezone="America/Chicago",lanes=["PROPS"]),db=DB(),market_api=NonRankMarket(),event_api=Event())
    assert result["rows"][0]["row_status"]=="HELD"


def test_daily_uses_server_owned_canonical_hydration_for_moneyline(monkeypatch):
    seen={}
    def score(*_args,**kwargs):
        seen.update(kwargs); return governed_team_result()
    monkeypatch.setattr("v17.daily_snapshot_runtime.score_team_event_request",score)
    result=run_daily_snapshot(DailySnapshotRequest(requested_slate_date=SLATE_DATE,requested_timezone="America/Chicago",lanes=["MONEYLINE"]),db=DB(),market_api=Market(),event_api=Event())
    assert seen["canonical_hydration_required"] is True
    assert result["rows"][0]["row_status"]=="COMPLETED"


def test_zero_row_query_failures_are_typed_as_data_unobtainable(monkeypatch):
    # A broken canonical database must be tested without separately acquired
    # cross-sport rows masking the MONEYLINE zero-row classification.
    monkeypatch.setattr("v17.daily_snapshot_runtime.discovery_feed.enabled", lambda: False)
    class BrokenQuery(Query):
        def execute(self): raise ConnectionError("boom")
    class BrokenDB:
        def table(self,_): return BrokenQuery([])
    result=run_daily_snapshot(DailySnapshotRequest(requested_slate_date=SLATE_DATE,requested_timezone="America/Chicago"),db=BrokenDB(),market_api=Market(),event_api=Event())
    assert result["lane_reconciliation"]["PROPS"]["zero_row_reason"]=="DISCOVERY_DATA_UNOBTAINABLE"
    assert result["lane_reconciliation"]["MONEYLINE"]["zero_row_reason"]=="DISCOVERY_DATA_UNOBTAINABLE"


def test_empty_prop_snapshot_invokes_server_acquisition_then_requeries(monkeypatch):
    class StatefulDB(DB):
        def __init__(self): self.props=[]
        def table(self,name):
            if name=="wow_prop_evidence_snapshots": return Query(self.props)
            return Query([])
    db=StatefulDB()
    calls=[]
    def acquire(**kwargs):
        calls.append(kwargs)
        db.props.append({"source_snapshot_id":"new-snap","event_id":"MLB:1","event_start_time":FUTURE,"sport":"MLB","player":"Pitcher","stat_type":"PITCHER_STRIKEOUTS","line":5.5,"hydration_status":"PASS","blockers":[]})
        return {
            "status":"COMPLETED","attempted":1,"hydrated":1,"persisted":1,"held":0,
            "snapshot_write_succeeded":1,"snapshot_write_failed":0,"explicit_prewrite_exclusions":0,
            "receipts":[{"source_snapshot_id":"new-snap","write_status":"SNAPSHOT_WRITE_SUCCEEDED"}],
            "blockers":[],"can_execute":False,
        }
    monkeypatch.setattr("v17.daily_snapshot_runtime.acquire_daily_prop_snapshots",acquire)
    result=run_daily_snapshot(DailySnapshotRequest(requested_slate_date=SLATE_DATE,requested_timezone="America/Chicago",lanes=["PROPS"]),db=db,market_api=Market(),event_api=Event())
    assert len(calls)==1
    assert result["lane_reconciliation"]["PROPS"]["discovered_count"]==1
    assert result["lane_reconciliation"]["PROPS"]["zero_row_reason"] is None
    handoff=result["lane_reconciliation"]["PROPS"]["handoff_reconciliation"]
    assert handoff["prewrite_balanced"] is True
    assert handoff["persisted_to_canonical_balanced"] is True
    assert handoff["canonical_to_scored_balanced"] is True
    assert "NO_CANONICAL_PREGAME_SNAPSHOTS" not in result["blockers"]


def test_persisted_rows_with_empty_canonical_readback_are_typed_ingestion_empty(monkeypatch):
    class EmptyDB(DB):
        def table(self,name): return Query([])
    receipts=[{"source_snapshot_id":f"snap-{i}","write_status":"SNAPSHOT_WRITE_SUCCEEDED"} for i in range(16)]
    monkeypatch.setattr("v17.daily_snapshot_runtime.acquire_daily_prop_snapshots",lambda **_: {
        "status":"COMPLETED","attempted":18,"hydrated":16,"persisted":16,"held":2,
        "snapshot_write_succeeded":16,"snapshot_write_failed":0,"explicit_prewrite_exclusions":2,
        "receipts":receipts,"blockers":[],"can_execute":False,
    })
    result=run_daily_snapshot(DailySnapshotRequest(requested_slate_date=SLATE_DATE,requested_timezone="America/Chicago",lanes=["PROPS"]),db=EmptyDB(),market_api=Market(),event_api=Event())
    assert "PROP_SNAPSHOT_INGESTION_EMPTY" in result["blockers"]
    assert "NO_CANONICAL_PREGAME_SNAPSHOTS" not in result["blockers"]
    assert result["lane_reconciliation"]["PROPS"]["zero_row_reason"]=="PROP_SNAPSHOT_INGESTION_EMPTY"
    handoff=result["lane_reconciliation"]["PROPS"]["handoff_reconciliation"]
    assert handoff["lane_discovered_raw"]==18
    assert handoff["persisted_candidates"]==16
    assert handoff["canonical_rows"]==0
    assert len(handoff["missing_persisted_snapshot_ids"])==16
    assert handoff["prewrite_balanced"] is True
    assert handoff["persisted_to_canonical_balanced"] is False
    assert result["can_execute"] is False


def test_all_snapshot_writes_failed_is_run_invalid(monkeypatch):
    class EmptyDB(DB):
        def table(self,name): return Query([])
    monkeypatch.setattr("v17.daily_snapshot_runtime.acquire_daily_prop_snapshots",lambda **_: {
        "status":"RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE","attempted":3,"hydrated":3,"persisted":0,"held":3,
        "snapshot_write_succeeded":0,"snapshot_write_failed":3,"explicit_prewrite_exclusions":0,
        "receipts":[{"source_snapshot_id":f"snap-{i}","write_status":"SNAPSHOT_WRITE_FAILED"} for i in range(3)],
        "blockers":["Pitcher:PROP_SNAPSHOT_WRITE_FAILED:ConnectionError"],"can_execute":False,
    })
    result=run_daily_snapshot(DailySnapshotRequest(requested_slate_date=SLATE_DATE,requested_timezone="America/Chicago",lanes=["PROPS"]),db=EmptyDB(),market_api=Market(),event_api=Event())
    assert result["run_status"]=="RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE"
    assert "RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE" in result["blockers"]
    assert result["lane_reconciliation"]["PROPS"]["zero_row_reason"]=="RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE"
    assert "NO_CANONICAL_PREGAME_SNAPSHOTS" not in result["blockers"]


def test_true_zero_upstream_keeps_no_canonical_pregame_snapshots(monkeypatch):
    class EmptyDB(DB):
        def table(self,name): return Query([])
    monkeypatch.setattr("v17.daily_snapshot_runtime.acquire_daily_prop_snapshots",lambda **_: {
        "status":"COMPLETED","attempted":0,"hydrated":0,"persisted":0,"held":0,
        "snapshot_write_succeeded":0,"snapshot_write_failed":0,"explicit_prewrite_exclusions":0,
        "receipts":[],"blockers":[],"can_execute":False,
    })
    result=run_daily_snapshot(DailySnapshotRequest(requested_slate_date=SLATE_DATE,requested_timezone="America/Chicago",lanes=["PROPS"]),db=EmptyDB(),market_api=Market(),event_api=Event())
    assert result["blockers"]==["NO_CANONICAL_PREGAME_SNAPSHOTS"]


def test_empty_prop_acquisition_failure_is_typed_not_model_unavailable(monkeypatch):
    class EmptyDB(DB):
        def table(self,name): return Query([])
    monkeypatch.setattr("v17.daily_snapshot_runtime.acquire_daily_prop_snapshots",lambda **_: {"status":"DATA_UNOBTAINABLE","attempted":0,"persisted":0,"blockers":["MLB_PROP_SCHEDULE_ACQUISITION_FAILED:TimeoutError"],"can_execute":False})
    result=run_daily_snapshot(DailySnapshotRequest(requested_slate_date=SLATE_DATE,requested_timezone="America/Chicago",lanes=["PROPS"]),db=EmptyDB(),market_api=Market(),event_api=Event())
    assert result["lane_reconciliation"]["PROPS"]["zero_row_reason"]=="DISCOVERY_DATA_UNOBTAINABLE"
    assert all("MODEL_UNAVAILABLE" not in blocker for blocker in result["blockers"])


def test_prop_manifest_pages_through_1000_rows_without_truncation():
    rows=[{"source_snapshot_id":f"snap-{i}","event_id":f"MLB:{i}","event_start_time":FUTURE,"sport":"MLB","player":f"P{i}","stat_type":"PITCHER_STRIKEOUTS","line":5.5,"hydration_status":"PASS","blockers":[]} for i in range(1000)]
    class PagedQuery:
        def __init__(self, data): self.data=data; self.start=0; self.end=len(data)-1
        def select(self,*_): return self
        def eq(self,*_): return self
        def order(self,*_,**__): return self
        def range(self,start,end): self.start=start; self.end=end; return self
        def execute(self): return Result(self.data[self.start:self.end+1])
    class PagedDB:
        def table(self,name): return PagedQuery(rows)
    manifest=_prop_manifest_rows(PagedDB(),SLATE_DATE,"America/Chicago",page_size=250)
    assert len(manifest)==1000
    assert manifest[0]["source_snapshot_id"]=="snap-0"
    assert manifest[-1]["source_snapshot_id"]=="snap-999"


class _FilteringPropQuery:
    """DB fake that actually applies hydration_status equality filters."""

    def __init__(self, data):
        self._all = list(data)
        self._filters = []
        self._start = 0
        self._end = len(data) - 1
        self.orders = []
        self.selected = None

    def select(self, columns, *_):
        self.selected = columns
        return self

    def eq(self, name, value):
        self._filters.append((name, value))
        return self

    def order(self, name, *_, **__):
        self.orders.append(name)
        return self

    def range(self, start, end):
        self._start = start
        self._end = end
        return self

    def execute(self):
        rows = [
            row for row in self._all
            if all(row.get(name) == value for name, value in self._filters)
        ]
        for name in reversed(self.orders):
            rows.sort(key=lambda row, key=name: str(row.get(key) or ""))
        return Result(rows[self._start:self._end + 1])


class _FilteringPropDB:
    def __init__(self, rows):
        self.rows = rows
        self.last_query = None

    def table(self, name):
        assert name == "wow_prop_evidence_snapshots"
        self.last_query = _FilteringPropQuery(self.rows)
        return self.last_query


def _manifest_row(source, captured, *, hydration="PASS", blockers=None, player="P"):
    return {
        "source_snapshot_id": source,
        "captured_at": captured,
        "event_id": "MLB:1",
        "event_start_time": FUTURE,
        "sport": "MLB",
        "player": player,
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 5.5,
        "hydration_status": hydration,
        "blockers": list(blockers or []),
    }


def test_newer_failed_observation_is_not_hidden_by_older_pass_prefilter():
    """DB PASS prefilter must not erase a newer FAILED observation before canonicalize."""
    db = _FilteringPropDB([
        _manifest_row("older-pass", ""+(FUTURE_DT - timedelta(hours=3)).isoformat()+""),
        _manifest_row(
            "newer-failed",
            ""+(FUTURE_DT - timedelta(hours=2)).isoformat()+"",
            hydration="FAILED",
            blockers=["PLAYER_AVAILABILITY_UNVERIFIED"],
        ),
    ])
    exclusions = []
    stats = {}
    manifest = _prop_manifest_rows(
        db, SLATE_DATE, "America/Chicago", exclusions=exclusions, stats=stats
    )
    assert manifest == []
    assert stats["excluded_by_reason"]["EVIDENCE_BLOCKED"] == 1
    assert exclusions[0]["source_snapshot_id"] == "newer-failed"
    assert exclusions[0]["blockers"] == ["PLAYER_AVAILABILITY_UNVERIFIED"]
    assert db.last_query is not None
    assert ("hydration_status", "PASS") not in db.last_query._filters


def test_source_order_does_not_allow_older_pass_to_override_newer_failed():
    """Source ID ordering is irrelevant to captured_at chronology."""
    older = _manifest_row("z-older-pass", ""+(FUTURE_DT - timedelta(hours=3)).isoformat()+"")
    newer = _manifest_row("a-newer-failed", ""+(FUTURE_DT - timedelta(hours=2)).isoformat()+"", hydration="FAILED")
    for rows in ((older, newer), (newer, older)):
        excluded = []
        manifest = _prop_manifest_rows(
            _FilteringPropDB(rows), SLATE_DATE, "America/Chicago", exclusions=excluded
        )
        assert manifest == []
        assert excluded[0]["source_snapshot_id"] == "a-newer-failed"


def test_newer_verified_pass_can_recover_from_older_failed_observation():
    """A historical failure alone never permanently blocks newer clear evidence."""
    older = _manifest_row("z-older-failed", ""+(FUTURE_DT - timedelta(hours=3)).isoformat()+"", hydration="FAILED")
    newer = _manifest_row("a-newer-pass", ""+(FUTURE_DT - timedelta(hours=2)).isoformat()+"")
    for rows in ((older, newer), (newer, older)):
        manifest = _prop_manifest_rows(
            _FilteringPropDB(rows), SLATE_DATE, "America/Chicago"
        )
        assert len(manifest) == 1
        assert manifest[0]["source_snapshot_id"] == "a-newer-pass"


def test_unknown_time_blocked_observation_cannot_be_overridden_by_clean_pass():
    """Invalid or infinite timestamp means chronology is not authoritative."""
    blocked = _manifest_row("unknown-blocked", "infinity", hydration="FAILED")
    clean = _manifest_row(
        "finite-pass", (FUTURE_DT - timedelta(hours=1)).isoformat()
    )
    for rows in ((blocked, clean), (clean, blocked)):
        exclusions = []
        manifest = _prop_manifest_rows(
            _FilteringPropDB(rows), SLATE_DATE, "America/Chicago",
            exclusions=exclusions,
        )
        assert manifest == []
        assert exclusions[0]["source_snapshot_id"] == "unknown-blocked"
        assert exclusions[0]["reason"] == "EVIDENCE_BLOCKED"


def test_same_time_conflicting_observations_fail_closed_to_blocked():
    db = _FilteringPropDB([
        _manifest_row("pass-a", ""+(FUTURE_DT - timedelta(hours=2)).isoformat()+""),
        _manifest_row(
            "failed-b",
            ""+(FUTURE_DT - timedelta(hours=2)).isoformat()+"",
            hydration="FAILED",
            blockers=["PLAYER_RULED_OUT"],
        ),
    ])
    exclusions = []
    manifest = _prop_manifest_rows(
        db, SLATE_DATE, "America/Chicago", exclusions=exclusions
    )
    assert manifest == []
    assert exclusions[0]["source_snapshot_id"] == "failed-b"
    assert exclusions[0]["blockers"] == ["PLAYER_RULED_OUT"]


def test_prop_manifest_pagination_remains_deterministic_with_mixed_hydration():
    rows = []
    for index in range(40):
        hydration = "FAILED" if index % 5 == 0 else "PASS"
        blockers = ["PLAYER_AVAILABILITY_UNVERIFIED"] if hydration == "FAILED" else []
        rows.append(
            {
                "source_snapshot_id": f"snap-{index:03d}",
                "captured_at": (FUTURE_DT - timedelta(minutes=5) + timedelta(seconds=index)).isoformat(),
                "event_id": f"MLB:{index}",
                "event_start_time": FUTURE,
                "sport": "MLB",
                "player": f"P{index}",
                "stat_type": "PITCHER_STRIKEOUTS",
                "line": 5.5,
                "hydration_status": hydration,
                "blockers": blockers,
            }
        )
    db = _FilteringPropDB(list(reversed(rows)))
    exclusions = []
    stats = {}
    manifest = _prop_manifest_rows(
        db,
        SLATE_DATE,
        "America/Chicago",
        page_size=7,
        exclusions=exclusions,
        stats=stats,
    )
    assert stats["read_complete"] is True
    assert stats["raw_rows_read"] == 40
    assert len(manifest) == 32
    assert all(row["hydration_status"] == "PASS" for row in manifest)
    assert stats["excluded_by_reason"]["EVIDENCE_BLOCKED"] == 8
    assert db.last_query.orders == ["event_start_time", "source_snapshot_id"]


def test_stale_pass_cannot_clear_newer_failed_without_blockers_list():
    """Non-PASS hydration alone is enough to block even when blockers is empty."""
    db = _FilteringPropDB([
        _manifest_row("older-pass", ""+(FUTURE_DT - timedelta(hours=3)).isoformat()+""),
        _manifest_row("newer-failed", ""+(FUTURE_DT - timedelta(hours=2)).isoformat()+"", hydration="FAILED"),
    ])
    exclusions = []
    manifest = _prop_manifest_rows(
        db, SLATE_DATE, "America/Chicago", exclusions=exclusions
    )
    assert manifest == []
    assert exclusions[0]["source_snapshot_id"] == "newer-failed"
    assert exclusions[0]["reason"] == "EVIDENCE_BLOCKED"


def test_pass_row_with_empty_blockers_is_held_when_role_status_rules_player_out():
    """Daily must re-evaluate role_status on read; writer-trust on blockers alone is unsafe."""
    row = _manifest_row("ruled-out-pass", ""+(FUTURE_DT - timedelta(hours=2)).isoformat()+"")
    row["role_status"] = {
        "status": "ACTIVE_CURRENT_ESPN_ROSTER",
        "availability": {
            "status": "OUT",
            "designation": "OUT",
            "source": "TEST_OFFICIAL_INJURY_REPORT",
            "captured_at": ""+(FUTURE_DT - timedelta(hours=2)).isoformat()+"",
            "evidence_status": "RETRIEVED",
            "fresh": True,
            "athlete_id": "1",
            "event_id": "MLB:1",
            "team": "TEST",
        },
    }
    db = _FilteringPropDB([row])
    exclusions = []
    manifest = _prop_manifest_rows(
        db, SLATE_DATE, "America/Chicago", exclusions=exclusions
    )
    assert manifest == []
    assert exclusions[0]["source_snapshot_id"] == "ruled-out-pass"
    assert exclusions[0]["reason"] == "EVIDENCE_BLOCKED"
    assert exclusions[0]["blockers"] == ["PLAYER_RULED_OUT"]
    assert db.last_query is not None
    assert "role_status" in str(db.last_query.selected)


# ---------------------------------------------------------------------------
# Cross-sport MONEYLINE discovery
#
# The certified MLB canonical-snapshot lane above is unchanged. These cover the
# additive lane that stops the board from *being* MLB whenever MLB is the only
# healthy bridge.
# ---------------------------------------------------------------------------

def _cross_sport_feed(rows_by_sport):
    def fetch(sport, sport_key=None, *, target=None, **_kwargs):
        return rows_by_sport.get(sport, [])

    return fetch


def _cross_sport_event(event_id, *, home="Alpha", away="Beta"):
    return {
        "id": event_id,
        "home_team": home,
        "away_team": away,
        "commence_time": FUTURE,
        "status": "SCHEDULED",
    }


def test_moneyline_lane_retains_unsupported_sports_instead_of_shrinking_to_mlb(monkeypatch):
    """The real lane runs; only the upstream feed is stubbed."""
    from v17 import cross_sport_discovery_feed as feed
    from v17 import cross_sport_winner_discovery as discovery
    from v17 import daily_snapshot_runtime as runtime
    from v17.team_event_bridge_runtime import TEAM_EVENT_BRIDGES

    monkeypatch.setattr(runtime, "score_team_event_request", lambda *a, **k: governed_team_result())

    slate = _cross_sport_feed(
        {
            "NFL": [_cross_sport_event("nfl-1")],
            "NHL": [_cross_sport_event("nhl-1")],
            "SOCCER": [_cross_sport_event("soccer-1")],
        }
    )
    monkeypatch.setattr(feed, "odds_proxy_feed", lambda **kwargs: slate)
    monkeypatch.setattr(feed, "rundown_board_feed", lambda **kwargs: slate)
    # The full-suite runner can otherwise inherit a short provider time budget;
    # this deterministic in-memory feed must walk every configured target.
    monkeypatch.setattr(discovery, "discovery_budget_seconds", lambda: 600.0)

    # Any earlier test that imports an API module boots the app and installs the
    # production discovery overlays, which canonicalize NHL aliases against the
    # live first-party NHL schedule. That schedule is an upstream source too, so
    # stub it deterministically; otherwise this test depends on suite order.
    from v17 import nhl_event_identity

    def _nhl_schedule_match(*, event_start_time, home_team, away_team, **_kwargs):
        return {
            "event_id": "nhl-1",
            "event_start_time": event_start_time,
            "home_team": home_team,
            "away_team": away_team,
            "identity_provider": "NHL_PUBLIC_WEB_API",
            "identity_verified_at": FUTURE,
            "market_features_used": False,
        }

    monkeypatch.setattr(nhl_event_identity, "resolve_nhl_current_event_identity", _nhl_schedule_match)


    # No non-MLB bridge is registered, so every discovered non-MLB row must be
    # retained as MODEL_UNAVAILABLE rather than disappear from the board.
    for sport in ("NFL", "NHL", "SOCCER"):
        monkeypatch.delitem(TEAM_EVENT_BRIDGES, sport, raising=False)

    payload = run_daily_snapshot(
        DailySnapshotRequest(
            requested_slate_date=SLATE_DATE,
            requested_timezone="America/Chicago",
            lanes=["MONEYLINE"],
            response_mode="FULL",
        ),
        db=DB(),
        market_api=Market,
        event_api=Event,
    )

    moneyline_rows = [row for row in payload["rows"] if row["lane"] == "MONEYLINE"]
    sports = {row["identity"].get("sport") for row in moneyline_rows}
    assert {"NFL", "NHL", "SOCCER"}.issubset(sports), "unsupported sports stay on the board"

    audit = payload["cross_sport_discovery_audit"]
    reconciliation = audit["reconciliation"]
    assert reconciliation["row_reconciliation"] == "PASS"
    assert reconciliation["events_accounted"] == reconciliation["events_discovered"]
    assert reconciliation["retained_unsupported_rows"] >= 3, (
        "Missing-model candidates were silently altered: "
        + repr([(r["identity"].get("sport"), r["result"].get("bucket"), r["result"].get("model_status")) for r in moneyline_rows])
    )
    # Every supported sport was *queried*, not just the ones with a model.
    assert audit["discovery"]["sports_queried"] == list(discovery.SUPPORTED_DISCOVERY_SPORTS)
    assert audit["discovery"]["discovery_independent_of_model_registry"] is True

    for row in moneyline_rows:
        if row["identity"].get("sport") in {"NFL", "NHL", "SOCCER"}:
            assert row["result"]["model_status"] == "MODEL_UNAVAILABLE"
            assert row["result"]["rank_eligible"] is False
            assert row["result"]["market_probability_substitution_allowed"] is False
            assert row["probability_publishable"] is False
        assert row["can_execute"] is False
    assert payload["can_execute"] is False


def test_moneyline_lane_degrades_cleanly_when_cross_sport_discovery_is_disabled(monkeypatch):
    from v17 import cross_sport_discovery_feed as feed

    monkeypatch.setattr(feed, "enabled", lambda: False)
    monkeypatch.setattr(
        "v17.daily_snapshot_runtime.score_team_event_request", lambda *a, **k: governed_team_result()
    )
    payload = run_daily_snapshot(
        DailySnapshotRequest(requested_slate_date=SLATE_DATE, requested_timezone="America/Chicago", lanes=["MONEYLINE"]),
        db=DB(),
        market_api=Market,
        event_api=Event,
    )
    assert payload["cross_sport_discovery_audit"] is None
    assert [row for row in payload["rows"] if row["lane"] == "MONEYLINE"]
    assert payload["reconciliation"]["balanced"] is True
    assert payload["can_execute"] is False


def test_spread_lane_binds_the_ledger_line_and_holds_without_a_model():
    from v17.daily_snapshot_runtime import _spread_lane_rows

    quote_at = (FUTURE_DT - timedelta(hours=1)).isoformat()
    common = {
        "provider_event_id": "rd-nfl-7001",
        "sport_key": "americanfootball_nfl",
        "event_start_utc": FUTURE,
        "market_id": "2",
        "market_name": "spreads",
        "sportsbook": "Pinnacle",
        "price_updated_at": quote_at,
        "is_live": False,
        "is_main_line": True,
        "is_available": True,
    }
    db = DB()
    db.market_rows = [
        {**common, "participant_type": "home", "participant_name": "Dallas Cowboys", "line_value": -6.5},
        {**common, "participant_type": "away", "participant_name": "Atlanta Falcons", "line_value": 6.5},
    ]
    moneyline = [{
        "lane": "MONEYLINE",
        "identity": {
            "official_event_id": "2026_03_ATL_DAL",
            "sport": "NFL",
            "home_team": "DAL",
            "away_team": "ATL",
            "event_start_time": FUTURE,
        },
        "result": {"sport": "NFL"},
    }]
    rows = _spread_lane_rows(db, moneyline_rows=moneyline, requested_limit=6)
    assert len(rows) == 1
    row = rows[0]
    payload = row["result"]
    assert row["lane"] == "SPREAD"
    assert row["row_status"] == "HELD"
    assert row["probability_publishable"] is False
    assert row["rank_eligible"] is False
    assert row["can_execute"] is False
    assert payload["exact_signed_spread"] == -6.5
    assert payload["exact_line_side"] == "HOME"
    assert payload["line_source"] == "APPEND_ONLY_MARKET_LEDGER"
    assert payload["sportsbook"] == "Pinnacle"
    assert payload["settlement_basis"] == "FULL_GAME_INCLUDING_OVERTIME"
    # The line is real; the selection is not, and no probability is invented.
    # The governed receipt relation exists but holds nothing for this event.
    assert payload["reason_codes"] == ["SPREAD_RECEIPT_NOT_PUBLISHED"]
    for absent in ("p_cover", "p_push", "p_not_cover", "calibrated_probability_lower_bound"):
        assert absent not in payload
    assert payload["market_evidence_observed_rows"] == 2


def test_spread_lane_persists_rows_for_the_authenticated_readback(monkeypatch):
    monkeypatch.setattr("v17.daily_snapshot_runtime.discovery_feed.enabled", lambda: False)
    monkeypatch.setattr(
        "v17.daily_snapshot_runtime.score_team_event_request",
        lambda *_args, **_kwargs: governed_team_result(),
    )
    db = DB()
    request = DailySnapshotRequest(
        requested_slate_date=SLATE_DATE,
        requested_timezone="America/Chicago",
        lanes=["MONEYLINE", "SPREAD"],
        response_mode="FULL",
    )
    response = run_daily_snapshot(request, db=db, market_api=Market, event_api=Event())
    spread_rows = [row for row in response["rows"] if row["lane"] == "SPREAD"]
    assert spread_rows, response["lane_reconciliation"]
    assert all(row["row_status"] == "HELD" for row in spread_rows)
    assert all(row["result"]["probability_publishable"] is False for row in spread_rows)
    assert all(row["result"]["can_execute"] is False for row in spread_rows)
    # Every spread row carries an explicit reason rather than being omitted.
    assert all(row["result"]["reason_codes"] for row in spread_rows)
    assert response["lane_reconciliation"]["SPREAD"]["scored_count"] == len(spread_rows)
    persisted = [row for row in db.row_detail_rows if row["lane"] == "SPREAD"]
    assert len(persisted) == len(spread_rows)
    assert response["can_execute"] is False


def _nfl_spread_lane_fixture(db):
    """Ledger-bound NFL event identity for the spread lane."""
    quote_at = (FUTURE_DT - timedelta(hours=2)).isoformat()
    common = {
        "provider_event_id": "rd-nfl-7001",
        "sport_key": "americanfootball_nfl",
        "event_start_utc": FUTURE,
        "market_id": "2",
        "market_name": "spreads",
        "sportsbook": "Pinnacle",
        "price_updated_at": quote_at,
        "is_live": False,
        "is_main_line": True,
        "is_available": True,
    }
    db.market_rows = [
        {**common, "participant_type": "home", "participant_name": "Dallas Cowboys", "line_value": -6.5},
        {**common, "participant_type": "away", "participant_name": "Atlanta Falcons", "line_value": 6.5},
    ]
    return [{
        "lane": "MONEYLINE",
        "identity": {
            "official_event_id": "2026_03_ATL_DAL",
            "sport": "NFL",
            "home_team": "DAL",
            "away_team": "ATL",
            "event_start_time": FUTURE,
        },
        "result": {"sport": "NFL"},
    }]


def certified_spread_receipt(**overrides):
    receipt = {
        "sport": "NFL",
        "official_event_id": "2026_03_ATL_DAL",
        "exact_line_side": "HOME",
        "exact_signed_spread": -6.5,
        "settlement_basis": "FULL_GAME_INCLUDING_OVERTIME",
        "controlling_spread_specialist": "NFL_SPREAD_MARGIN_DISTRIBUTION_CHALLENGER_V1",
        "model_artifact_version": "NFL_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
        "model_family": "NFL_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
        "p_cover": 0.54, "p_push": 0.02, "p_not_cover": 0.44,
        "lower_bound_required": True,
        "calibrated_lower_bound": 0.47,
        "calibrated_upper_bound": 0.61,
        "probability_publishable": True,
        "rank_eligible": True,
        "model_timestamp": (FUTURE_DT - timedelta(minutes=30)).isoformat(),
        "event_state": "SCHEDULED",
        "score_snapshot_status": "PASS",
        "can_execute": False,
        "source_receipt_id": "receipt-spread-1",
        "model_class": "CERTIFIED_FITTED",
        "model_version": "NFL_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
        "calibration_version": "cal-v1",
        "source_snapshot_timestamp": (FUTURE_DT - timedelta(minutes=40)).isoformat(),
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "controlling_engine_identity": "LLP_TEAM_BETTING_ENGINE",
        # The governed relation only stores admitted receipts; a row without it
        # cannot exist, so the fixture mirrors the stored shape.
        "admission_status": "PASS",
    }
    receipt.update(overrides)
    return receipt


def test_governed_receipt_completes_the_spread_row():
    from v17.daily_snapshot_runtime import _spread_lane_rows

    db = DB()
    moneyline = _nfl_spread_lane_fixture(db)
    rows = _spread_lane_rows(
        db,
        moneyline_rows=moneyline,
        requested_limit=6,
        receipt_provider=lambda _db, **_kwargs: (certified_spread_receipt(), None),
    )
    assert len(rows) == 1
    row = rows[0]
    payload = row["result"]
    assert row["row_status"] == "COMPLETED"
    assert row["probability_publishable"] is True
    assert row["rank_eligible"] is True
    assert row["can_execute"] is False
    assert payload["spread_admission"]["status"] == "PASS"
    assert payload["p_cover"] == 0.54
    assert payload["p_push"] == 0.02
    assert payload["p_not_cover"] == 0.44
    assert payload["calibrated_probability_lower_bound"] == 0.47
    assert payload["selection_status"] == "QUALIFIED"
    assert payload["reason_codes"] == []
    assert payload["exact_signed_spread"] == -6.5
    assert payload["line_source"] == "APPEND_ONLY_MARKET_LEDGER"


def test_receipt_at_a_different_line_holds_the_row():
    from v17.daily_snapshot_runtime import _spread_lane_rows

    db = DB()
    moneyline = _nfl_spread_lane_fixture(db)
    rows = _spread_lane_rows(
        db,
        moneyline_rows=moneyline,
        requested_limit=6,
        receipt_provider=lambda _db, **_kwargs: (
            certified_spread_receipt(exact_signed_spread=-3.5), None
        ),
    )
    payload = rows[0]["result"]
    assert rows[0]["row_status"] == "HELD"
    assert rows[0]["probability_publishable"] is False
    assert "SPREAD_ADMISSION:EXACT_LINE_MISMATCH" in payload["reason_codes"]
    for absent in ("p_cover", "p_push", "p_not_cover"):
        assert absent not in payload


def test_receipt_for_a_started_event_holds_the_row():
    from v17.daily_snapshot_runtime import _spread_lane_rows

    db = DB()
    moneyline = _nfl_spread_lane_fixture(db)
    rows = _spread_lane_rows(
        db,
        moneyline_rows=moneyline,
        requested_limit=6,
        receipt_provider=lambda _db, **_kwargs: (
            certified_spread_receipt(event_state="IN_PROGRESS"), None
        ),
    )
    payload = rows[0]["result"]
    assert rows[0]["row_status"] == "HELD"
    assert "SPREAD_ADMISSION:EVENT_ALREADY_STARTED_OR_FINAL" in payload["reason_codes"]
    assert "p_cover" not in payload


def test_unavailable_receipt_registry_holds_with_a_typed_reason():
    from v17.daily_snapshot_runtime import _spread_lane_rows

    db = DB()
    moneyline = _nfl_spread_lane_fixture(db)

    def unavailable(_db, **_kwargs):
        raise RuntimeError("relation does not exist")

    rows = _spread_lane_rows(
        db, moneyline_rows=moneyline, requested_limit=6, receipt_provider=unavailable
    )
    payload = rows[0]["result"]
    assert rows[0]["row_status"] == "HELD"
    assert payload["reason_codes"][0].startswith("SPREAD_RECEIPT_REGISTRY_UNAVAILABLE")
    assert "p_cover" not in payload


def test_default_registry_read_completes_a_row_from_the_governed_table():
    from v17.daily_snapshot_runtime import _spread_lane_rows

    db = DB()
    moneyline = _nfl_spread_lane_fixture(db)
    db.spread_receipts = [certified_spread_receipt()]
    rows = _spread_lane_rows(db, moneyline_rows=moneyline, requested_limit=6)
    assert rows[0]["row_status"] == "COMPLETED"
    assert rows[0]["result"]["p_cover"] == 0.54


def test_empty_receipt_registry_keeps_the_row_held_not_published():
    from v17.daily_snapshot_runtime import _spread_lane_rows

    db = DB()
    moneyline = _nfl_spread_lane_fixture(db)
    db.spread_receipts = []
    rows = _spread_lane_rows(db, moneyline_rows=moneyline, requested_limit=6)
    payload = rows[0]["result"]
    assert rows[0]["row_status"] == "HELD"
    assert payload["reason_codes"] == ["SPREAD_RECEIPT_NOT_PUBLISHED"]
    assert rows[0]["probability_publishable"] is False
