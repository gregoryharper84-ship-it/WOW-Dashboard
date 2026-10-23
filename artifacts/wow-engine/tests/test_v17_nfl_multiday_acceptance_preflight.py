from dataclasses import replace
from datetime import datetime, timedelta, timezone
from v17.cross_sport_acceptance_factory import Fixture, SourceInventory
from v17.nfl_multiday_acceptance_preflight import plan_nfl_week_dates, reconcile_nfl_week_snapshots
UTC=timezone.utc
BASE=datetime(2026,10,15,tzinfo=UTC)
SHA="a"*64

def example():
    fixtures=tuple(Fixture("NFL",f"g{i}",BASE+timedelta(days=i%4,hours=i%5),"SCHEDULED") for i in range(13))
    inv=SourceInventory("official",SHA,BASE-timedelta(hours=1),fixtures)
    selection={"status":"COHORT_ELIGIBLE_FOR_TEST","selected_week":{"sport":"NFL","season_key":"2026","week_key":"7"},"cohort":{"status":"COHORT_ELIGIBLE_FOR_TEST","canonical_event_ids":[f"g{i}" for i in range(13)],"source_id":"official","source_payload_sha256":SHA}}
    return selection,inv

def responses(plan):
    return {b["requested_slate_date"]:{"requested_slate_date":b["requested_slate_date"],"run_id":"daily-"+b["requested_slate_date"],"can_execute":False,"global_terminal_authority":"V17_TERMINAL_REDUCER","automation_auth":"GITHUB_ACTIONS_OIDC","run_status":"COMPLETED","terminal":True,"requested_timezone":"America/Chicago","reconciliation":{"balanced":True},"cross_sport_discovery_audit":{"reconciliation":{"row_reconciliation":"PASS","events_accounted":len(b["expected_event_ids"]),"events_discovered":len(b["expected_event_ids"])}},"rows":[{"lane":"MONEYLINE","identity":{"sport":"NFL","official_event_id":e},"can_execute":False,"result":{}} for e in b["expected_event_ids"]]} for b in plan["dates"]}

def test_week_partition_all_events():
    s,inv=example();p=plan_nfl_week_dates(s,inv,independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))
    assert p["status"]=="DATE_PLAN_READY_NOT_ACTION_ACCEPTED"
    assert len(p["dates"])>=3
    assert sum(len(x["expected_event_ids"]) for x in p["dates"])==13
    assert not p["can_execute"] and not p["probability_publishable"]

def test_source_digest_mismatch():
    s,inv=example();assert plan_nfl_week_dates(s,inv,independently_authenticated_source_sha256="b"*64, trusted_now=BASE-timedelta(minutes=30))["status"]=="PREFLIGHT_HELD"

def test_thin_week_held():
    s,inv=example();s["cohort"]["canonical_event_ids"].pop()
    assert plan_nfl_week_dates(s,inv,independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))["status"]=="PREFLIGHT_HELD"

def test_duplicate_fixture_held():
    s,inv=example();inv=replace(inv,fixtures=inv.fixtures+(inv.fixtures[0],))
    assert plan_nfl_week_dates(s,inv,independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))["status"]=="PREFLIGHT_HELD"

def test_started_game_held():
    s,inv=example();inv=replace(inv,fixtures=(replace(inv.fixtures[0],status="FINAL"),)+inv.fixtures[1:])
    assert plan_nfl_week_dates(s,inv,independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))["status"]=="PREFLIGHT_HELD"

def test_complete_multi_date_reconciles_nonpublishing():
    s,inv=example();p=plan_nfl_week_dates(s,inv,independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))
    r=reconcile_nfl_week_snapshots(p,responses(p))
    assert r["status"]=="WEEK_SNAPSHOT_RECONCILED_NOT_ACTION_OR_MODEL_CERTIFIED" and r["nfl_rows"]==13
    assert r["can_execute"] is False and r["probability_publishable"] is False

def test_missing_date_held():
    s,inv=example();p=plan_nfl_week_dates(s,inv,independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))
    r=responses(p);del r[next(iter(r))]
    assert reconcile_nfl_week_snapshots(p,r)["status"]=="PREFLIGHT_HELD"

