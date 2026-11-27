# WOW Native Swarm: recurring research HOLD is not learning (2026-11-24)

- date: 2026-11-24
- typed_reason: NATIVE_SWARM_RESEARCH_EVIDENCE_NOT_SCREENED (research disposition, not a new canonical failure code)
- mechanism: the same NHL canonical event was researched repeatedly with five workers and zero cleared facts, while the existing receipt had no cross-run comparison. Nine Supabase receipts from only two events repeated the same HOLD, which predicts continued zero improvements until an authorized evidence source changes.
- detection_point: compare the second distinct GitHub run for the same official event before claiming learning.
- fix: bounded deterministic read-only cross-run learning report, with idempotent run IDs, distinct-event guard, and typed evidence-change outcomes. Include it in the existing Multi-Scout artifact using built-in GitHub Actions artifact retention; never retrain a scorer or auto-edit policy.
- prevention_artifact: tests/test_wow_native_swarm_learning.py; GitHub Action output \`swarm-learning.json\`; user-facing Supabase notebooks/receipts remain source of actual prior executions.
- link: https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/2823

This is *research-gap learning*, not posterior-model calibration or improved win prediction. A source must independently establish research reuse rights. A governed fitted specialist remains the only probability producer.
