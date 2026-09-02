"""Schedulable entrypoint for V17 prop forward-cohort capture.

Intended for a server-owned scheduler (for example a Render cron service), not
for the Custom GPT.  Importing the accepted production entrypoint preserves the
same environment-gated score_prop policy used by production HTTP requests.
"""
from __future__ import annotations

import json
import os

import api_ncaaf_acceptance as production
from v17.prop_forward_cohort_runtime import PropForwardCohortRequest, run_prop_forward_cohort


def main() -> int:
    try:
        max_snapshots = int(os.getenv("WOW_PROP_FORWARD_COHORT_MAX_SNAPSHOTS", "100"))
    except ValueError:
        max_snapshots = 100
    max_snapshots = max(1, min(max_snapshots, 200))

    result = run_prop_forward_cohort(
        PropForwardCohortRequest(max_snapshots=max_snapshots),
        db=production._db_client(),
        market_api=production.base.market_api,
    )
    # Deliberately emit only non-secret aggregate receipt fields.  Row/player
    # payloads remain in the governed ledger and are not sprayed into cron logs.
    print(json.dumps({
        "run_status": result.get("run_status"),
        "snapshots_considered": result.get("snapshots_considered"),
        "directions_considered": result.get("directions_considered"),
        "captured_forward_predictions": result.get("captured_forward_predictions"),
        "calibration_readiness": result.get("calibration_readiness"),
        "calibrator_fit_performed": False,
        "can_execute": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
