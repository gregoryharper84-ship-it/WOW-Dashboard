# V17 Cross-Sport Repair Acceptance

The repair is complete only when all of the following are true:

1. All 12 cataloged sports appear in team/event parity health with the same orchestration/status shape.
2. All 12 cataloged sports appear in prop parity health with the same accounting shape; existing prop-artifact `GOLF` is mapped to canonical `PGA` only for parity/reporting and is not silently renamed in fitted artifacts.
3. Discovery never filters a sport merely because its model is unavailable.
4. Registered team/event sports receive explicit sporting evidence from discovery when present; sportsbook odds/implied probability are never converted into model inputs.
5. Missing exact fitted models remain `MODEL_UNAVAILABLE`; missing evidence/calibration for an available model remains `MODEL_INPUTS_INSUFFICIENT`.
6. MLB provider IDs resolve only through bounded participant/start/slate canonicalization, including safe handling of city-only aliases; no ambiguous identity guessing.
7. Evidence fields proven upstream but reported missing downstream produce `RUN_INVALID_EVIDENCE_BINDING`, not an ordinary no-pick.
8. A nonzero Daily PROPS run forces the governed current producer once, but the full valid requested-slate canonical manifest remains eligible for scoring. Current-run receipt-backed rows and preexisting same-slate canonical rows are counted separately so NFL/WNBA/user-board rows are never discarded or falsely attributed to the MLB autonomous producer.
9. Prop parity explicitly distinguishes autonomous candidate discovery from row-triggered automatic hydration. MLB currently has Daily autonomous discovery; MLB/NFL/WNBA have reviewed row hydration; sports without a certified producer remain visible with their declared lane status. Zero candidates is not capability proof.
10. Provider quota exhaustion is circuit-broken within a scan and remains an acquisition blocker rather than a model blocker.
11. Governance diagnostics are sport-scoped and row publication remains owned by the governed package + `V17_TERMINAL_REDUCER`; no static global publication hold may suppress healthy sport routes.
12. Engine-to-proxy acquisition can use a separate server-only read-only bearer; existing Action/OIDC auth remains intact.
13. `can_execute=false` everywhere.
14. Prop calibration shrinkage and the 65% calibrated lower-bound qualification floor are unchanged by this orchestration repair.
