# V28 independent QA mode conflict and single-policy prevention

- date: 2026-11-18
- typed_reason: QA_REVIEW_MODES_CONFLICT (historical) -> QA_REVIEW_MODE_UNCONFIGURED / QA_REVIEW_MODE_INVALID
- mechanism: Independent review and zero-credit flags were both true, which predicts QA failure before review-policy capture and leaves no valid source receipt.
- detection_point: GitHub QA run 44325892151 at exact PR #2715 head 222bca96; configuration preflight should have rejected the contradictory settings before expensive work.
- fix: Replace two independent Booleans with repository-wide WOW_QA_MODE; preserve fail-closed independent/zero_credit/legacy semantics, manual canary allowlist and provider preflight.
- prevention_artifact: artifacts/wow-engine/test_v17_independent_release_checks.py selector and full-consumer contract tests; one wow-verify QA completion source.
- link: https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2715
- verification: source PR only until exact-head independent QA/Release acceptance, protected merge and production readback; do not mark FIXED_AND_VERIFIED before those checks.
