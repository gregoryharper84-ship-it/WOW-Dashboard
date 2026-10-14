"""Fail-closed nonpublishing multi-date NFL acceptance preflight (#1689).
Trusted caller must independently authenticate inventory digest and selected cohort.
Does not invoke Actions, certify or publish scores, or modify release controls.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Mapping, Any
from v17.cross_sport_acceptance_factory import SourceInventory

TERMINAL = "V17_TERMINAL_REDUCER"

def hold(reason: str) -> dict[str, Any]:
    return {"status":"PREFLIGHT_HELD","blockers":[reason],"can_execute":False,"probability_publishable":False,"global_terminal_authority":TERMINAL}

def plan_nfl_week_dates(selection: Mapping[str,Any], inventory: SourceInventory, *, independently_authenticated_source_sha256: str, trusted_now: datetime, minimum_events: int=13) -> dict[str,Any]:
    # The authorized server caller must provide a trusted current UTC/aware clock.
    # No implicit datetime.now() and no source-provided 'now' are accepted here.
    if not isinstance(trusted_now, datetime) or trusted_now.tzinfo is None or trusted_now.utcoffset() is None:
        return hold("NFL_WEEK_TRUSTED_CLOCK_REQUIRED")
    if not isinstance(inventory.fetched_at, datetime) or inventory.fetched_at.tzinfo is None or inventory.fetched_at.utcoffset() is None:
        return hold("NFL_WEEK_SOURCE_TIME_UNVERIFIED")
    if not timedelta(0) <= trusted_now - inventory.fetched_at <= timedelta(hours=6):
        return hold("NFL_WEEK_SOURCE_STALE_OR_FUTURE")
    if type(minimum_events) is not int or minimum_events < 13:
        return hold("NFL_WEEK_THRESHOLD_INVALID")
    if not isinstance(selection, Mapping) or selection.get("status") != "COHORT_ELIGIBLE_FOR_TEST":
        return hold("NFL_WEEK_NOT_ELIGIBLE")
    cohort=selection.get("cohort")
    week=selection.get("selected_week")
    if not isinstance(cohort,Mapping) or not isinstance(week,Mapping) or week.get("sport")!="NFL" or cohort.get("status")!="COHORT_ELIGIBLE_FOR_TEST":
        return hold("NFL_WEEK_IDENTITY_UNVERIFIED")
    if not independently_authenticated_source_sha256 or inventory.payload_sha256!=independently_authenticated_source_sha256 or cohort.get("source_payload_sha256")!=independently_authenticated_source_sha256 or not inventory.source_id or inventory.source_id!=cohort.get("source_id"):
        return hold("NFL_WEEK_SOURCE_UNVERIFIED")
    ids=cohort.get("canonical_event_ids")
    if not isinstance(ids,list) or len(ids)<minimum_events or any(not isinstance(i,str) or not i.strip() for i in ids) or len(set(ids))!=len(ids):
        return hold("NFL_WEEK_EVENT_IDS_INVALID")
    by_id=defaultdict(list)
    for f in inventory.fixtures:
        if f.official_event_id in ids:
            by_id[f.official_event_id].append(f)
    if set(by_id)!=set(ids) or any(len(v)!=1 for v in by_id.values()):
        return hold("NFL_WEEK_FIXTURE_MISMATCH")
    dates=defaultdict(list)
    for eid in ids:
        f=by_id[eid][0]
        if f.sport!="NFL" or f.status not in ("SCHEDULED","PRE_GAME") or not isinstance(f.start_utc,datetime) or f.start_utc.tzinfo is None or f.start_utc.utcoffset() is None:
            return hold("NFL_WEEK_FIXTURE_NOT_VERIFIED_PREGAME")
        if f.start_utc <= trusted_now:
            return hold("NFL_WEEK_KICKOFF_NOT_FUTURE")
        dates[f.start_utc.astimezone(ZoneInfo("America/Chicago")).date().isoformat()].append(eid)
    if len(dates)>9 or any(len(v)>32 for v in dates.values()):
        return hold("NFL_WEEK_BATCH_LIMIT")
    return {"status":"DATE_PLAN_READY_NOT_ACTION_ACCEPTED","dates":[{"requested_slate_date":d,"expected_event_ids":sorted(v)} for d,v in sorted(dates.items())],"expected_event_ids":sorted(ids),"source_payload_sha256":inventory.payload_sha256,"can_execute":False,"probability_publishable":False,"global_terminal_authority":TERMINAL}

def reconcile_nfl_week_snapshots(plan: Mapping[str,Any], results: Mapping[str,Mapping[str,Any]]) -> dict[str,Any]:
    if not isinstance(plan, Mapping) or plan.get("status")!="DATE_PLAN_READY_NOT_ACTION_ACCEPTED":
        return hold("NFL_WEEK_PLAN_NOT_READY")
    # A callback may invoke reconciliation independently of the authenticated
    # preflight. Never let a forged or truncated DATE_PLAN_READY envelope
    # manufacture a 13+ full-week acceptance from one event or one date.
    batches=plan.get("dates")
    expected=plan.get("expected_event_ids")
    if (not isinstance(batches,list) or not 1<=len(batches)<=9
            or not isinstance(expected,list) or len(expected)<13
            or any(not isinstance(e,str) or not e.strip() for e in expected)
            or len(expected)!=len(set(expected))):
        return hold("NFL_WEEK_PLAN_EVENT_COVERAGE_INVALID")
    dates=[]
    allocated=[]
    for batch in batches:
        if not isinstance(batch,Mapping):
            return hold("NFL_WEEK_PLAN_EVENT_COVERAGE_INVALID")
        day=batch.get("requested_slate_date")
        ids=batch.get("expected_event_ids")
        if (not isinstance(day,str) or len(day)!=10
                or not isinstance(ids,list) or not 1<=len(ids)<=32
                or any(not isinstance(e,str) or not e.strip() for e in ids)
                or len(ids)!=len(set(ids))):
            return hold("NFL_WEEK_PLAN_EVENT_COVERAGE_INVALID")
        try:
            from datetime import date
            if date.fromisoformat(day).isoformat()!=day:
                return hold("NFL_WEEK_PLAN_EVENT_COVERAGE_INVALID")
        except ValueError:
            return hold("NFL_WEEK_PLAN_EVENT_COVERAGE_INVALID")
        dates.append(day)
        allocated.extend(ids)
    if (len(set(dates))!=len(dates) or len(set(allocated))!=len(allocated)
            or set(allocated)!=set(expected)):
        return hold("NFL_WEEK_PLAN_EVENT_COVERAGE_INVALID")
    if not isinstance(results,Mapping) or set(results)!=set(dates):
        return hold("NFL_WEEK_DATE_COVERAGE_MISMATCH")
    observed=[]
    run_ids=set()
    for b in batches:
        day=b["requested_slate_date"]
        r=results[day]
        if not isinstance(r,Mapping) or r.get("requested_slate_date")!=day or r.get("can_execute") is not False or r.get("global_terminal_authority")!=TERMINAL or r.get("automation_auth")!="GITHUB_ACTIONS_OIDC" or r.get("terminal") is not True or r.get("run_status")!="COMPLETED" or r.get("requested_timezone")!="America/Chicago":
            return hold("NFL_WEEK_DAILY_AUTH_OR_TERMINAL_INVALID")
        run_id=r.get("run_id")
        if not isinstance(run_id,str) or not run_id.strip() or run_id in run_ids:
            return hold("NFL_WEEK_DAILY_RUN_ID_INVALID_OR_DUPLICATE")
        run_ids.add(run_id)
        rec=r.get("reconciliation") or {}
        audit=(r.get("cross_sport_discovery_audit") or {}).get("reconciliation") or {}
        if rec.get("balanced") is not True or audit.get("row_reconciliation")!="PASS" or type(audit.get("events_discovered")) is not int or type(audit.get("events_accounted")) is not int or audit.get("events_accounted")<0 or audit.get("events_accounted")!=audit.get("events_discovered"):
            return hold("NFL_WEEK_RECONCILIATION_FAILED")
        rows = r.get("rows")
        # External daily receipts are untrusted until shape and identity validate.
        # Malformed payloads must produce a typed hold, never an exception or pass.
        if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
            return hold("NFL_WEEK_DAILY_ROWS_INVALID")
        day_ids=[]
        for row in rows:
            if row.get("lane")!="MONEYLINE":
                continue
            identity = row.get("identity")
            if not isinstance(identity, Mapping):
                return hold("NFL_WEEK_DAILY_ROW_IDENTITY_INVALID")
            if identity.get("sport")!="NFL":
                continue
            eid=identity.get("official_event_id") or identity.get("event_id") or identity.get("id")
            if not isinstance(eid, str) or not eid.strip() or row.get("can_execute") is not False or "MODEL_INVOCATION_BUDGET_REACHED" in str(row.get("result") or {}):
                return hold("NFL_WEEK_ROW_INVALID_OR_EXHAUSTED")
            day_ids.append(eid)
        if len(day_ids)!=len(set(day_ids)) or set(day_ids)!=set(b["expected_event_ids"]):
            return hold("NFL_WEEK_DAILY_EVENT_MISMATCH")
        observed.extend(day_ids)
    if len(observed)!=len(set(observed)) or set(observed)!=set(plan["expected_event_ids"]):
        return hold("NFL_WEEK_EXACT_ONCE_MISMATCH")
    return {"status":"WEEK_SNAPSHOT_RECONCILED_NOT_ACTION_OR_MODEL_CERTIFIED","nfl_rows":len(observed),"can_execute":False,"probability_publishable":False,"global_terminal_authority":TERMINAL}
