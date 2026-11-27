# 2026-11-17 NFL exact-version forward evidence consistency

- **Date:** 2026-11-17
- **Typed reason:** `NFL_FORWARD_HEALTH_COHORT_MISMATCH` / `NFL_EXACT_VERSION_COHORT_MISMATCH`
- **Mechanism:** A global NFL forward health PASS can coexist with a mixed model-version/artifact cohort or a different count from the intended verified champion; a passing global health row alone cannot establish exact-version evidence.
- **Detection point:** The independent scientific review before model certification/promotion, not after a model is published or a user-facing terminal receipt.
- **Fix (nonauthorizing diagnostic):** An independently implemented pure arithmetic replay on immutable verified grade inputs, binding model version, artifact ID/checksum, event/prediction uniqueness, pregame chronology, per-grade Brier/log-loss, ten-bin ECE, bias and lower-bound gap to the health row. It has no DB writes, no probability fitting, and always sets `probability_publishable=false`.
- **Prevention artifact:** `artifacts/wow-engine/tests/test_v17_nfl_exact_version_evidence_replay.py`; 8 local focused tests (red missing module, green after implementing).
- **Link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1896 (independent SIRT #1563).
- **Status:** MOVING: the tool verifies consistency of supplied evidence, not trusted production provenance or independent approval. No claim of FIXED_AND_VERIFIED.
