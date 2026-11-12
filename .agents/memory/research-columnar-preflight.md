# V17 optional columnar research preflight

- date: 2026-11-12
- typed_reason: COLUMNAR_INTEGRITY_HOLD
- mechanism: Chronologically sorted outcomes do not prove point-in-time feature availability, unique observation identity, or immutable source bytes. A fast Parquet scan could quietly fit on changed sources or future-captured features/market quotes. DuckDB was already declared research-only but absent from the runtime and had no validated frozen-source scan.
- detection_point: Before considering a faster multi-sport fit on historical data, at source snapshot preflight and corpus-integrity validation.
- fix: Added an optional, read-only, hash-pinned Parquet audit with sport/season identity, observation uniqueness, event-start and available-at epoch checks. Rehash inputs after scan to detect hash-check/scan TOCTOU mutation. A PASS is strictly data-preflight-only.
- prevention_artifact: artifacts/wow-engine/tests/test_research_columnar_preflight.py, including a deliberately red-then-green future-feature regression.
- link: docs/wow/engineering/2026-11-12-columnar-preflight-research.md
- verification: 15 synthetic/local tests passed, 2 optional real-DuckDB integration tests skipped; DuckDB + real Parquet and production verification unrun; 0 qualified/published model delta.
- execution: can_execute=false, no model fit, no certification or publication.
