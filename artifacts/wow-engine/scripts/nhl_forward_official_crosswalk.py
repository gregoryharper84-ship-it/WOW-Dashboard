"""Research-only ESPN->NHL first-party identity crosswalk for frozen prospective events.

Uses the repository's existing v17.nhl_event_identity resolver. This is NOT a
new probabilistic specialist or permission to publish any model probability.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any, Callable


CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False


def resolve_rows(fixture: dict, *, resolver: Callable[...,dict] | None = None) -> dict:
    if resolver is None:
        from v17.nhl_event_identity import resolve_nhl_current_event_identity
        resolver = resolve_nhl_current_event_identity
    if fixture.get("schema_version") != "WOW_FORWARD_MODEL_IDENTITY_FIXTURE_V1" or fixture.get("can_execute") is not False:
        raise ValueError("INVALID_FROZEN_IDENTITY_FIXTURE")
    rows = (fixture.get("sports") or {}).get("NHL",{}).get("events")
    if not isinstance(rows,list):
        raise ValueError("NHL_FROZEN_EVENTS_MISSING")
    findings=[]
    seen=set()
    for event in rows:
        espn_id=str(event.get("espn_event_id") or "")
        if not espn_id or espn_id in seen:
            raise ValueError("NHL_FROZEN_EVENT_ID_DUPLICATE_OR_MISSING")
        seen.add(espn_id)
        report={"espn_event_id":espn_id,"home_espn_team_id":event.get("home_espn_team_id"),
                "away_espn_team_id":event.get("away_espn_team_id"),
                "event_start_utc":event.get("event_start_utc"),
                "can_execute":False,"probability_publishable":False}
        try:
            result=resolver(
                event_start_time=event["event_start_utc"],
                home_team=event["home_name"], away_team=event["away_name"],
            )
            if not isinstance(result,dict) or not str(result.get('event_id') or '').strip():
                report['status']='NHL_CANONICAL_IDENTITY_INVALID_RETURN'
            elif result.get('game_type') != 2 and str(result.get('game_type')) != '2':
                report['status']='NHL_CANONICAL_GAME_NOT_REGULAR_SEASON'
            elif result.get('identity_provider')!='NHL_PUBLIC_WEB_API' or result.get('can_execute') is not False:
                report['status']='NHL_CANONICAL_PROVENANCE_INVALID'
            else:
                report.update({'status':'NHL_CANONICAL_EVENT_IDENTITY_RECONCILED',
                               'nhl_official_event_id':str(result['event_id']),
                               'nhl_official_start_utc':result.get('event_start_time'),
                               'nhl_home_abbreviation':result.get('home_team'),
                               'nhl_away_abbreviation':result.get('away_team'),
                               'nhl_identity_source':result.get('identity_source'),
                               'identity_verified_at':result.get('identity_verified_at')})
        except RuntimeError as exc:
            code = getattr(exc, "code", "")
            report["status"] = code if isinstance(code, str) and code.startswith("NHL_") else "NHL_OFFICIAL_RESOLVER_RUNTIME_ERROR"
        except (KeyError,TypeError,ValueError):
            report['status']='NHL_CANONICAL_RESOLVER_INPUT_INVALID'
        findings.append(report)
    official=[r['nhl_official_event_id'] for r in findings if 'nhl_official_event_id' in r]
    if len(official)!=len(set(official)):
        for r in findings:
            if r.get('nhl_official_event_id') in {n for n in official if official.count(n)>1}:
                r['status']='NHL_CANONICAL_EVENT_ID_REUSED'
                r.pop('nhl_official_event_id',None)
    counts=dict(sorted(Counter(r['status'] for r in findings).items()))
    return {'schema_version':'WOW_NHL_FIRST_PARTY_FORWARD_IDENTITY_REPORT_V1',
            'sport':'NHL', 'events_evaluated':len(findings),
            'status_counts':counts,
            'status':'IDENTITY_RECONCILIATION_RESEARCH_ONLY',
            'model_feature_compatibility':'UNVERIFIED',
            'sporting_probability_available':False,
            'probability_publishable':False,'can_execute':False,
            'rows':findings}


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    try:
        fixture=json.loads(args.fixture.read_text())
        result=resolve_rows(fixture)
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
        print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2,sort_keys=True))
        return 0
    except (ValueError,OSError,TypeError) as exc:
        print(json.dumps({'status':'BLOCKED','reason_code':type(exc).__name__,'can_execute':False}))
        return 2

if __name__=='__main__':
    raise SystemExit(main())