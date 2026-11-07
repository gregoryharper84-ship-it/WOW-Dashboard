# NHL game-winner specialist restoration — status 2026-11-07

`can_execute = false` everywhere. This document records engineering evidence; it
does not certify a model, publish a probability, or authorize a wager.

## 1. Integration vehicle

- Existing integration PR: #2456 (`chatgpt/v17-p0-team-boundary-negatives-20261104`), head `eba72da8b5e4818bade2632b73708ae9d7659b24`.
- Source work PR: #2453 (`fix/v17-team-event-probability-boundary-20261104`), head `56508d896c34de3c12e7fccd4112bd7f10187dc5`.
- Working branch for this restoration: `fix/v17-nhl-specialist-restoration-20261107`, created at `eba72da8` and merged with current `main` `372bef19a04a1c053ad6ab3f649bdbcaf62c10b3`.
- No protected-main write, no competing main-target PR, no force push.

The three existing stacked fixes are retained:

1. `_strict_probability` + `_governed_package_blocker`: strict `0 < p < 1`,
   finite values only, and `lower <= point <= upper`.
2. NHL settlement vocabulary: the model outcome label
   `HOME_AWAY_INCLUDING_OT_SO` and the externally settled contract
   `FULL_GAME_MONEYLINE_INCLUDING_OVERTIME_SHOOTOUT` are both accepted; every
   other basis, and every non-string/unhashable basis, holds with
   `NHL_SETTLEMENT_BASIS_UNVERIFIED`.
3. Registered selected-side identity: unknown third clubs, `DRAW`, `TIE`, and
   malformed participant identities never inherit the home-oriented package.
   NFL rank admission now requires a literal `rank_eligible is True` and an
   explicit calibrated lower bound (a point estimate is not a bound).

Additional integration repair in this branch: selected-side matching now
resolves the specialist's canonical participants and NHL club aliases, so
`Boston Bruins` and `BOS` are one identity while an unrelated club still holds.

## 2. NHL route diagnosis (current `main` + #2456)

```text
POST /score-team-event-request
→ sport=NHL, league=NHL branch
→ _registered_score_request (shared TEAM_EVENT envelope)
→ v17.team_event_probability_preservation.score_team_event_request
→ v17.team_event_bridge_runtime.score_registered_team_event_request
→ TEAM_EVENT_BRIDGES["NHL"]
→ v17.multisport_team_event_bridges._score_one
→ NHL scorer
→ reduce_multisport_team_event (V17_TERMINAL_REDUCER)
→ _completed_registered (batch boundary)
→ immutable outcome receipt (can_execute=false)
```

Findings before this change:

- The live NHL scorer was a hand-tuned Elo + goalie/special-teams Monte Carlo
  simulation (`NHL_ELO_GOALIE_SPECIAL_TEAMS_OT_V1`). It was not the fitted
  chronological candidate.
- Its calibration artifact was read from
  `req.sport_specific_evidence["calibration_artifact"]` — caller-supplied model
  calibration, contrary to the governed-write contract. Missing caller evidence
  produced `MODEL_INPUTS_INSUFFICIENT / CALIBRATION_ARTIFACT_MISSING`.
- The scorer never emitted `selected_participant`, so the generic batch boundary
  could only hold `TEAM_EVENT_SELECTED_PARTICIPANT_IDENTITY_MISSING`.
- `_score_one` converted any unknown scorer exception into
  `MODEL_SCORER_FAILED`, which would have flattened typed input/artifact holds.
  That collapse is now explicitly prevented for `NHLFittedSpecialistError`.

## 3. Storage decision

NHL data belongs in the **existing shared governed tables**, not in new
NHL-named tables.

Evidence from `wow-engine-validation` (`iczfhsmjrrafhvcpmqhr`):

| Table | NHL rows |
| --- | --- |
| `public.wow_d1_source_events` | 11,991 |
| `public.wow_d1_training_rows` | 12,328 (6,128 distinct events) |
| `public.wow_d1_candidate_artifacts` | 36 |

There are no `*nhl*` table names. Creating arbitrary per-sport tables would fork
the governed lifecycle, provenance, immutability and grant model. The shared
`wow_d1_*` tables already implement the required writer ownership and
`can_execute=false` / non-publishable candidate contract.

