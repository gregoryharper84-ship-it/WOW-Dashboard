# MLB 1IP empirical model decision — 2026-09-01

Status: **RESEARCH SHADOW / NOT SERVING**

The temporal shadow compared the legacy Gaussian per-batter event tree, a league aggregate empirical conditional-total-pitches PMF, and a pitcher-shrunk empirical challenger. On the deterministic 2024 training / 2025 holdout packet, the aggregate empirical PMF produced the best measured combination of Brier score and ECE, while the pitcher-shrunk challenger also passed both gates but did not improve on the aggregate model.

Observed 2025 holdout metrics from the immutable shadow packet:

- current Gaussian event tree: Brier 0.2121846441, ECE 0.0524241875, gates passed
- pitcher-shrunk empirical: Brier 0.2095614407, ECE 0.0301755027, gates passed
- aggregate empirical conditional-total-pitches PMF: Brier 0.2067737412, ECE 0.0147573878, gates passed

Decision: advance **MLB_1IP_CONDITIONAL_TOTAL_PITCH_PMF_V1** as the preferred research candidate because it is both simpler and empirically superior on the untouched temporal holdout. Do not add extra pitcher-specific adjustment layers unless a future disjoint validation set shows a reproducible gain. This follows the accuracy-without-layer-saturation objective.

The formal candidate contract is implemented in `mlb_1ip_empirical_pmf.py`. It stores compact BF-conditional total-pitch counts and fitted BF weights, computes exact discrete MORE/LESS/push mass, is deterministic, and remains `probability_publishable=false` and `can_execute=false`.

Certification boundaries remain unchanged. The candidate must stay CANDIDATE/SHADOW until a lineage-bound candidate packet passes temporal validation and receives independent reviewer approval from a distinct context. No Supabase promotion, active artifact insertion, Render deployment, or V17 cutover is authorized by this document.