def test_duplicate_result_held():
    s,inv=example();p=plan_nfl_week_dates(s,inv,independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))
    r=responses(p);day=next(iter(r));r[day]["rows"].append(r[day]["rows"][0])
    assert reconcile_nfl_week_snapshots(p,r)["status"]=="PREFLIGHT_HELD"

def test_missing_auth_held():
    s,inv=example();p=plan_nfl_week_dates(s,inv,independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))
    r=responses(p);r[next(iter(r))]["automation_auth"]="UNVERIFIED"
    assert reconcile_nfl_week_snapshots(p,r)["status"]=="PREFLIGHT_HELD"

def test_model_budget_held():
    s,inv=example();p=plan_nfl_week_dates(s,inv,independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))
    r=responses(p);r[next(iter(r))]["rows"][0]["result"]={"blocker":"MODEL_INVOCATION_BUDGET_REACHED"}
    assert reconcile_nfl_week_snapshots(p,r)["status"]=="PREFLIGHT_HELD"

def test_actual_daily_runtime_terminal_contract_accepts():
    s, inv = example()
    plan = plan_nfl_week_dates(s, inv, independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))
    receipts = responses(plan)
    assert all(item["run_status"] == "COMPLETED" for item in receipts.values())
    assert reconcile_nfl_week_snapshots(plan, receipts)["status"] == "WEEK_SNAPSHOT_RECONCILED_NOT_ACTION_OR_MODEL_CERTIFIED"

def test_nonterminal_result_is_held():
    s, inv = example()
    plan = plan_nfl_week_dates(s, inv, independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))
    receipts = responses(plan)
    receipts[next(iter(receipts))]["terminal"] = False
    assert reconcile_nfl_week_snapshots(plan, receipts)["status"] == "PREFLIGHT_HELD"

def test_incomplete_acquisition_run_is_held():
    s, inv = example()
    plan = plan_nfl_week_dates(s, inv, independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))
    receipts = responses(plan)
    receipts[next(iter(receipts))]["run_status"] = "COMPLETED_WITH_ACQUISITION_BLOCKERS"
    assert reconcile_nfl_week_snapshots(plan, receipts)["status"] == "PREFLIGHT_HELD"

def test_current_runtime_event_id_fallback():
    s, inv = example()
    plan = plan_nfl_week_dates(s, inv, independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))
    receipts = responses(plan)
    day = next(iter(receipts))
    identity = receipts[day]["rows"][0]["identity"]
    identity["event_id"] = identity.pop("official_event_id")
    assert reconcile_nfl_week_snapshots(plan, receipts)["status"] == "WEEK_SNAPSHOT_RECONCILED_NOT_ACTION_OR_MODEL_CERTIFIED"

def test_wrong_timezone_is_held():
    s, inv = example()
    plan = plan_nfl_week_dates(s, inv, independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))
    receipts = responses(plan)
    receipts[next(iter(receipts))]["requested_timezone"] = "UTC"
    assert reconcile_nfl_week_snapshots(plan, receipts)["status"] == "PREFLIGHT_HELD"

def test_missing_daily_run_receipt_is_held():
    s, inv = example()
    plan = plan_nfl_week_dates(s, inv, independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))
    receipts = responses(plan)
    receipts[next(iter(receipts))]["run_id"] = None
    assert reconcile_nfl_week_snapshots(plan, receipts)["status"] == "PREFLIGHT_HELD"

def test_reused_daily_run_id_is_held():
    s, inv = example()
    plan = plan_nfl_week_dates(s, inv, independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))
    receipts = responses(plan)
    keys = list(receipts)
    receipts[keys[1]]["run_id"] = receipts[keys[0]]["run_id"]
    assert reconcile_nfl_week_snapshots(plan, receipts)["status"] == "PREFLIGHT_HELD"

def test_absent_discovery_counts_are_held():
    s, inv = example()
    plan = plan_nfl_week_dates(s, inv, independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))
    receipts = responses(plan)
    audit = receipts[next(iter(receipts))]["cross_sport_discovery_audit"]["reconciliation"]
    audit.pop("events_accounted")
    audit.pop("events_discovered")
    assert reconcile_nfl_week_snapshots(plan, receipts)["status"] == "PREFLIGHT_HELD"

