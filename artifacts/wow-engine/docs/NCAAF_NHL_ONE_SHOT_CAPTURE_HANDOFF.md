# NCAAF + NHL ESPN forward capture — 2026-10-23 / 2026-10-24

Research-only: no betting or order execution, no model probability publication, no database writes, no Render deployment or scheduled jobs.

## One-shot operator commands

Run from an **authorized Render shell** in `artifacts/wow-engine`, after independently copying the exact reviewed script from this branch into `/tmp/wow_ncaaf_nhl_one_shot_capture.py` and checking its Git blob hash. The Render production main checkout **does not automatically contain this draft PR**.

For NCAAF's upcoming Saturday slate:

```bash
python3 /tmp/wow_ncaaf_nhl_one_shot_capture.py capture --sport NCAAF --date 2026-10-24 --outdir /tmp/wow-ncaaf-one-shot-20261024
python3 /tmp/wow_ncaaf_nhl_one_shot_capture.py verify --sport NCAAF --date 2026-10-24 --outdir /tmp/wow-ncaaf-one-shot-20261024
```

For NHL games on October 23:

```bash
python3 /tmp/wow_ncaaf_nhl_one_shot_capture.py capture --sport NHL --date 2026-10-23 --outdir /tmp/wow-nhl-one-shot-20261023
python3 /tmp/wow_ncaaf_nhl_one_shot_capture.py verify --sport NHL --date 2026-10-23 --outdir /tmp/wow-nhl-one-shot-20261023
```

The NCAAF endpoint uses ESPN group `80` and `limit=500`: **do not claim full NCAAF coverage**. Both sports record `full_slate_completeness_attested=false`. Verified SHA-256 and replay establish local byte integrity, **not** independent time attestation. A capture with zero pregame-eligible events is still a source receipt, not evidence of completed forward selection. Archive the two raw JSON files and two receipts from their `/tmp` directories outside the ephemeral Render container; separately re-hash before invoking downstream processes.

## Review notes

- Mocked local tests: **18 passing**, author-run, not independent CI approval.
- Source file uses fixed, allowlisted ESPN HTTPS paths; rejects redirects, malformed/oversized payload, stale/missing HTTP Date, duplicate game identity and missing home/away identities.
- Current downstream fitted sport-specific specialist feature semantics and numeric packages are **not certified by this raw-source capture**.
- All model output, calibrated bounds, market publication, betting and orders remain disabled. `can_execute=false`.
