# Solo verification: unsigned attestation and owner-opt-in cannot prove independence

- **Date:** 2026-11-19
- **Typed reason:** `SOLO_EXTERNAL_ADMIN_ATTESTATION_INVALID` / `SOLO_INDEPENDENT_ADMIN_NOT_PROVEN` (existing registered code types).
- **Mechanism:** `_validate_external_admin_attestation` accepted a locally supplied JSON object if `admin_user_id` differed from the owner and `attestation_sha256` looked like 64 hex characters, with no authenticated issuing identity, signed payload or external administration binding. Separately, `allow_solo_owner_admin_for_protected=True` let an owner-controlled environment toggle return `SOLO_PROTECTED_EVIDENCE_READY` for P/C without independent administrative sign-off. These are owner-self-approval paths, even though output says it cannot merge or deploy.
- **Prediction:** An unsigned owner-controlled attestation or owner-admin opt-in must produce `HOLD` at the same head SHA even when three native checks, CodeQL and adversarial tests succeed. They cannot be treated as independent protected approval.
- **Detection point:** First executable negative test of provenance / authority on PR #2738 (not optional coverage chasing).
- **Observed RED:** hosted rapid workflow on `81fa58540af78d1d464c7b301380f39b047448e9`, run `44615604403`, 2 failed / 91 passed. Both new security tests observed `EVIDENCE_READY` instead of `HOLD`.
- **Fix (unmerged candidate):** Reject all unattested external-admin JSON as invalid until an independently authenticated signer/issuer is actually implemented; an owner-controlled policy opt-in cannot promote P/C evidence. Preserve compatible arguments without granting authority. R-class deterministic verification remains available. Any P/C claim stays HOLD without verified independent provenance.
- **Prevention artifact:** `artifacts/wow-engine/tests/test_v17_solo_automated_verification.py` — two permanent negative tests + updated expectations for protected P/C cases; `artifacts/wow-engine/v17/solo_automated_verification.py`.
- **Link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2738
- **Status:** PR is draft, hosted GREEN on final head must be checked; not merged/deployed. No main, ruleset, fitted model, qualification, terminal, or trading authority changes.
- **Product scoreboard:** no new certified/qualified rows, no verified Render/main deployment attributable to this change.
