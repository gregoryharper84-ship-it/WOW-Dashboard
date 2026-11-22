# V17 Team/Event Certification Intake — Read-Only Boundary

**Change class:** reliability / integrity preflight only. This module is not a fitted model, calibration service, certification authority, terminal reducer, or deployment release gate by itself.

## Trust sequence

1. Repository file: `v17/certification/team_event/*.json` is a **staged review candidate only**. `load_receipts()` uses the existing `parse_certification()` shape validation and rejects unreadable, duplicated, malformed, symlinked, or mismatched files. SHA-256 **strings are not the same thing as proof that artifact bytes were hashed**.
2. The trusted, server-owned service-role RPC `wow_v17_active_team_event_certification` supplies the authoritative certification record via the existing `resolve_certification()` method. Matching **all** parsed certification fields with the staged file is necessary for `READY_FOR_GOVERNANCE_PROMOTION`; repository JSON alone cannot reach that state. The trusted service still owns independent artifact-byte verification, replay, verifier provenance, and calibration evidence.
3. An independent governed review decides whether a verified record may enter `CERTIFIED_TEAM_EVENT_SPORTS`. `intake()` cannot mutate that ledger. `catalog_integrity()` fails if a new static catalog entry lacks the matching staged and trusted evidence. MLB's existing legacy entry is narrowly grandfathered.
4. Even a `CERTIFIED_CATALOG_BACKED` intake diagnostic always returns `probability_publishable=false` and `rank_eligible=false`. Only the exact fitted sport specialist, calibrated model package, freshness gates, durable Action receipt, and `V17_TERMINAL_REDUCER` may establish row-level sporting publication.

## Functions

- `load_receipts(directory=None)`: strictly parse all staged receipts. Any invalid entry stops the inventory; absent default directory is an empty candidate set.
- `intake(sport, directory=None, registry_client=None)`: read-only per-sport diagnostic: `CERTIFIED_LEGACY`, `BLOCKED`, `READY_FOR_GOVERNANCE_PROMOTION`, or `CERTIFIED_CATALOG_BACKED`. The registry client must be provided by a trusted server, **never** user or board payload.
- `catalog_integrity(directory=None, registry_client=None)`: fail-closed catalog-versus-receipt proof audit. With no trusted registry client, any new entry cannot pass even if the receipt JSON says PASS.
- `pending_promotions(directory=None, registry_client=None)`: list only staged, registry-matched, not-yet-cataloged sports. With no trusted client it returns an empty list for valid staged files.

## Verification and production acceptance

The associated `tests/test_v17_team_event_certification_intake.py` suite uses synthetic records and a fake RPC to test the boundary, **not** to prove a fitted artifact exists. Required before production integration: exact-head repository CI, independent security/QA review, trusted service-role RPC acceptance, real artifact and calibration digests, chronological replay/holdout metrics, reviewed catalog admission, protected merge, Render/Action SHA parity, row-level scorer and reducer receipts. Existing registered but uncertified sports must remain unpublishable.

Do not rewrite tests to call a research fixture certified. No manual probability, universal haircut, odds-derived forecast, or fake receipt may fill missing evidence.

`can_execute=false`; `V17_TERMINAL_REDUCER` remains the only terminal authority.