## 4. Restored fitted specialist

New module: `v17/nhl_fitted_specialist.py`.

- Resolves the newest eligible `NHL_REGULAR_SEASON_LOGISTIC_V1` candidate
  server-side from `wow_d1_candidate_artifacts`; requests cannot supply
  coefficients, calibrator, version, or bounds.
- Verifies sport/league/model family/feature schema/source policy, lifecycle
  `CANDIDATE`, inert promotion/execution flags, research screen, artifact
  checksum (canonical JSON SHA-256), dataset hash, training-code SHA, split
  policy and exact partition counts.
- Rebuilds the 10-key `NHL_REGULAR_SEASON_FEATURES_V1` vector for the current
  event by replaying prior settled first-party rows from
  `wow_d1_source_events` with the exact training state machine (per-season
  state, Elo K=20, 10-game window, rest days).
- Applies the candidate lifecycle's selected calibrator. The current candidates
  selected `IDENTITY_RAW_PROBABILITY_V1`; empirical Wilson bins are supported
  when present.
- Emits exact model version, dataset hash, artifact checksum, training-code SHA,
  timeline hash and prior-game counts in the package.
- Emits `selected_participant` from the exact resolved clubs.
- Never publishes: `probability_publishable=false`, `rank_eligible=false`,
  `automatic_certification=false`, `automatic_promotion=false`,
  `can_execute=false`.

The NHL bridge now registers this server-resolved scorer. `calibration_artifact`
was removed from the NHL required-input contract.

## 4b. Append-only NHL provenance re-ingest

New module: `v17/nhl_source_reingest.py`; guarded route
`POST /internal/v17/nhl-source-reingest`.

- Acquires only official settled regular-season games (`gameType == 2`,
  `gameState` `FINAL`/`OFF`) for one season.
- Appends V2 `outcome_json` provenance: `game_type`, `neutral_site`,
  `game_state`, `provenance_version`, `ingest_code_sha`.
- Never updates or deletes. It reuses the existing unique identity
  `(sport, official_event_id, source_payload_sha256)` with
  `ignore_duplicates=True`, so repeated runs are idempotent.
- Uses a domain-separated, canonical **per-event envelope hash** for
  `source_payload_sha256`, independent of which club's schedule supplied the
  payload. The exact raw payload hash remains in
  `outcome_json.source_payload_commitment` with an explicit hash scope, so real
  legacy raw hashes cannot collide with V2 provenance rows. An intentionally
  constructed exact-hash collision is never overwritten; it fails closed as
  `NHL_SOURCE_REINGEST_PROVENANCE_INCOMPLETE`.
- Canonicalizes the settled state (`FINAL` and `OFF`) inside that envelope, so a
  lifecycle transition from `FINAL` to `OFF` cannot append a second provenance
  identity for one event. The raw reported state is still retained in
  `outcome_json.game_state`.
- Acquires and validates every game, including duplicate-club identity, before
  the first write. A source failure cannot leave a partial timeline. Database
  batching is not assumed atomic: a mid-batch failure returns a typed retryable
  block, and a full retry converges to exactly one row per event through the
  unique identity.
- Verifies the season after writing and reports `provenance_complete`;
  incomplete coverage returns `NHL_SOURCE_PROVENANCE_INCOMPLETE`. Concurrent
  attempts use the same deterministic identities and converge without
  multiplying source rows.
- Is installed only behind the existing authenticated internal maintenance
  dependency; anonymous requests are rejected before any database access.
- Supports `dry_run=true`, which performs acquisition and validation but no
  write.

Runtime replay now requires V2 provenance on every row it consumes. One
authoritative V2 row supersedes a legacy sibling for the same event; an event
with no V2 row still fails closed with `NHL_SOURCE_PROVENANCE_UNVERIFIED`. No
legacy row is silently treated as regular-season or non-neutral.

Real 2026-27 evidence captured on 2026-11-07: the official API returned 17
settled Boston regular-season games. A 32-game, two-club genuine timeline was
recorded at `tests/fixtures/nhl_2026_27_real_timeline.json`. The chosen target
`2026020257` (PIT 2 – BOS 6, 2026-11-05) has 15 home and 16 away prior games.
Replay of that genuine event matches the training pipeline feature vector
exactly and scores with the governed artifact.

