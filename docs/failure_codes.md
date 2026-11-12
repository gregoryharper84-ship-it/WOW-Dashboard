# WOW V17 — Typed Failure Code Registry (initial source-acquisition subset)

**Canonical artifact:** `docs/failure_codes.md` — versioned failure-code register initiated for governed V17 error semantics.

**Status:** `PARTIAL_REGISTRY_BACKFILL_REQUIRED`. Existing historical errors throughout the repository are not yet comprehensively registered. This file must not be interpreted as a complete system-wide taxonomy or as runtime authority.

**Ownership:** New archive-code family below belongs only to `WOW-V17-SOURCE-ACQUISITION` / `v17/cricsheet_source_candidate.py`. A source-code registration does not authorize a dataset, trained model, probability publication, terminal override, or a wager.

**Envelope:** `SOURCE_RESEARCH_HELD` is the collector CLI diagnostic prefix, not a substitute for the exact code. The application status remains `BLOCKED` (research-source preflight failure). No source candidate may pass training or model certification from an acquisition receipt alone. `can_execute=false` and `V17_TERMINAL_REDUCER` are invariant.

## Registered codes introduced by the Cricsheet research collector

| Code | Owning gate | Exact trigger | Disposition |
| --- | --- | --- | --- |
| `SOURCE_ARCHIVE_COMPRESSION_RATIO_INVALID` | `SOURCE_ARCHIVE_INTEGRITY` | An entry exceeds the anti-zip-bomb compression ratio limit | `BLOCKED` |
| `SOURCE_ARCHIVE_DUPLICATE_MATCH_ID` | `SOURCE_ARCHIVE_INTEGRITY` | Multiple entries claim the same match ID after directory normalization | `BLOCKED` |
| `SOURCE_ARCHIVE_DUPLICATE_MEMBER` | `SOURCE_ARCHIVE_INTEGRITY` | Archive repeats a ZIP member path | `BLOCKED` |
| `SOURCE_ARCHIVE_ENTRY_TOO_LARGE` | `SOURCE_ARCHIVE_INTEGRITY` | A single archive entry exceeds its permitted uncompressed size | `BLOCKED` |
| `SOURCE_ARCHIVE_EXECUTABLE_MEMBER_FORBIDDEN` | `SOURCE_ARCHIVE_INTEGRITY` | Archive carries a disallowed executable/script/HTML filename | `BLOCKED` |
| `SOURCE_ARCHIVE_FORMAT_INVALID` | `SOURCE_ARCHIVE_INTEGRITY` | Bytes cannot be opened as a valid ZIP container | `BLOCKED` |
| `SOURCE_ARCHIVE_INTERNATIONAL_SCOPE_UNVERIFIED` | `SOURCE_ARCHIVE_INTEGRITY` | Match team_type is not explicitly international within the international-T20 archive route | `BLOCKED` |
| `SOURCE_ARCHIVE_LICENSE_MISSING_OR_AMBIGUOUS` | `SOURCE_ARCHIVE_INTEGRITY` | Required license text file is absent or duplicated | `BLOCKED` |
| `SOURCE_ARCHIVE_LICENSE_NOT_RECOGNIZED` | `SOURCE_ARCHIVE_INTEGRITY` | License text is inconsistent with expected ODC-BY/attribution markings | `BLOCKED` |
| `SOURCE_ARCHIVE_LICENSE_READ_FAILED` | `SOURCE_ARCHIVE_INTEGRITY` | Required license entry cannot be read with supported ZIP methods | `BLOCKED` |
| `SOURCE_ARCHIVE_MATCH_DATE_INVALID` | `SOURCE_ARCHIVE_INTEGRITY` | ISO-format match date is not a valid calendar date | `BLOCKED` |
| `SOURCE_ARCHIVE_MATCH_IDENTITY_OR_DATE_INVALID` | `SOURCE_ARCHIVE_INTEGRITY` | Two distinct team names or valid ISO-format source dates are missing | `BLOCKED` |
| `SOURCE_ARCHIVE_MATCH_ID_INVALID` | `SOURCE_ARCHIVE_INTEGRITY` | JSON match entry filename does not have the expected numeric identifier | `BLOCKED` |
| `SOURCE_ARCHIVE_MATCH_JSON_INVALID` | `SOURCE_ARCHIVE_INTEGRITY` | A match member fails ZIP read or JSON decoding | `BLOCKED` |
| `SOURCE_ARCHIVE_MATCH_TYPE_UNVERIFIED` | `SOURCE_ARCHIVE_INTEGRITY` | Match info is malformed or sport format is not T20/IT20 | `BLOCKED` |
| `SOURCE_ARCHIVE_MEMBER_COUNT_INVALID` | `SOURCE_ARCHIVE_INTEGRITY` | Archive contains zero or more than configured maximum members | `BLOCKED` |
| `SOURCE_ARCHIVE_NO_MATCHES` | `SOURCE_ARCHIVE_INTEGRITY` | Archive contains no JSON match candidates | `BLOCKED` |
| `SOURCE_ARCHIVE_ORIGIN_UNVERIFIED` | `SOURCE_ARCHIVE_INTEGRITY` | Final archive response origin is not the expected publisher HTTPS origin | `BLOCKED` |
| `SOURCE_ARCHIVE_OUTCOME_CONTRADICTORY` | `SOURCE_ARCHIVE_INTEGRITY` | Match outcome claims both winner and non-winner result | `BLOCKED` |
| `SOURCE_ARCHIVE_OUTCOME_MISSING` | `SOURCE_ARCHIVE_INTEGRITY` | Match info lacks an object-valued outcome | `BLOCKED` |
| `SOURCE_ARCHIVE_OUTCOME_UNRESOLVED` | `SOURCE_ARCHIVE_INTEGRITY` | Match neither specifies a valid winner nor supported tie/draw/no-result | `BLOCKED` |
| `SOURCE_ARCHIVE_PATH_INVALID` | `SOURCE_ARCHIVE_INTEGRITY` | Archive member has absolute, traversal, backslash or malformed path | `BLOCKED` |
| `SOURCE_ARCHIVE_PIN_MISMATCH` | `SOURCE_ARCHIVE_INTEGRITY` | Expected pinned SHA256 is malformed or does not equal actual archive hash | `BLOCKED` |
| `SOURCE_ARCHIVE_REDIRECT_UNTRUSTED` | `SOURCE_ARCHIVE_INTEGRITY` | Publisher HTTPS download redirects to a different or untrusted origin | `BLOCKED` |
| `SOURCE_ARCHIVE_SETTLEMENT_TEAM_MISMATCH` | `SOURCE_ARCHIVE_INTEGRITY` | Declared winner is not a string matching either match team | `BLOCKED` |
| `SOURCE_ARCHIVE_SIZE_INVALID` | `SOURCE_ARCHIVE_INTEGRITY` | Empty, non-byte or oversized input before archive inspection | `BLOCKED` |
| `SOURCE_ARCHIVE_SYMLINK_FORBIDDEN` | `SOURCE_ARCHIVE_INTEGRITY` | ZIP metadata describes a symbolic-link member | `BLOCKED` |
| `SOURCE_ARCHIVE_UNCOMPRESSED_LIMIT` | `SOURCE_ARCHIVE_INTEGRITY` | Sum of uncompressed sizes exceeds bounded extraction budget | `BLOCKED` |

## Change and acceptance discipline

Any new typed code introduced in a PR must appear in this file in the same PR, with its owner, trigger and disposition. Native backend failure semantics (such as `MODEL_UNAVAILABLE`, `MODEL_INPUTS_INSUFFICIENT`, `MODEL_SCORER_FAILED`, `MODEL_OUTPUT_INVALID`, and Action errors) remain distinct and are **not redefined** by this source-only registry. Existing code-family backfill requires independent inventory, conflict reconciliation and governed adoption; it is deliberately not claimed by this narrow PR.

For these codes: do not reinterpret archive preflight errors as scorer failure, certified model absence or failed official settlement. The collector may emit a receipt only when its bounded validation succeeds; otherwise it must retain the precise typed hold. Published source availability, catalog presence and training entitlement are separate.

**Verification:** `tests/test_v17_cricsheet_failure_code_registry.py` asserts that every `SourceCandidateHold` literal in the collector is present exactly once here and forbids silent code-minting without registration.
