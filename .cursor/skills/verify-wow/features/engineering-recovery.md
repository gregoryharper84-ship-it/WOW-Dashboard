# Engineering recovery

## Sub-features

Original defect reproduction, narrowly scoped patch, exact-source regression, authorized writer lease, independent QA and release, Render web/worker SHA, governed Action/persistence receipts and user-visible restoration.

## How to get to it (user POV)

User reports that a WOW workflow fails. Engineering is expected to restore the actual user workflow and independently verify the repair.

## Driving it with existing AMRT/QA/Release evidence

Read the original incident and reproduce its failure from real evidence. Inspect patch SHA, exact-head test outcome, independent review, then authorized release evidence. Read-only correlate merged SHA to Render deployed web/worker revisions and same-run Action/model/persistence/terminal user-path receipts. Existing read-only evaluator: artifacts/wow-engine/v17/product_outcome_acceptance.py. Offline adjacent regression:

~~~bash
python3 -m pytest -q artifacts/wow-engine/tests/test_v17_user_journey_recovery_contract.py
~~~

## Gotchas

Do not dispatch another writer, clear a held Cursor lease, self-approve, merge, deploy or mint a new QA/Release authority. Source and green CI alone are not FIXED_AND_VERIFIED. Absent production receipts remain a precise evidence gap; they are not automatically MODEL_UNAVAILABLE. Respect current independent closure policies. can_execute=false.
