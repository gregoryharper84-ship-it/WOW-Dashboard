#!/usr/bin/env python3
"""Independent recovery witness. Run with a SEPARATE authorized GitHub credential.

Start on a different machine before the operator's execute command. Once armed,
it observes the exact temporary four-check policy and restores the full original
policy at the signed time limit even when the operator process disappears.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys
import time

from release_breakglass import (
    GitHub, RecoveryHold, RULESET_ID, canonical, checks_from_ruleset, expected_check_set,
    parse_intent, require, restore, sha256, snapshot, temporary_ruleset,
    verify_separate_principals, verify_signature,
)


def watch(api: GitHub, intent: dict, baseline: dict, *, period: float = 2.0,
          clock=time.monotonic, sleep=time.sleep, ready=print) -> None:
    """Always read back. Restore on expiry or unexpected drift; never merge."""
    require(checks_from_ruleset(baseline) == expected_check_set(), "WITNESS_BASELINE_NOT_FIVE_CHECKS")
    original_hash = sha256(baseline)
    require(original_hash == intent["baseline_ruleset_sha256"], "WITNESS_SNAPSHOT_DIGEST_MISMATCH")
    temporary_hash = sha256(temporary_ruleset(baseline))
    live_hash = sha256(snapshot(api.get(f"rulesets/{RULESET_ID}")))
    require(live_hash == original_hash, "WITNESS_WAS_NOT_ARMED_BEFORE_RULESET_CHANGE")
    activated = clock()
    # 2-minute cap includes all GitHub API operations; failed retrieval remains
    # a red condition. The witness must not be silently stopped by operator.
    deadline = activated + intent["window_seconds"]
    ready("WITNESS_ARMED; distinct credential required; deadline in " + str(intent["window_seconds"]) + "s")
    observed_temporary = False
    while True:
        try:
            current_hash = sha256(snapshot(api.get(f"rulesets/{RULESET_ID}")))
        except (RecoveryHold, KeyError, TypeError):
            # No success inference on transport failure. Retry with bounded interval.
            current_hash = None
        if current_hash == temporary_hash:
            observed_temporary = True
        if current_hash == original_hash and observed_temporary:
            ready("WITNESS_CONFIRMED_FIVE_CHECK_RESTORATION")
            return
        if current_hash not in (original_hash, temporary_hash, None):
            # An unexpected policy mutation during exclusive window is P0.
            restore(api, baseline)
            raise RecoveryHold("WITNESS_UNEXPECTED_POLICY_DRIFT_RESTORED")
        if clock() >= deadline:
            if current_hash != original_hash:
                restore(api, baseline)
                ready("WITNESS_DEADLINE_RESTORE_EXECUTED")
            else:
                ready("WITNESS_DEADLINE_FIVE_CHECKS_INTACT")
            return
        sleep(period)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intent", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--owner-signature", type=Path, required=True)
    parser.add_argument("--sirt-signature", type=Path, required=True)
    parser.add_argument("--owner-signers", type=Path, required=True)
    parser.add_argument("--sirt-signers", type=Path, required=True)
    args = parser.parse_args()
    data = args.intent.read_bytes()
    intent = parse_intent(json.loads(data))
    require(data == canonical(intent), "WITNESS_INTENT_NOT_CANONICAL")
    verify_separate_principals(args.owner_signers, args.sirt_signers, intent["reviewer_login"])
    verify_signature(data, args.owner_signature, args.owner_signers, "wow-owner")
    verify_signature(data, args.sirt_signature, args.sirt_signers,
                     "wow-sirt-" + intent["reviewer_login"])
    baseline_data = json.loads(args.baseline.read_bytes())
    watch(GitHub(), intent, baseline_data)


if __name__ == "__main__":
    try:
        main()
    except (RecoveryHold, OSError, ValueError, KeyError, TypeError) as exc:
        print("WITNESS_P0:" + str(exc), file=sys.stderr)
        sys.exit(2)
