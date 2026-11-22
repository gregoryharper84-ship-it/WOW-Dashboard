# Viktor GitHub App audit provenance and non-author review — 2026-11-22

- **date:** 2026-11-22
- **typed_reason:** VIKTOR_CONTEXT_GOVERNANCE_INVALID (new advisory-only typed evidence failure in this candidate; no model behavior altered).
- **mechanism:** a GitHub issue comment displays the owner's username even when posted through Viktor; selecting by username/marker can mistake an ordinary owner comment for a tool-origin advisory. The active main ruleset also requires zero reviewer approvals, and the Viktor app metadata advertises broad write rights.
- **detection_point:** the first authenticated Viktor GitHub smoke-test API readback, before enabling scheduled external comments or worker intake.
- **causal_prediction:** username/marker-only matching would falsely admit an owner comment; validating the API-originated performed_via_github_app id/slug plus author ID, exact incident/SHA and strict JSON rejects that counterexample.
- **fix:** draft-only source-bound, URL-only evidence parser and insertion after existing worker dispatch; no standalone dispatch or release authority. Main reviewer rule and vendor permission scope need separate owner-admin correction.
- **prevention_artifact:** test_v17_viktor_audit_evidence.py, test_v17_viktor_workflow_integration.py, failure_codes.md registration, and exact-head independent release acceptance.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2796
- **verification:** 13 local pure-Python tests passed; protected GitHub CI and production end-to-end remain unverified. Not FIXED_AND_VERIFIED.

## Independent review findings and prevention update

- detection_point: Codex inline review at 179f2267 before protected promotion.
- mechanism: Claude exact-PR repair switches to an older worktree before new intake; this predicts `viktor_audit_evidence.py` absent and the repair is blocked. Also `urlsplit().port` raises on malformed ports, and issue paths containing prose survived earlier prefix-only validation.
- correction: copy helper from verified workflow SHA into runner temp before older-repair switch, reject malformed and instruction-bearing URL forms, retain red-to-green tests and no-authority behavior.
- prevention_artifact: test_v17_viktor_workflow_integration.py trusted-helper repair-checkout reproduction; test_v17_viktor_audit_evidence.py URL negative cases; PR #2796 protected review.
- status: implementation candidate; final exact-head CI and independent release review remain required.
