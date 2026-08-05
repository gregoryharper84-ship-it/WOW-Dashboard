# Regression Tests — wow-cross-sport-high-probability-selector

PATCH: WOW-PATCH-2026-08-05-CROSS-SPORT-HIGH-PROBABILITY-SELECTOR
STATUS: Proposed — tests are defined but not yet run against a live activation

1. A winning prior card cannot upgrade a current candidate.
2. Missing event identity blocks all lanes.
3. Missing participant status blocks current qualification.
4. Missing exact market blocks edge but not probability.
5. Same-game stacks are flagged.
6. Same injury thesis across legs is flagged.
7. Cross-book legs are not called one executable parlay.
8. Weakest-leg elimination runs before output.
9. Final refresh is mandatory.
10. Outcomes never overwrite predictions.
11. Human confirmation remains mandatory.
12. NO_PLAY is returned when nothing qualifies.
