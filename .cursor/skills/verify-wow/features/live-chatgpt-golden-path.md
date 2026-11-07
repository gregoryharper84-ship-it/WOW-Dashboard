# Live ChatGPT golden journey

## Sub-features

Production WOW_BETTING_ENGINE host/editor, inventory, board ingestion, scoreWowPickRequest, fitted scoring, V17 terminal output, and visible ChatGPT response.

## How to get to it (user POV)

Open the real WOW_BETTING_ENGINE Custom GPT and enter the canonical prompt from artifacts/wow-engine/V17_USER_JOURNEY_HEALTH.md:

> Use full model and provide me the best props across all sports.

## Driving it with live host and existing evaluator

Capture the actual host prompt, editor identity, invocation metadata, redacted Action response, source row counts, exact player/event/line identities, model/terminal receipts and rendered answer. Reconcile all candidates once. Validate the receipt's field-level structure:

~~~bash
python3 artifacts/wow-engine/v17/engineering_effectiveness.py journey /path/to/actual-host-receipt.json
~~~

The evaluator checks assertions in supplied JSON; independently match claims to real host/Action/terminal and persistence records before concluding user-journey PASS.

## Gotchas

Repository CI, HTTP health and backend scoring invoked outside ChatGPT cannot prove this user journey. Missing live editor or Action access is a specific verification prerequisite, not permission to fabricate a PASS. Keep historical evidence separate from current production. can_execute=false.
