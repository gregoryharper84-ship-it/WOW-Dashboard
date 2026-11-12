# CUR-DELIV-20261112 — sport × market × route inventory

**As-of:** 2026-11-12  
**LIVE web+worker SHA:** `1ab243ca31827e08d501ce177237ce996eeda051`  
**Source:** `/health` team_event_bridges + static `CERTIFIED_TEAM_EVENT_SPORTS` + open restoration PRs  
**Safety:** `can_execute=false`; no fabricated probabilities

## Team / event winner routes (`/score-team-event`, `/score-team-event-request`)

| Sport | LIVE status | certification_status | artifact | scorer | capability_ready | Cursor action |
| --- | --- | --- | --- | --- | --- | --- |
| MLB | UP | CERTIFIED | true | true | true | Canary with auth (credential BLOCKED in this agent) |
| NFL | UP | CANDIDATE_REGISTERED_UNCERTIFIED | true | true | true | Runtime champion path; do not expand static CERTIFIED without Model Excellence |
| NHL | UP | CANDIDATE_REGISTERED_UNCERTIFIED | false | true | false | Cursor adoption #2637 of #2571 onto current main (local 241 tests pass); still not statically CERTIFIED; authorized merge + independent cert BLOCKED |
| WNBA | UP | CANDIDATE_REGISTERED_UNCERTIFIED | false | true | false | HOLD — Freebuff/Model Excellence |
| SOCCER | UP | CANDIDATE_REGISTERED_UNCERTIFIED | false | true | false | HOLD |
| TENNIS | UP | CANDIDATE_REGISTERED_UNCERTIFIED | false | true | false | HOLD |
| MMA | UP | CANDIDATE_REGISTERED_UNCERTIFIED | false | true | false | HOLD |
| NBA | MODEL_UNAVAILABLE | NOT_CERTIFIED | false | false | false | Existing PR #2560 (required regression FAIL) |
| NCAAF | MODEL_UNAVAILABLE | NOT_CERTIFIED | false | false | false | HOLD / research |
| NCAAB | MODEL_UNAVAILABLE | NOT_CERTIFIED | false | false | false | Event identity PR #2550 CONFLICTING |
| PGA | MODEL_UNAVAILABLE | NOT_CERTIFIED | false | false | false | HOLD |
| BOXING | MODEL_UNAVAILABLE | NOT_CERTIFIED | false | false | false | HOLD |
| CRICKET | MODEL_UNAVAILABLE | NOT_CERTIFIED | false | false | false | HOLD |

Static repository certification (`CERTIFIED_TEAM_EVENT_SPORTS`) remains **MLB only**. Declared specialist identities for WNBA/NHL/SOCCER/TENNIS/MMA are routing/development identities, not publication certification.

## Prop routes (`/score-prop`, `/score-pick-request`)

| Observation | Classification | Action |
| --- | --- | --- |
| Startup `/score-prop` 422 `PROP_EVIDENCE_SNAPSHOT_NOT_FOUND` for MORE+LESS | **Expected fixture HOLD** — probe uses `event_id=WOW:PROP:LIVE:SELF_ACCEPTANCE:NO_EVIDENCE` and a fresh random `source_snapshot_id`; validator requires 422 fail-closed without specialist invoke | Label logs `fixture=EXPECTED_NO_SNAPSHOT`; do not fabricate snapshots |
| Real fitted prop self-acceptance | Separate path `prop_live_model_acceptance.py` requiring configured snapshot / bootstrap | Needs production snapshot ID + Action key |
| Anonymous 401 `auth_scheme=NONE` | Expected auth fail-closed | Not a model outage |
| Prop evidence identity PR #2526 | Open; owner-authored; touches trust-root workflow | Coordinate; do not duplicate blindly |
| Freebuff offline #2605 | Offline fitting | Cursor must not touch |

## Actions / editor

| Surface | Status |
| --- | --- |
| OpenAPI source under `artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml` | Present in repo |
| Live Custom GPT editor sync | `LIVE_GPT_EDITOR_SYNC=UNVERIFIED` (no editor access in this agent) |
| Authenticated Action canary | `BLOCKED` — `WOW_ACTION_API_KEY` absent in agent environment |

## Immutable receipts / terminal authority

- Prediction immutability: governed `wow_predictions` / settlement via backend-derived hit/push (`api_prod_market_acceptance.settle_prop`)
- Terminal authority: `V17_TERMINAL_REDUCER` only
- Action invocation receipts: existing telemetry tables; historical count ≠ current success

## Competing owners (do not collide)

| PR | Scope | Notes |
| --- | --- | --- |
| #2526 | Prop evidence canonical identity | Trust-root workflow touch |
| #2560 | NBA restoration | Required regression currently FAIL |
| #2571 | NHL fitted specialist | App checks green but BEHIND main; superseded for Cursor path by #2637 |
| #2637 | Cursor NHL adoption of #2571 | Nonowner rebase onto `1ab243ca`; not yet certified publication |
| #2550 | NCAAB event identity | CONFLICTING with main |
| #2605 | Freebuff offline NFL props | Out of Cursor scope |

<!-- qa-incident-body-reentry 2026-11-12T17:07Z -->
