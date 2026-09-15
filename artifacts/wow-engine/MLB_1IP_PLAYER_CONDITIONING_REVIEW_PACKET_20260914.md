# MLB 1IP player-conditioning review packet — 2026-09-14

Status: **REVIEW REQUIRED / NOT SERVING**

## Defect being repaired

The active `MLB_1IP_CONDITIONAL_TOTAL_PITCH_PMF_V1` scorer mixes BF-conditional pitch-count distributions with one league-wide BF weight vector. Because the live scorer receives only the artifact, exact line, and direction for the numeric PMF calculation, every pitcher at the same exact line/direction receives the same probability.

That behavior is acceptable for a league baseline but is not sufficient for player-level ranking.

## Rejected shortcut

A direct exact-line recent-hit-rate shrinkage challenger was rerun with an explicit same-line discrimination audit. It preserved calibration but did **not** produce reliable overall discrimination:

- aggregate within-line AUC: `0.500000`
- recent-hit shrinkage within-line AUC: `0.502250`
- AUC gain: `0.002250`
- discrimination gate: **FAIL**

This shortcut is therefore not the proposed fix.

Immutable workflow evidence:

- workflow run: `34875490855`
- artifact: `mlb-1ip-shadow-research-6960cfc5cfd57b8374a027b48ecb13a13a1474b9`
- artifact digest: `sha256:79626edc039007c639a744981de9c12964b545bb7e45c4fbfe498f2d72852a83`

## Candidate fix

Candidate family:

`MLB_1IP_PLAYER_CONDITIONED_BF_MIXTURE_V1`

The candidate preserves the active aggregate artifact's BF-conditional total-pitch distributions and changes only the mixture weights. The weights come from the already-developed `MLB_1IP_BF_DIRICHLET_SHRINKAGE_V1` model:

- pitcher recent first-inning BF history: maximum 10 starts
- league BF prior
- Dirichlet prior strength / alpha: `50.0`
- posterior categories: BF=3, BF=4, BF>=5

The live acquisition layer now preserves exact recent first-inning BF totals and the resolved pitcher ID so the same player-conditioned state is available at scoring time.

## BF component historical validation

The BF component's leakage-safe historical run used 2024 development and untouched chronological 2025 validation.

- 2024 training rows: `1308`
- 2025 validation rows: `1331`
- selected alpha: `50.0`
- historical validation: **PASS**
- blockers: none

On 2025:

- multiclass Brier: baseline `0.665824` → candidate `0.664152`
- multiclass log loss: baseline `1.097357` → candidate `1.094837`
- BF>=4 Brier: baseline `0.220581` → candidate `0.219981`
- BF>=5 Brier: baseline `0.229529` → candidate `0.228485`

Immutable workflow evidence:

- workflow run: `34873894267`
- artifact digest: `sha256:a4fea431f73a74f2f1b1aefe882c404475a004e5f2fc6d8c5d018f2d595fca1e`

## New disjoint 2026 test

Because the composite model was designed after inspecting the earlier 2025 evidence, 2025 is not reused as an untouched final test. The new candidate was evaluated on settled 2026 games through 2026-09-13 with a chronological warm-up/test split and no 2026 parameter tuning.

- warm-up games: `400`
- test games: `700`
- mature serving-cohort observations per exact line: `977`
- total mature line assignments: `5862`
- source gap rate: `0.0`
- validation result: **PASS**
- validation failures: none

### Exact-line results

| Line | Aggregate AUC | Player AUC | AUC gain | Aggregate Brier | Player Brier | Player ECE | Same-line probability std |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 11.5 | 0.5000 | **0.5311** | **+0.0311** | 0.130220 | **0.129926** | 0.012287 | 0.008205 |
| 13.5 | 0.5000 | **0.5250** | **+0.0250** | 0.211581 | **0.211276** | 0.004958 | 0.011941 |
| 15.5 | 0.5000 | **0.5210** | **+0.0210** | 0.248243 | **0.247650** | 0.018615 | 0.015447 |
| 17.5 | 0.5000 | **0.5282** | **+0.0282** | 0.242345 | **0.241663** | 0.008870 | 0.016137 |
| 19.5 | 0.5000 | **0.5271** | **+0.0271** | 0.217205 | **0.216591** | 0.028087 | 0.014567 |
| 21.5 | 0.5000 | **0.5107** | **+0.0107** | 0.179201 | **0.179150** | 0.015222 | 0.012195 |

The player-conditioned candidate improved Brier score on all six exact certified lines and created positive same-line discrimination on all six lines. The two lines present on the supplied board that are actually certified, 11.5 and 15.5, both passed the predeclared Brier/ECE/AUC/variance gates.

Immutable workflow evidence:

- workflow run: `34876306729`
- validation artifact: `mlb-1ip-player-conditioning-2026-11fb66de520345f70a4230391a77eea4a99eb3fe`
- artifact digest: `sha256:8c4a6f3fb216c9d3bd4abf85edfb75620b6541da3379e80fc89fb5345cb90474`

## Current-board research rerun

The supplied Sep. 14 PrizePicks board was rerun with official MLB live hydration and the candidate mixture. The same-line 15.5 rows no longer collapse to one value. Unsupported 12.5/14.5/16.5 lines remain OOD; thin-history rows remain input-insufficient.

Immutable workflow evidence:

- workflow run: `34876502128`
- artifact: `mlb-1ip-current-rerun-0c6a4001d14cfc97bf1817a70cf129ce50a67e4b`
- artifact digest: `sha256:615c0a8fbc7f8057ad669be65a23d448e84c639eb5325806fa244909f64ef8d0`

## Required review decision

Return one of:

- `APPROVE_FOR_FORMAL_CANDIDATE_PROMOTION`
- `HOLD_WITH_FINDINGS`
- `REJECT`

Review must confirm:

1. the 2026 test was not used for parameter tuning;
2. the aggregate conditional-pitch artifact remains unchanged;
3. BF posterior weighting is the only numerical player-conditioning change;
4. exact certified line support remains unchanged;
5. thin player history still fails closed;
6. unsupported lines still fail OOD;
7. player-conditioned probabilities are not yet relabeled as governed production probabilities before promotion;
8. `probability_publishable=false` and `can_execute=false` remain invariant until the separate governed promotion step.
