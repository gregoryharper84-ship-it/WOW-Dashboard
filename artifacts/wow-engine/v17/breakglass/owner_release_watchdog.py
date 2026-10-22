#!/usr/bin/env python3
"""Same-owner, separate-process emergency ruleset restoration watchdog.

No second human, signer, reviewer or GitHub account is required. It survives
operator-process termination but NOT loss of its host, network or credentials.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from owner_release_recovery import (
    GitHub, RecoveryHold, RULESET_ID, parse_intent, require,
    sha256, snapshot, temporary_ruleset, restore, expected_check_set,
    checks_from_ruleset, canonical,
)

def write_ready(ready: Path, pid: int, digest: str, expires_monotonic: float):
    obj = {"pid":pid,"intent_digest":digest,"expires_monotonic":expires_monotonic}
    tmp = ready.with_name(ready.name + ".tmp")
    fd = os.open(tmp, os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(canonical(obj));f.flush();os.fsync(f.fileno())
    os.replace(tmp,ready)

def watch(api, intent: dict, baseline: dict, ready: Path, intent_digest: str, *,
          period: float = 1, clock=time.monotonic, sleep=time.sleep):
    require(checks_from_ruleset(baseline) == expected_check_set(), "WATCHDOG_BASELINE_NOT_FIVE_CHECKS")
    require(sha256(baseline) == intent["baseline_ruleset_sha256"], "WATCHDOG_BASELINE_HASH")
    orig = sha256(baseline)
    temporary = sha256(temporary_ruleset(baseline))
    require(sha256(snapshot(api.get(f"rulesets/{RULESET_ID}"))) == orig,
            "WATCHDOG_NOT_ARMED_BEFORE_CHANGE")
    started = clock()
    deadline = started + intent["window_seconds"]
    write_ready(ready,os.getpid(),intent_digest,deadline)
    print("WATCHDOG_ARMED_INDEPENDENT_PROCESS",flush=True)
    observed_change = False
    while True:
        try:
            actual = sha256(snapshot(api.get(f"rulesets/{RULESET_ID}")))
        except (RecoveryHold,KeyError,TypeError,ValueError):
            actual = None
        if actual == temporary:
            observed_change = True
        if actual == orig and observed_change:
            print("WATCHDOG_READBACK_FIVE_CHECKS_RESTORED",flush=True)
            return
        if actual not in (orig, temporary, None):
            # Unexpected policy drift while the maintenance window is active.
            restore(api,baseline)
            raise RecoveryHold("WATCHDOG_UNEXPECTED_POLICY_DRIFT_RESTORED")
        if clock() >= deadline:
            # After an API outage, always attempt restoration, never infer green.
            if actual != orig:
                restore(api,baseline)
                print("WATCHDOG_DEADLINE_RESTORED_FIVE_CHECKS",flush=True)
            else:
                print("WATCHDOG_DEADLINE_BASELINE_INTACT",flush=True)
            return
        sleep(period)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--intent",type=Path,required=True)
    ap.add_argument("--baseline",type=Path,required=True)
    ap.add_argument("--ready",type=Path,required=True)
    a=ap.parse_args()
    raw=a.intent.read_bytes()
    intent=parse_intent(json.loads(raw))
    require(raw==canonical(intent),"WATCHDOG_INTENT_NOT_CANONICAL")
    baseline=json.loads(a.baseline.read_bytes())
    digest=hashlib.sha256(raw).hexdigest()
    watch(GitHub(),intent,baseline,a.ready,digest)

if __name__=="__main__":
    try:
        main()
    except (RecoveryHold,OSError,ValueError,TypeError,KeyError) as exc:
        print("P0_OWNER_WATCHDOG:"+str(exc),file=sys.stderr,flush=True)
        raise SystemExit(2)
