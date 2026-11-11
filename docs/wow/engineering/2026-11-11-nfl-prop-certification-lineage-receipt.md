# NFL prop certification lineage receipt — 2026-11-11

`can_execute=false`. Lineage evidence only; no promotion, calibration approval, deployment or publication authority.

## Conclusion
- Original training commit `baa23d502be843c5ba3ee44525be277b237bc37c` (run 35514457169) is **recoverable via GitHub**, so `ORIGINAL_TRAINING_CODE_UNRECOVERABLE` does not apply. It is orphaned from repository refs; retention depends on GitHub. Preserving it (e.g. a tag) is a repo write left to the owner.
- Trainer bytes on `main` (sha256 `b0b4db8a…d9`) equal the trainer at that commit.
- Re-running the trainer on the five pinned nflverse CSVs (hashes re-verified) with the original package pins reproduced both frozen files byte-for-byte (artifacts `ed3cc55f…f9d5`, report `d45b9bb6…9ae5`), all four lanes.

## Five separate assertions (see receipt JSON)
1. Source identity: verified externally, not in repo refs.
2. Internal checksum consistency: verified (recomputed in tests).
3. Statistical reproduction: exact — but by re-executing the same trainer, not an independent reimplementation.
4. Artifact-byte reproduction: exact, same caveat.
5. Production registration: registry rows match on checksum/hash/code SHA/rows in the **validation** Supabase project only; payload bytes and production project identity unverified.

Registry lifecycle is PROSPECTIVE_CERTIFIED with `NFL_PROP_PRECALIBRATION_BOOTSTRAP_V1`; `probability_publishable=false`.

Files: `scripts/verify_nfl_prop_lineage.py`, `data/wow_nfl_prop_lineage_{evidence_2026-11-11,receipt_v1}.json`, `test_v17_nfl_prop_lineage_receipt.py`.
