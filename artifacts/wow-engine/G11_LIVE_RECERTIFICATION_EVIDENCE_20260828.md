# G11 Live Recertification Evidence — 2026-08-28

Purpose: trigger the repository's existing PR verification workflows against the exact currently deployed WOW engine code revision without changing production behavior.

## Verified production revision

- GitHub main before this certification branch: `9de577b235c4bdd87ded27939887a7b58f416f33`
- Render service: `wow-governed-probability-engine`
- Render live deploy commit at evidence capture: `9de577b235c4bdd87ded27939887a7b58f416f33`
- Runtime start command: `uvicorn api_g11:app --host 0.0.0.0 --port $PORT`

## Live G11 evidence observed in Render logs

`WOW_G11_SELF_ACCEPTANCE result=PASS status=200 code=REAL_FITTED_MODEL_PATH_PROVEN event_id=822691 scoring_evidence_produced=true probability_fields_withheld=true probability_publishable=false can_execute=false leaked_probability_fields=0`

The self-acceptance probe exercises the deployed service's HTTP authentication boundary and real server-owned MLB fitted-model bridge. It explicitly requires zero numeric probability fields in the held response and preserves `probability_publishable=false` and `can_execute=false`.

## Governance boundary

This evidence note does not promote any deployment gate, change calibration status, publish a probability, or alter `can_execute=false`. Gate status remains governed by the live Supabase ledger and must only be updated after CI and live evidence are jointly reviewed.

## Certification method

This branch is created directly from the live/main SHA and changes only this documentation file. A green PR run therefore validates the same application code currently deployed, plus this non-executable evidence note, while leaving `main` and Render untouched and SHA-aligned during certification.
