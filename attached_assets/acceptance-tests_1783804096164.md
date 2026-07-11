# Acceptance Tests

1. Missing event date triggers slate purge before modeling.
2. Conflicting current and stale season averages produce source conflict and no READY label.
3. L10/L5 divergence >20% triggers one-game outlier isolation and L9 recomputation.
4. Role-dependent player uses matching role-split ledger.
5. Gate 3 coin-flip MORE evaluation automatically assesses LESS.
6. WNBA primary teammate OUT/GTD creates role-amplification flag.
7. User screenshot price remains `operator_supplied` and caps at WATCH.
8. Kalshi price age 11 minutes returns DATA_UNOBTAINABLE.
9. Empty Kalshi orderbook returns DATA_UNOBTAINABLE.
10. Kalshi market closed returns REJECT_BAD_RULES.
11. Kalshi sports `INVENTORY_EMPTY` causes immediate stop and no scan.
12. `can_execute` is false in every result.
13. Bare `LLP_PLAYABLE_LIMIT_ONLY` normalizes to `LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN`.
14. CHI NHIGH maps to KMDW, never KORD.
15. MIA NHIGH maps to KMIA, never PBI.
16. LA NHIGH maps to KLAX, never BUR.
17. Gaussian weather brackets normalize between 0.97 and 1.03.
18. Four-market Kalshi sports combo hard rejects during Reliability Freeze.
19. Duplicate same-event same-side entries count as one model/calibration observation.
20. Missing joint probability or combo breakeven returns COMBO_EV_UNOBTAINABLE/REJECT_BAD_STRUCTURE.
21. QA auditor recomputes edge and catches arithmetic mismatch.
22. Lowest-ceiling propagation prevents downstream READY from overriding upstream HOLD.
23. Bankroll manager returns no allocation when capital lane is blocked.
24. Sports psychology context cannot exceed low-weight adjustment cap or use unsupported mental-state claims.
25. Ref/umpire skill returns no adjustment when assignment is unconfirmed.