This sandbox has no Supabase write credential, so the live append was not
executed against `wow-engine-validation`. Until the authorized route runs
there, replay of a genuine November event holds at
`NHL_SOURCE_PROVENANCE_UNVERIFIED` because no 2026-27 rows are persisted (and at
`NHL_PRIOR_GAMES_INSUFFICIENT` if the season is too early). The code path itself
is proven against the recorded official timeline.

## 5. Current governed evidence and blockers

Latest eligible candidate (read 2026-11-07):

```text
candidate_id       06a765ad-845f-4a57-986e-62a8d9adfaed
model_version      NHL_RS_LOGISTIC_V1-5528ab8891df79fd-372bef19a04a
model_family       NHL_REGULAR_SEASON_LOGISTIC_V1
dataset_hash       5528ab8891df79fdea3b94982525158e2110ed697715dacef4e67ff76309728b
artifact_checksum  f412153941de7ffd1a867cbd5b8290df4154fd525c570d2705b55566a67fb506
partitions         3676 train / 1226 calibration / 1226 untouched test
research_screen    PASS
source_review      REQUIRED
calibrator         IDENTITY_RAW_PROBABILITY_V1 (1226 calibration rows)
raw/test Brier     0.247669 vs 0.249564 baseline
```

Typed holds preserved:

- `NHL_FITTED_REGISTRY_UNAVAILABLE` / `MODEL_UNAVAILABLE` — no trusted registry.
- `NHL_FITTED_ARTIFACT_*` — missing, drifted, promoted, or checksum-tampered artifact.
- `NHL_STARTING_GOALIE_NOT_CONFIRMED`, `NHL_GOALIE_IDENTITY_UNVERIFIED`,
  `NHL_GOALIE_STATUS_STALE` — missing/stale starter evidence.
- `NHL_EVENT_IDENTITY_UNVERIFIED`, `NHL_EVENT_TIME_INVALID`,
  `NHL_EVENT_NOT_PREGAME`, `NHL_NEUTRAL_SITE_UNVERIFIED`,
  `NHL_SETTLEMENT_BASIS_UNVERIFIED` — invalid or unsupported inputs.
- `NHL_PRIOR_GAMES_INSUFFICIENT`, `NHL_SOURCE_EVENT_IDENTITY_CONFLICT`,
  `NHL_EVENT_ALREADY_SETTLED` — incomplete or conflicting chronological replay.
- `NHL_CALIBRATED_LOWER_BOUND_UNAVAILABLE` — the selected calibrator has no
  empirical residual/Wilson bound.
- `TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED`,
  `CALIBRATION_ARTIFACT_NOT_CERTIFIED` — certification gate.

### Why certification is not earned

1. NHL is not in `CERTIFIED_TEAM_EVENT_SPORTS` (only MLB is). The static catalog
   and the immutable fitted-certification registry contain no NHL receipt.
2. Every current NHL candidate selected identity calibration. Identity is a
   valid research candidate, but it supplies no calibrated lower bound, so
   `NHL_CALIBRATED_LOWER_BOUND_UNAVAILABLE` holds the selected package.
3. `source_review_status = REQUIRED`; no independent source review or
   prospective forward cohort exists.
4. The persisted NHL source timeline ends 2026-04-17. No 2026-27 settled rows
   are present, so a genuine November 2026 event currently holds at
   `NHL_SOURCE_PROVENANCE_UNVERIFIED` rather than scoring. The append-only
   re-ingest now exists and is proven locally, but it has not been run against
   the shared validation DB from this sandbox.
5. `wow_d1_source_events` historically did not persist `game_type` or
   `neutral_site`. The V2 re-ingest closes that gap for newly appended rows;
   replay refuses legacy rows with `NHL_SOURCE_PROVENANCE_UNVERIFIED` rather
   than inferring their provenance. Existing historical rows remain legacy and
   are not rewritten.
6. No deployed Render SHA, live provider credential, Action readback, or
   independent SIRT review was available in this environment.

## 6. Verification performed

```text
.venv/bin/python -m pytest -q \
  tests/test_*nhl*.py tests/test_*cross_sport*.py \
  tests/test_*team_event*.py tests/test_*candidate*.py \
  test_v17_nhl_model_maintenance.py test_v17_d1_candidate_registry.py \
  test_v17_model_source_entitlements.py
664 passed  (29 files)
```

