# V17 model acceptance harness report

- Harness: `V17_MODEL_ACCEPTANCE_HARNESS_V1` · mode `FIXTURE` · evaluated `2026-10-21T15:00:00Z`
- Harness status: **HARNESS_FIXTURES_CONSISTENT**
- Production acceptance: **NOT_PERFORMED_REQUIRES_INDEPENDENT_SIRT_QA** (self_certified=False)
- dry_run_only=True · can_execute=False

## Totals

26 cases = 6 accepted + 5 held (typed) + 15 rejected · reconciled=True · money-audit failures=1

Coverage complete: True
Receipts: 20 (20 unique)

## Routes

| Case | Route | Probability | Money | First failing stage | Exact blocker | p / lower bound | Publishable | Receipt |
|---|---|---|---|---|---|---|---|---|
| nfl-ml-accept | NFL_MONEYLINE | PROBABILITY_ACCEPTED | MONEY_AUDIT_PASSED | — | — | 0.64 / 0.58 | True | RCPT-nfl-ml-accept |
| nfl-ml-1896-contradiction | NFL_MONEYLINE | REJECTED | MONEY_AUDIT_PASSED | PUBLICATION | PUBLISHABILITY_CONTRADICTION | 0.64 / 0.58 | True | RCPT-nfl-ml-1896-contradiction |
| nfl-prop-passing-accept | NFL_PROP | PROBABILITY_ACCEPTED | MONEY_AUDIT_PASSED | — | — | 0.64 / 0.58 | True | RCPT-nfl-prop-passing-accept |
| nfl-prop-rushing-accept | NFL_PROP | PROBABILITY_ACCEPTED | MONEY_AUDIT_PASSED | — | — | 0.64 / 0.58 | True | RCPT-nfl-prop-rushing-accept |
| nfl-prop-receiving-money-absent | NFL_PROP | PROBABILITY_ACCEPTED | MONEY_AUDIT_FAILED | MONEY_AUDIT | MONEY_EVIDENCE_ABSENT | 0.64 / 0.58 | True | RCPT-nfl-prop-receiving-money-absent |
| nfl-prop-1894-alias-unverified | NFL_PROP | REJECTED | MONEY_AUDIT_PASSED | IDENTITY | ESPN_ALIAS_UNVERIFIED | 0.64 / 0.58 | True | RCPT-nfl-prop-1894-alias-unverified |
| nfl-prop-threshold-weakened | NFL_PROP | REJECTED | MONEY_AUDIT_PASSED | CALIBRATION | PUBLICATION_THRESHOLD_WEAKENED | 0.57 / 0.5 | True | RCPT-nfl-prop-threshold-weakened |
| nfl-prop-malformed-output | NFL_PROP | REJECTED | MONEY_AUDIT_NOT_RUN | MODEL | MODEL_OUTPUT_INVALID | 0.61x / 0.58 | True | — |
| nba-points-200-model-unavailable | NBA_PROP | HELD_TYPED | MONEY_AUDIT_NOT_RUN | MODEL | MODEL_UNAVAILABLE | None / None | False | — |
| nba-pra-200-model-unavailable | NBA_PROP | HELD_TYPED | MONEY_AUDIT_NOT_RUN | MODEL | MODEL_UNAVAILABLE | None / None | False | — |
| nba-points-scorer-failed | NBA_PROP | HELD_TYPED | MONEY_AUDIT_NOT_RUN | MODEL | MODEL_SCORER_FAILED | None / None | False | — |
| nba-points-inputs-insufficient | NBA_PROP | HELD_TYPED | MONEY_AUDIT_NOT_RUN | MODEL | MODEL_INPUTS_INSUFFICIENT | None / None | False | — |
| nfl-spread-accept-integer-push | NFL_SPREAD | PROBABILITY_ACCEPTED | MONEY_AUDIT_PASSED | — | — | 0.64 / 0.58 | True | RCPT-nfl-spread-accept-integer-push |
| ncaaf-spread-accept | NCAAF_SPREAD | PROBABILITY_ACCEPTED | MONEY_AUDIT_PASSED | — | — | 0.64 / 0.58 | True | RCPT-ncaaf-spread-accept |
| ncaaf-1h-spread-unsupported-hold | NCAAF_SPREAD | HELD_TYPED | MONEY_AUDIT_NOT_RUN | ROUTE | UNSUPPORTED_ROUTE_HOLD | None / None | False | — |
| nfl-spread-borrowed-ml | NFL_SPREAD | REJECTED | MONEY_AUDIT_PASSED | MODEL | PROBABILITY_BORROWED | 0.64 / 0.58 | True | RCPT-nfl-spread-borrowed-ml |
| nfl-spread-unsigned-line | NFL_SPREAD | REJECTED | MONEY_AUDIT_PASSED | IDENTITY | SPREAD_LINE_UNSIGNED | 0.64 / 0.58 | True | RCPT-nfl-spread-unsigned-line |
| nfl-spread-push-rule-missing | NFL_SPREAD | REJECTED | MONEY_AUDIT_PASSED | CALIBRATION | PUSH_RULE_MISSING | 0.64 / 0.58 | True | RCPT-nfl-spread-push-rule-missing |
| nfl-ml-stale-receipt | NFL_MONEYLINE | REJECTED | MONEY_AUDIT_PASSED | RECEIPT | RECEIPT_STALE | 0.64 / 0.58 | True | RCPT-nfl-ml-stale-receipt |
| nfl-ml-foreign-receipt | NFL_MONEYLINE | REJECTED | MONEY_AUDIT_PASSED | RECEIPT | RECEIPT_FOREIGN | 0.64 / 0.58 | True | RCPT-nfl-ml-foreign-receipt |
| nfl-ml-run-sha-mismatch | NFL_MONEYLINE | REJECTED | MONEY_AUDIT_PASSED | ENVIRONMENT | RUN_SHA_MISMATCH | 0.64 / 0.58 | True | RCPT-nfl-ml-run-sha-mismatch |
| nfl-prop-identity-conflict | NFL_PROP | REJECTED | MONEY_AUDIT_PASSED | IDENTITY | IDENTITY_CONFLICT | 0.64 / 0.58 | True | RCPT-nfl-prop-identity-conflict |
| nfl-ml-live-unrecognized-event | NFL_MONEYLINE | REJECTED | MONEY_AUDIT_PASSED | LIFECYCLE | LIVE_EVENT_UNRECOGNIZED | 0.64 / 0.58 | True | RCPT-nfl-ml-live-unrecognized-event |
| nfl-ml-live-pregame-reuse | NFL_MONEYLINE | REJECTED | MONEY_AUDIT_PASSED | LIFECYCLE | PREGAME_REUSED_IN_LIVE | 0.64 / 0.58 | True | RCPT-nfl-ml-live-pregame-reuse |
| nfl-prop-pregame-event-started | NFL_PROP | REJECTED | MONEY_AUDIT_PASSED | LIFECYCLE | EVENT_STARTED_INVALIDATED | 0.64 / 0.58 | True | RCPT-nfl-prop-pregame-event-started |
| nfl-ml-row-terminated-twice | NFL_MONEYLINE | REJECTED | MONEY_AUDIT_PASSED | RECONCILIATION | RECONCILIATION_COUNT_MISMATCH | 0.64 / 0.58 | True | RCPT-nfl-ml-row-terminated-twice |

Fixture evidence only. Independent SIRT/QA must perform production acceptance after an authorized Render release; this report does not certify production.