def test_elapsed_kickoff_with_stale_pregame_label_held():
    s, inv = example()
    inv = replace(inv, fixtures=(replace(inv.fixtures[0], start_utc=BASE-timedelta(hours=1)),) + inv.fixtures[1:])
    r = plan_nfl_week_dates(s, inv, independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))
    assert r["status"] == "PREFLIGHT_HELD"
    assert "NFL_WEEK_KICKOFF_NOT_FUTURE" in r["blockers"]

def test_stale_inventory_held():
    s, inv = example()
    inv = replace(inv, fetched_at=BASE-timedelta(days=1))
    r = plan_nfl_week_dates(s, inv, independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))
    assert "NFL_WEEK_SOURCE_STALE_OR_FUTURE" in r["blockers"]

def test_future_dated_inventory_held():
    s, inv = example()
    inv = replace(inv, fetched_at=BASE)
    r = plan_nfl_week_dates(s, inv, independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))
    assert "NFL_WEEK_SOURCE_STALE_OR_FUTURE" in r["blockers"]

def test_naive_trusted_clock_held():
    s, inv = example()
    r = plan_nfl_week_dates(s, inv, independently_authenticated_source_sha256=SHA, trusted_now=BASE.replace(tzinfo=None))
    assert "NFL_WEEK_TRUSTED_CLOCK_REQUIRED" in r["blockers"]

def test_kickoff_exactly_now_not_future():
    s, inv = example()
    inv = replace(inv, fixtures=(replace(inv.fixtures[0], start_utc=BASE-timedelta(minutes=30)),) + inv.fixtures[1:])
    r = plan_nfl_week_dates(s, inv, independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))
    assert "NFL_WEEK_KICKOFF_NOT_FUTURE" in r["blockers"]


def test_forged_thin_ready_plan_cannot_pass_full_week_reconciliation():
    s, inv = example()
    plan = plan_nfl_week_dates(s, inv, independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))
    # Simulate a downstream caller bypassing the authenticated plan builder.
    thin = {**plan, "expected_event_ids": ["g0"], "dates": [{"requested_slate_date": plan["dates"][0]["requested_slate_date"], "expected_event_ids": ["g0"]}]}
    result = reconcile_nfl_week_snapshots(thin, responses(thin))
    assert result["status"] == "PREFLIGHT_HELD"
    assert "NFL_WEEK_PLAN_EVENT_COVERAGE_INVALID" in result["blockers"]
    assert result["probability_publishable"] is False


def test_truncated_ready_plan_date_bucket_fails_closed():
    s, inv = example()
    plan = plan_nfl_week_dates(s, inv, independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))
    partial = {**plan, "dates": plan["dates"][:-1]}
    result = reconcile_nfl_week_snapshots(partial, responses(partial))
    assert "NFL_WEEK_PLAN_EVENT_COVERAGE_INVALID" in result["blockers"]


def test_duplicate_or_malformed_date_bucket_fails_closed():
    s, inv = example()
    plan = plan_nfl_week_dates(s, inv, independently_authenticated_source_sha256=SHA, trusted_now=BASE-timedelta(minutes=30))
    duplicate = {**plan, "dates": plan["dates"] + [plan["dates"][0]]}
    assert "NFL_WEEK_PLAN_EVENT_COVERAGE_INVALID" in reconcile_nfl_week_snapshots(duplicate, responses(plan))["blockers"]
    malformed = {**plan, "dates": [{"requested_slate_date": "not-a-date", "expected_event_ids": plan["expected_event_ids"]}]}
    assert "NFL_WEEK_PLAN_EVENT_COVERAGE_INVALID" in reconcile_nfl_week_snapshots(malformed, {})["blockers"]


def test_malformed_ready_plan_never_raises_or_approves():
    assert reconcile_nfl_week_snapshots(None, {})["status"] == "PREFLIGHT_HELD"
    assert "NFL_WEEK_PLAN_EVENT_COVERAGE_INVALID" in reconcile_nfl_week_snapshots({"status": "DATE_PLAN_READY_NOT_ACTION_ACCEPTED"}, {})["blockers"]
