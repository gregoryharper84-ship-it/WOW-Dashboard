# NFL hourly forward evidence: missing Thursday in Tuesday window

- **Date:** 2026-11-17
- **Typed reason:** existing `NO_CANDIDATES` / zero-eligible-event acquisition outcome; no new failure code introduced
- **Mechanism:** The hourly OIDC NFL core-prop producer had a hard-coded `today`, `tomorrow` date loop, so a real Thursday NFL fixture on 2026-11-19 was outside the 2026-11-17 producer's acquisition window. This predicted 0 attempts/0 snapshots despite a valid future fixture.
- **Detection point:** On the first successful zero-result hourly acquisition run, compare the requested calendar window to the next official NFL kickoff. A successful HTTP response is not discovery coverage.
- **Fix:** Include local Central date +2 in the existing request loop; preserve endpoint, OIDC, 32-candidate per-date cap, rotating page, fail-closed response flags and nonexecution.
- **Prevention artifact:** `artifacts/wow-engine/v17/test_nfl_prop_evidence_workflow.py::test_nfl_hourly_evidence_producer_includes_thursday_on_tuesday_without_extra_route`; focused structural red/green and `TZ=America/Chicago date -d '2026-11-17 +2 days' +%F` returning `2026-11-19`.
- **Link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/2285
- **Evidence limit:** No authenticated scoring run, permitted market quote, immutable snapshot, or terminal acceptance is established by this source patch. Protected workflow change remains P-class until independent exact-head approval and production proof.
