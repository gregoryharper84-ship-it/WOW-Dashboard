# Canonical prop Action

## Sub-features

Exact event/player/stat/period/line/direction, authorized ingress, fitted specialist, calibrated lower bound, typed errors and exact-once row reconciliation.

## How to get to it (user POV)

A user supplies a PrizePicks board or exact props. WOW routes resolved pregame candidates to scoreWowPickRequest on POST /score-pick-request.

## Driving it with existing canonical Action workflow

First verify a genuine current pregame request file. An authorized workflow actor can then execute the existing GitHub Action, rather than using guessed credentials:

~~~bash
gh workflow run wow-v17-canonical-prop-action.yml --ref main -f request_file=PATH_TO_CURRENT_GOVERNED_REQUEST_JSON -f allow_historical_request=false
~~~

Only run with current verified inputs and established workflow authorization. Read its v17-canonical-prop-action-result artifact. Match prepared versus returned rows, unique row keys, terminal status and typed codes. If scored, compare exact identity, model artifact, calibration and lower bound. Offline adjacent regression:

~~~bash
python3 -m pytest -q artifacts/wow-engine/tests/test_v17_prop_canonical_identity.py artifacts/wow-engine/tests/test_v17_compact_receipt_contract.py
~~~

## Gotchas

The workflow alone cannot establish that live ChatGPT invoked its Action. A historical replay cannot establish current-slate availability. Never infer direction or interchange adjacent prop lines. No fake probability or terminal promotion. V17 terminal reducer remains controlling; can_execute=false.
