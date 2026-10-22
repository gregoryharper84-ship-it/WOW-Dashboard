# Forward feature hydration, NCAAF / NHL — October 22, 2026

## NHL: prospective candidate vector reconstruction

`nhl_forward_candidate_feature_hydrator.py` reads the 12 first-party NHL canonical event crosswalk rows frozen on research PR #1975. It retrieves the entire 2026–27 regular-season club-schedule history from 32 NHL clubs, stores raw JSON and HTTP clock/hash evidence, verifies that each settled historical game appears identically in *both* clubs' snapshots, and reconstructs only the **ten existing `nhl_candidate_pipeline.FEATURE_NAMES` inputs**. It uses the existing candidate transform's season reset, starting Elo 1500, K=20, recent-10 team outcomes, rest cap at 14 days, >5 prior settled games, and neutral-site semantics. The minimum prior-games criterion is at least five, not more than five.

It rejects missing/changed canonical IDs, mismatching club schedule identities, invalid scores, conflicting duplicates, incomplete club universe, insufficient sample, missing source time, time skew, and any final game within eight hours of feature observation (to avoid claiming a recent result was independently settled by the pregame cutoff). Timestamp checks are **not** independent cryptographic attestation; absence of such attestation is retained. Original raw schedule bytes are stored as a GitHub artifact, not written to Supabase.

`model_artifact_id=None`, `goaltender_status_attested=false`, `calibration_certified=false`, `probability_publishable=false`, `can_execute=false` are invariant, even when all ten vector inputs are reconstructed. The NHL fitted candidate remains research-only. No sportsbook-implied probabilities or general Elo ranking is published as governed probability.

The dedicated workflow `.github/workflows/wow-research-nhl-forward-hydration-once.yml` runs a **single bounded** GitHub Actions job only on an exact-branch PR synchronization of this workflow file, retaining captured raw bytes and typed failure receipts for 30 days.

## NCAAF: point-in-time input source gap

Read-only inspection of **validation** project `wow-engine-validation` as of this recovery: 44/44 Oct 24 ESPN IDs had no NCAAF feature snapshots (0 snapshots over Oct 24–25), 0 `wow_ncaaf_pregame_evidence` rows for Oct 24–25, 0 fitted model artifact rows. The validation project nevertheless has 72 NCAAF season-2026 source-snapshot records and 514 training-game records; neither the source aggregates nor settled training-game rows prove exact **future** QB/injury, power, EPA, weather, rest and team evidence for those 44 events. Do not invent, default or replace features with market inputs. Each captured NCAAF game is `MODEL_INPUTS_INSUFFICIENT` until its full 18-feature snapshot is compiled and checked against exact event/team/timestamp.

The existing `ncaaf_feature_compiler` provides required per-scope evidence fields and strict point-in-time checks; `ncaaf_feature_transform.model_features_from_snapshot` and `ncaaf_trainer.FEATURES` are the only fitted 18-value contract. Acquisition of the missing independent input families and active fitted-artifact/calibrator certifications remains AMRT's actual restoration task.

### Execution boundaries

Neither workflow touches the protected main branch, the existing scoring service, Render deploys, Supabase writes, betting actions, or publication. Research PR remains Draft. `can_execute=false`.