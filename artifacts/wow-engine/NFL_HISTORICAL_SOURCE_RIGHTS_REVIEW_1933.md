# WOW #1933 — NFL historical source-rights review (research-only)

**Review status:** evidence assembled, **NOT approved for staging, model training, commercial redistribution, or production publication**. This document is a source-rights checklist, not a legal clearance or an alteration to WOW's source-entitlement registry.

## Source-by-source separation

| Dataset | Public license / source evidence | Pilot use | Remaining decision |
|---|---|---|---|
| nflverse 2025 weekly player stats, `stats_player_week_2025.csv` | `nflverse/nflverse-data` repository publishes **CC BY 4.0**. nflverse's organization overview says most non-FTN releases are CC BY 4.0, broadly permitting noncommercial and commercial use subject to attribution; rights apply only to rights licensor can grant. | Read-only, exact hash match against WOW's existing immutable player-training-source reference; 19,422 raw rows. | Review upstream data provenance, field-level restrictions and full attribution notice. Maintain current `LICENSE_REVIEW_REQUIRED` in WOW historical manifest unless separately authorized. |
| nflverse/nfldata `games.csv` | Related nflverse data project. Do not assume a dataset's exact terms solely from a different repository's license banner; verify the relevant asset/release and any embedded third-party information. | Read-only SHA-256 verification of one pinned upstream Git commit; use ONLY identity/final scores/kickoff/team names. Do not consume odds/spread/market columns as sporting labels. | Asset-level licensing/attribution and data provenance review, including games.csv's embedded market fields. |
| ESPN NFL public historical scoreboard JSON | ESPN is a **separate upstream publisher**, not covered by nflverse's license. The public endpoint working in a browser does **not** prove authorization to persist, republish, bulk redistribute or train on its data. | Bounded, no-credential, one-off read-only scoreboard comparisons. Only hashes, final-score identity crosswalk and totals in CI summaries; no storage of raw ESPN response as a production training asset. | Review ESPN terms, permitted automated requests and any commercial usage; seek approved data feed/license if required. Maintain `UNREVIEWED_FOR_STAGING_OR_TRAINING`. |

## Exact source records

- [nflverse project licensing overview](https://github.com/nflverse) — current licensing description for non-FTN vs FTN releases and attribution obligations.
- [nflverse-data CC BY 4.0 license](https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md). The terms are limited to the licensor's own licensable rights and exclude patent/trademark grants, endorsement and independent third-party rights.
- [nflverse-data package description](https://github.com/nflverse/nflverse-data/blob/main/DESCRIPTION) — declares `License: CC BY 4.0`.
- [Upstream issue #101](https://github.com/nflverse/nflverse-data/issues/101) asked whether exact fields and intended paid-feature use were covered; do not treat issue closure or lack of response as field-level approval.
- Frozen nflverse player file hash: `e5e0615b3d96a3eaebfaee91e55afb4a4e7fe0caf057454177bcd7d6ad4bcfc2`.
- Frozen nflverse/nfldata schedule commit: `a2c9f13593a311b09bfd2d00851e55dbdafb3c8d`; expected SHA: `6011ae7b41fe3dcea1572b6e508fc8af3b8e03cf063df2681eb9e9a8c19a4332`.

## Specific uses intentionally excluded

- No storage or onward publication of raw ESPN event feeds, no mirrored data API, no ESPN model input, no live betting endpoint use, and no production historical-feature population from the parity tool.
- No FTN-specific charting, logos or photographs, and no unauthorized use of sports trademarks; NFL betting odds and market prices must retain a separate MARKET evidence contract.
- No training, calibration, calibrated lower bounds, publishability decisions or execution derived from any source-quality report. WOW V17 specialist/terminal controls remain unchanged.
- The historical nflverse source manifest still reads `LICENSE_REVIEW_REQUIRED`; only a separate, reviewed entitlement change can supersede it.

## Acceptance checklist for a later independently reviewed rights decision

- Confirm precise upstream dataset and field provenance, whether non-FTN player statistics and schedules are covered, and whether any non-CC data have been mixed into the selected field set.
- Specify exact permitted purposes: research, at-rest staging, internal training, user-visible statistics, derivative model output, and redistribution, **separately for nflverse and ESPN**.
- Record attribution and modifications notices where required; do not imply league, ESPN or nflverse endorsement.
- Propose a rights approval record signed by an authorized reviewer and bound to immutable dataset IDs/versions. This draft PR creates **no** approval mechanism, no override token and no authenticated attestation.

**Disposition:** `RIGHTS_REVIEW_PENDING`, `staging_write_authorized=false`, `training_ready=false`, `probability_publishable=false`, `can_execute=false`. Independent public-source parity is purely corroborative for historical final results.
