# Optional columnar research preflight — 2026-11-12

**Scope:** R-class, offline/read-only data-integrity preflight, not new modeling, calibration or deployment infrastructure. No new production requirement. DuckDB was already declared in artifacts/wow-engine/requirements-research.txt. No production dependency change is made. The open cross-sport replay/certification PR #2592 and NFL five-count-stat candidate PR #2605 remain the owning work; this is not an alternative replay, source entitlement registry, or probability service.

## Mechanism / prediction

A naive multi-sport Parquet training script can accept duplicate observations, changed bytes, wrong sport/season partitions, or features/quotes captured after the game started. This predicts artificially improved holdout scores and invalid apparent acceleration even with chronological row sorting. A source-hash and event-time preflight before optional DuckDB scans rejects those inputs instead of quietly fitting them.

## Research-only usage

Install only the existing optional research requirements in an appropriate environment with license-approved, frozen source files. Never enable production training from this preflight.

Example manifest (frozen_manifest.json), with SHA replaced by actual full SHA-256 of an owned/approved Parquet file:

~~~json
{
  "schema": "WOW_V17_COLUMNAR_PREFLIGHT_V1",
  "can_execute": false,
  "dataset_id": "nfl_2025_frozen",
  "sport": "NFL",
  "season": 2025,
  "files": [{"path": "NFL/2025/rows.parquet", "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]
}
~~~

Required Parquet columns: sport, season, observation_id, event_id, event_start_epoch, feature_available_epoch, quote_captured_epoch. The last three are UTC epoch seconds, not ambiguous local dates; quote_captured_epoch may be NULL for nonmarket rows. observation_id is unique across the input corpus (player/event/stat rows need separate IDs). Files must be regular, under the supplied research root, and pinned by their actual contents. Manifests may not claim fitting or training authority.

~~~bash
python -m pip install -r artifacts/wow-engine/requirements-research.txt
python artifacts/wow-engine/v17/research_columnar_preflight.py \
  --manifest frozen_manifest.json --root /path/to/approved-frozen-corpus
python -m pytest -q artifacts/wow-engine/tests/test_research_columnar_preflight.py
~~~

A pass returns DATA_PREFLIGHT_PASS_ONLY, corpus counts, quote counts, manifest digest and DuckDB scan milliseconds. These measurements are **not a speedup claim** until a like-for-like pandas/current-runner baseline is timed against real rows. An invalid manifest, source hash, file path, missing DuckDB dependency or future-dated data gives a typed HOLD (nonzero CLI exit). All outputs unconditionally report training_authorized=false, model_fitted=false, probability_publishable=false, can_execute=false.

## Verification boundary

- Local synthetic tests: **14 passed, 2 conditional real-DuckDB integration tests skipped** (Python 3.13 environment, 2026-11-12).
- Deliberately suppressed the features_after_start rejection: corresponding regression RED (**1 failed, 6 passed**), then restored the guard and reran GREEN (**14 passed**). Test remains permanently in the suite.
- Real DuckDB + real Parquet execution: **unrun**; dedicated two-case integration test will run when research DuckDB is installed (DuckDB and approved corpus unavailable in this environment).
- Production SHA/row persistence: **unrun**, not applicable to this research-only change.
- Scoreboard delta: **0** model-evaluated, **0** qualified, **0** production SHA changes established from this research change.

## Follow-through

1. Complete #2605 pinned real-source reconciliation and rights review; this preflight is not a substitute.
2. Once actual Parquet materializations exist, run against frozen approved corpora; retain emitted hash+latency receipt and compare with a same-host same-corpus baseline.
3. Only if measured bottlenecks justify it, propose a separately governed fitter optimization; keep specialist-specific features and calibration semantics, prospective evidence, existing certification path, and V17 terminal reducer unchanged.
