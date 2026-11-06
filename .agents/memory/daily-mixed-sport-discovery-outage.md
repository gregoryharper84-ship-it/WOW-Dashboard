# Daily PROPS mixed-sport discovery outage propagation — 2026-11-05

- date: 2026-11-05
- typed_reason: DAILY_PROP_SPORT_DISCOVERY_UNOBTAINABLE
- mechanism: Composite acquisition retains a failed MLB discovery leg as COMPLETED_WITH_ROW_BLOCKERS when NFL succeeds. Daily runtime previously propagated only DATA_UNOBTAINABLE for the *composite*, so run_status could incorrectly report COMPLETED despite a missing sport's upstream evidence.
- detection_point: First integrated two-sport Daily acceptance test, before release. Checking per-leg status only in the acquisition response was insufficient; the terminal run boundary must receive a typed blocker.
- fix: Propagate any sport_coverage DATA_UNOBTAINABLE into the Daily run blockers, preserving existing per-sport evidence and successful NFL rows. Keep can_execute=false.
- prevention_artifact: artifacts/wow-engine/v17/test_daily_response_contract_acceptance.py::test_mixed_sport_discovery_outage_is_a_daily_run_blocker
- link: https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2485
