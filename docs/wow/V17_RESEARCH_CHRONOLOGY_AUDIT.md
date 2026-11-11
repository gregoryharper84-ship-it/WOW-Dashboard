# V17 candidate corpus chronology preflight (research only)

`artifacts/wow-engine/v17/research_chronology_audit.py` checks **local actual bytes** against source and feature SHA-256 digests, validates timezone-aware publication/capture/freeze/kickoff/settlement chronology, rejects duplicate theses and events appearing in multiple splits, and checks training/calibration/holdout separation including label-availability cutoffs. It uses the Python standard library, performs no network or database writes and does not install another service.

Example:

```bash
python artifacts/wow-engine/v17/research_chronology_audit.py \
  /path/to/candidate_rows.jsonl --source-root /path/to/immutable/archive
```

Every JSONL row must contain: `row_id`, `sport`, `model_family`, `market_family`, `official_event_id`, `thesis_key` (exact side/period/line or equivalent identity), `split` (`TRAIN`, `CALIBRATION`, `HOLDOUT`), `source_snapshot_id`, `source_file`, `source_payload_sha256`, `feature_file`, `feature_snapshot_sha256`, `source_published_at`, `source_captured_at`, `feature_frozen_at`, `event_start_at`, `label_available_at`. Times must be timezone-aware ISO 8601; paths are relative to `--source-root`; hashes are the actual file bytes' lowercase SHA-256.

Exit 0 means local **research audit pass** only; exit 2 means held. A pass cannot prove the supplied timestamps were independently attested, the source was legally available for training, or the event ID was official. Both file contents and their claimed timestamps still require external source provenance. The report cannot certify a fitted artifact, calibrator, calibrated lower bound, live Action, or terminal receipt; `probability_publishable=false` and `can_execute=false` are fixed.

Tests:

```bash
python -m pytest -q artifacts/wow-engine/tests/test_v17_research_chronology_audit.py
```

Scope is intentionally separate from PR #2537's production reader, MLB evidence migrations and serving enforcement. Engineering must use independent certification and production-receipt gates after this offline preflight.

## Snapshot-ID integrity hardening (2026-11-11)

The offline audit now rejects `SOURCE_SNAPSHOT_ID_CONFLICT` whenever one `source_snapshot_id` is reused with different purported source bytes, sport, publication time, or capture time. A stable identical snapshot can be reused for later events when all source identity fields agree and every row's chronology passes. This adds a research-provenance preflight, **not** new authority: the audit still cannot attest the publisher's actual historical availability, source rights, canonical official IDs, calibration, or model certification. No collector, source entitlement, model, production route, or execution permission changes.

Run: `python -m pytest -q artifacts/wow-engine/tests/test_v17_research_chronology_audit.py`. All regressions require exact-head CI and independent acceptance before any production claims.
