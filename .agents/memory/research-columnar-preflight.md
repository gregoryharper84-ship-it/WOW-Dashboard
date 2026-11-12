# V17 optional columnar research preflight

- date: 2026-11-12
- typed_reason: COLUMNAR_INTEGRITY_HOLD
- mechanism: Chronologically sorted outcomes do not prove point-in-time feature availability, unique observation identity, or immutable source bytes. A fast Parquet scan could quietly fit on changed sources or future-captured features/market quotes. DuckDB was already declared research-only but absent from the runtime and had no validated frozen-source scan.
- detection_point: Before considering a faster multi-sport fit on historical data, at source snapshot preflight and corpus-integrity validation.
- fix: Added an optional, read-only, hash-pinned Parquet audit with sport/season identity, observation uniqueness, event-start and available-at epoch checks. Hash-before/after alone can miss source A → B → A mutation; scan only private verified bytes, rehash snapshot and source afterward, forbid glob metacharacters, and require a non-null quote time when is_market_observation=true. A PASS is strictly data-preflight-only. Every Parquet file now independently proves all required columns before by-name union, and a vanished source root produces typed HOLD.
- prevention_artifact: artifacts/wow-engine/tests/test_research_columnar_preflight.py: future-feature red-green, A→B→A pinned copy, market quote timing, glob rejection, multi-file missing-column and vanished-root negative cases.
- link: docs/wow/engineering/2026-11-12-columnar-preflight-research.md
- verification: Previous head 15 synthetic/local tests passed, 2 optional real-DuckDB tests skipped; new immutable snapshot and market-flag tests added, fresh exact-head hosted verification pending; approved real corpus and production readback unrun; 0 qualified/published model delta.
- execution: can_execute=false, no model fit, no certification or publication.