The new suite proves: the real registry artifact checksum is reproduced; a
tampered artifact and drifted schema are rejected; chronological replay and
minimum-prior-game/invalid-target/duplicate-identity holds; confirmed-fresh
goalie requirement; registry absence; caller calibration cannot override the
registry; identity calibration withholds bounds; empirical bounds contain the
point; the batch boundary admits a non-publishable candidate receipt; full club
names resolve to the selected side while an unrelated club holds; and the bridge
preserves `MODEL_INPUTS_INSUFFICIENT` rather than collapsing it.

The re-ingest suite additionally proves: the official 2026-27 schedule is
acquired and validated before writing; 32 genuine settled games are appended
with V2 `game_type`/`neutral_site`/`game_state`/`provenance_version` and ingest
SHA; repeated runs are idempotent and never update/delete; dry-run writes
nothing; partial provenance is rejected; non-regular-season games are rejected;
the legacy raw-hash collision is avoided by the envelope hash and the raw
commitment is retained; an exact synthetic collision fails closed without an
overwrite; a partial batch write retries to one row per event; concurrent
attempts converge; tampered scores or identities are rejected by envelope
recomputation; invalid neutral-site evidence is rejected; and the internal
route rejects anonymous requests without touching the database. A later official
read that reports a corrected score or start time is blocked before any write
(dry-run included) with `NHL_SOURCE_IDENTITY_CONFLICT` and
`provenance_complete=false` rather than appending a second immutable identity
that the reader would reject for the whole season; an already-conflicted season
can never be reported complete, and inexact season evidence is rejected instead
of truncated. Post-write verification re-checks the same one-identity invariant,
so a concurrent writer or a correction that surfaces mid-run is reported as
`NHL_SOURCE_IDENTITY_CONFLICT` at `SOURCE_VERIFICATION` rather than a false
completion, and an unexpected preflight read failure is a typed retryable block.
The persisted
genuine timeline replays target `2026020257` (PIT 2 – BOS 6) with exact
training-pipeline feature parity (15 home / 16 away prior games), then scores
with the governed artifact while remaining non-publishable.

Routing and input-boundary regressions prove: NHL dispatches through the shared
governed request wrapper (`BRIDGE_SCORERS["NHL"]`), so the route contract, the
mandatory Scout/Research barrier, `reduce_multisport_team_event` and the
official-publication guard all run, while the fitted specialist remains the raw
`MODEL_SCORERS["NHL"]` scorer. A malformed `startTimeUTC` is a typed
`NHL_SOURCE_EVENT_TIME_INVALID` block under strict re-ingest acquisition and a
dropped row on the non-strict training path. Re-ingesting the same settled game
first as `FINAL` and then as `OFF` keeps exactly one provenance row and never
rewrites the persisted evidence.

## 7. Acceptance status

| Requirement | Status |
| --- | --- |
| Existing settlement / selected-team / rank-admission fixes | Restored and verified |
| Official NHL event identity | Enforced (`\d{10}`, club alias resolution) |
| Schedules / game status / goalie / rest / injuries | Typed holds; no live 2026-27 settled timeline |
| Fitted NHL model restored | Yes — exact governed candidate, server-resolved |
| Calibrator restored | Yes — lifecycle-selected identity calibrator |
| Reproducible training / holdout / provenance | Registry + code SHA + dataset hash + partitions pinned |
| Append-only 2026-27 provenance re-ingest | Implemented and proven locally; live validation append **not run** (no write credential) |
| Genuine current-season replay | Proven on official 2026-27 fixture with exact feature parity |
| Calibrated lower bound | **Not available** for identity calibrator |
| Immutable receipt, `can_execute=false` | Yes, for candidate-grade packages |
| Production receipt / deployed SHA | **Not earned** |
| Independent review | **Not performed**; required before protected merge |

**Conclusion:** runtime readiness is improved and the fitted specialist is
wired end to end, but NHL certification remains on hold. No probability may be
published and no event may be ranked as an official winner until an NHL
certification receipt, a bound-producing calibrator, a complete 2026-27
settled timeline, and independent review all exist.
