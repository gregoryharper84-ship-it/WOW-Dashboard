# V17 Cross-Sport Repair Acceptance

The repair is complete only when all of the following are true:

1. All 12 cataloged sports appear in parity health with the same orchestration/status shape.
2. Discovery never filters a sport merely because its model is unavailable.
3. Registered sports receive explicit sporting evidence from discovery when present; sportsbook odds/implied probability are never converted into model inputs.
4. Missing exact fitted models remain `MODEL_UNAVAILABLE`; missing evidence/calibration for an available model remains `MODEL_INPUTS_INSUFFICIENT`.
5. MLB provider IDs resolve only through bounded participant/start/slate canonicalization, including safe handling of city-only aliases; no ambiguous identity guessing.
6. Evidence fields proven upstream but reported missing downstream produce `RUN_INVALID_EVIDENCE_BINDING`, not an ordinary no-pick.
7. Daily prop scoring uses current-run receipt-backed acquisition rows and cannot silently substitute historical persisted snapshots.
8. Provider quota exhaustion is circuit-broken within a scan and remains an acquisition blocker rather than a model blocker.
9. Governance diagnostics are sport-scoped and row publication remains owned by the governed package + `V17_TERMINAL_REDUCER`; no static global publication hold may suppress healthy sport routes.
10. Engine-to-proxy acquisition can use a separate server-only read-only bearer; existing Action/OIDC auth remains intact.
11. `can_execute=false` everywhere.
12. Prop calibration shrinkage and the 65% calibrated lower-bound qualification floor are unchanged by this orchestration repair.
