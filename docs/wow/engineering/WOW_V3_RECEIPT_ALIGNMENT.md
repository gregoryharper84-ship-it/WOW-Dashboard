# V3 owner-authored receipt alignment — review candidate

The single-owner V3 policy merged in PR #2303 at
`830694b69a55a95663079e5ae736255e0ac7ef51`. Its QA classifier permits
owner-authored protected engineering changes, while the dispatcher and bootstrap
receipt verifier still reject them unconditionally. This candidate aligns those
classifiers. It is a protected authority change requiring independent review;
this document is neither acceptance nor deployment evidence.

Owner identity must match authenticated GitHub numeric ID, login and human
account type. Partial matches fail closed. Non-owner changes still require an
authenticated owner APPROVED review at the exact source head.

New owner-authored bootstrap receipts use schema 3 within the explicit
TRUST_ROOT_BOOTSTRAP certification mode. They contain no owner review and claim
no independent semantic review. Their scope binds the exact head and explicitly
sets release_authorized=false and owner_merge_required=true. All existing
required exact-head checks, App identity, expiry, provenance and live rechecks
remain mandatory. A genuine semantic REJECT remains binding in the dispatcher.

Schema 2 bootstrap receipts retain their original non-owner-only meaning.
Ordinary signed-review schema 3 receipts remain a separate certification mode.
The CLI build/read regression checks this mode separation and rejects missing
live checks. Tampered authorization flags and mismatched identities are rejected.

The owner must still deliberately merge through the protected release path.
This change does not supply that action, a security review, production
acceptance, model certification, calibration, a terminal override or permission
to wager. can_execute remains false. Deployment and production proof remain
separate requirements after genuine acceptance.
