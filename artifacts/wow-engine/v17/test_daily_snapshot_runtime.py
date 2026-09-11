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
    def order(self, *_, **__): return self
    def limit(self, *_): return self
    def upsert(self, *_args, **_kwargs): return self
    def execute(self): return Result(self.data)


class DB:
    def table(self, name):
        if name == "wow_prop_evidence_snapshots":
            return Query([{"source_snapshot_id":"snap-prop","event_id":"p1","event_start_time":FUTURE,"sport":"MLB","player":"P","stat_type":"strikeouts","line":4.5,"hydration_status":"PASS","blockers":[]}])
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
    monkeypatch.setattr("v17.daily_snapshot_runtime.score_team_event_request", lambda *_args, **_kwargs: governed_team_result())
    result=run_daily_snapshot(DailySnapshotRequest(requested_slate_date=SLATE_DATE,requested_timezone="America/Chicago"),db=DB(),market_api=Market(),event_api=Event())
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
    result=run_daily_snapshot(DailySnapshotRequest(requested_slate_date=SLATE_DATE,requested_timezone="America/Chicago",lanes=["MONEYLINE"]),db=DB(),market_api=Market(),event_api=Event())
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


def test_zero_row_query_failures_are_typed_as_data_unobtainable():
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
