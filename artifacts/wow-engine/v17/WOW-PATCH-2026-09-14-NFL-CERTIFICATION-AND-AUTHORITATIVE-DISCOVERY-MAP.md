# WOW-PATCH-2026-09-14-NFL-CERTIFICATION-AND-AUTHORITATIVE-DISCOVERY-MAP

PATCH_ID: WOW-PATCH-2026-09-14-NFL-CERTIFICATION-AND-AUTHORITATIVE-DISCOVERY-MAP  
PATCH_VERSION: V17.0  
EFFECTIVE_DATE: 2026-09-14  
APPLIES_TO: WOW Betting Engine host, LLP Team Betting Engine, cross-sport team/event discovery, team/event capability certification  
STATUS: READY_FOR_INTEGRATION  
can_execute=false  
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true

## 1. Purpose

Close the two remaining governance questions from the Claude Code cross-sport runtime build:

1. distinguish runtime bridge registration from sport-model certification;
2. replace guessed discovery keys with an authoritative current TheRundown sport-ID map where the provider exposes one.

This patch is additive over:
- August 2026 slate-integrity/calibration/final-refresh governance;
- September 2026 model-completion failure taxonomy;
- 2026-09-14 cross-sport full-slate discovery/model-coverage patch.

## 2. Certification decision: NFL

### Decision

Do **not** add NFL to `CERTIFIED_TEAM_EVENT_SPORTS` merely because the runtime bridge now exists.

After the Claude runtime branch is deployed, NFL may be reported as:

```text
registered_capability = true
runtime_bridge_status = UP
certification_status = CANDIDATE_REGISTERED_UNCERTIFIED
```

until the certification evidence gates below are satisfied.

Bridge registration proves routing/scoring capability. It does not by itself prove calibrated prospective production fitness.

### Why

Current live V17 governance distinguishes capability and certification.

The live MLB lane exposes a prospective certification record including:
- explicit provider/model identity;
- model artifact version;
- feature schema;
- minimum simulation contract;
- calibration health;
- prospective/forward-shadow evidence;
- terminal ceiling;
- `can_execute=false`.

No equivalent NFL certification package is currently present in live governance.

Therefore:

```text
REGISTERED != CERTIFIED
```

and:

```text
NFL bridge deployed
!=
NFL certified for official probability publication
```

## 3. NFL certification gates

NFL may enter `CERTIFIED_TEAM_EVENT_SPORTS` only after all applicable existing V17 certification policy requirements pass.

At minimum require evidence for:

```text
G-NFL-01 exact fitted NFL team/event model identity
G-NFL-02 model artifact/version pinned
G-NFL-03 feature/input schema version pinned
G-NFL-04 governed NFL input adapter and required-field contract
G-NFL-05 scorer resolvable in production
G-NFL-06 valid numeric probability package contract
G-NFL-07 dynamic calibration path present
G-NFL-08 calibrated lower/upper bounds validated
G-NFL-09 deterministic/reproducibility checks pass where required
G-NFL-10 failure-regime / unconditional-probability contract passes
G-NFL-11 prospective or forward-shadow validation sufficient under active certification policy
G-NFL-12 calibration-health assessment passes
G-NFL-13 probability claim auditor compatible
G-NFL-14 event decision governor compatible
G-NFL-15 event mutex/final refresh pass
G-NFL-16 live cross-sport smoke test passes after deployment
G-NFL-17 no market-probability or generic-reasoning substitution
G-NFL-18 can_execute=false
```

Do not invent numeric certification thresholds in this patch. Use the active repository/governance certification policy.

If certification evidence is incomplete:

```text
runtime bridge may remain available
official certification remains false
terminal ceiling remains whatever active V17 governance permits for uncertified model output
```

## 4. Health representation

Health should separate at least:

```yaml
NFL:
  registered_capability: true|false
  adapter_importable: true|false
  scorer_resolvable: true|false
  status: UP|DOWN|MODEL_UNAVAILABLE
  certification_status: CERTIFIED|CANDIDATE_REGISTERED_UNCERTIFIED|NOT_CERTIFIED
  certification_id: string|null
  can_execute: false
```

Do not infer certification from `status=UP`.

## 5. Authoritative TheRundown discovery IDs

Validated against the current TheRundown sport registry on 2026-09-14.

### Primary LLP discovery map

| LLP family | TheRundown sport ID | Provider sport name | Discovery treatment |
|---|---:|---|---|
| NCAAF | 1 | NCAA Football | ACTIVE |
| NFL | 2 | NFL | ACTIVE |
| MLB | 3 | MLB | ACTIVE |
| NBA | 4 | NBA | ACTIVE |
| NCAAB | 5 | NCAA Men's Basketball | ACTIVE |
| NHL | 6 | NHL | ACTIVE |
| MMA/UFC | 7 | UFC/MMA | ACTIVE |
| WNBA | 8 | WNBA | ACTIVE |
| Soccer / MLS | 10 | MLS | ACTIVE |
| Soccer / EPL | 11 | EPL | ACTIVE |
| Soccer / Ligue 1 | 12 | FRA1 | ACTIVE |
| Soccer / Bundesliga | 13 | GER1 | ACTIVE |
| Soccer / La Liga | 14 | ESP1 | ACTIVE |
| Soccer / Serie A | 15 | ITA1 | ACTIVE |
| Soccer / UEFA Champions League | 16 | UEFACHAMP | ACTIVE |
| Soccer / UEFA Euro | 17 | UEFAEURO | ACTIVE |
| Soccer / FIFA | 18 | FIFA | ACTIVE |
| Soccer / J1 | 19 | JPN1 | ACTIVE |
| Soccer / UEFA Europa League | 33 | UEFA Europa League | ACTIVE |
| Soccer / Liga MX | 34 | Liga MX | ACTIVE |
| Tennis / ATP | 38 | ATP | ACTIVE |
| Tennis / WTA | 39 | WTA | ACTIVE |
| Golf / PGA | 40 | PGA | ACTIVE |

### Competition/regime variants

These provider IDs are discoverable but must not automatically inherit regular-season model certification:

| Family | ID | Provider name |
|---|---:|---|
| NBA | 23 | NBA Preseason |
| NBA | 24 | NBA Playoffs |
| NFL | 25 | NFL Preseason |
| NFL | 26 | NFL Playoffs |
| NHL | 27 | NHL Preseason |
| NHL | 28 | NHL Playoffs |
| MLB | 30 | MLB Spring Training |
| MLB | 31 | MLB Playoffs |
| NBA | 32 | NBA Summer League |

Rule:

```text
discovery may include regime variant
BUT
model routing requires explicit model/regime support
```

For example, a fitted NFL regular-season model must not automatically score NFL preseason unless its artifact/input/calibration contract explicitly permits that regime.

## 6. Unsupported / absent provider families

### Boxing

The current TheRundown sport registry does not expose a boxing sport ID.

Therefore:

```text
BOXING discovery via TheRundown = NOT_CONFIGURED
```

Required behavior:
- try another authorized discovery source when available;
- otherwise emit `NO_CONFIGURED_DISCOVERY_FEED`;
- never guess a provider key;
- never silently report zero boxing events as though inventory were checked.

### Other LLP sports without provider mapping

Apply the same rule:

```text
NO_CONFIGURED_DISCOVERY_FEED
```

is an explicit acquisition blocker, not an empty slate.

## 7. Soccer discovery expansion

Soccer discovery must not map only to EPL.

At minimum, query the active soccer IDs listed in section 5 that are relevant to the requested date/session.

The canonical family remains `SOCCER`, with league/competition preserved separately.

Required row identity:

```text
sport = SOCCER
league = exact canonical competition
provider_sport_id = exact TheRundown ID
```

Do not collapse all competitions into EPL.

## 8. Discovery audit

For every configured discovery family report:

```text
family
provider
provider_sport_ids_attempted
request_status
events_returned
blocker_if_any
```

Distinguish:

```text
NO_EVENTS_RETURNED
NO_CONFIGURED_DISCOVERY_FEED
PROVIDER_REQUEST_FAILED
PROVIDER_RATE_LIMITED
PROVIDER_SCHEMA_FAILURE
```

`NO_EVENTS_RETURNED` may be used only after a configured provider query actually succeeds.

## 9. Cross-sport discovery set

The requested supported discovery set remains broader than current fitted-model coverage.

A recommended discovery configuration is:

```yaml
NCAAF: [1]
NFL: [2]
MLB: [3]
NBA: [4]
NCAAB: [5]
NHL: [6]
MMA: [7]
WNBA: [8]
SOCCER: [10,11,12,13,14,15,16,17,18,19,33,34]
TENNIS: [38,39]
GOLF: [40]
BOXING: []
```

Season/regime variants should be held in a separate mapping with explicit routing policy.

## 10. Model routing remains independent

After discovery:

```text
discovered event
→ canonical sport/league/regime
→ exact registered fitted model resolver
```

Examples:

```text
NFL regular season + registered NFL model
=> route to NFL model

NFL preseason + only regular-season model certified
=> do not automatically route
=> MODEL_UNAVAILABLE or MODEL_INPUTS/REGIME_UNSUPPORTED according to active contract

ATP event + no tennis model
=> MODEL_UNAVAILABLE

Boxing feed unavailable
=> acquisition blocker before model routing
```

Do not blur acquisition failure and model capability failure.

## 11. Acceptance tests added by this patch

### CD-001 — NFL bridge is not automatic certification

Setup:
- NFL bridge registered and UP;
- certification evidence absent.

Expected:

```text
registered_capability=true
certification_status=CANDIDATE_REGISTERED_UNCERTIFIED
NFL not added to CERTIFIED_TEAM_EVENT_SPORTS
```

### CD-002 — Certification after evidence

Setup:
- all active certification gates satisfied.

Expected:
- governance may add NFL to certified set through the repository's normal certification mechanism;
- certification ID/version recorded;
- health exposes certification separately from bridge status.

### CD-003 — NCAAF authoritative ID

Expected configured provider ID:

```text
1
```

### CD-004 — Tennis authoritative IDs

Expected:

```text
ATP=38
WTA=39
```

No guessed keys.

### CD-005 — Golf authoritative ID

Expected:

```text
PGA=40
```

### CD-006 — Soccer multi-league

Expected configured set includes multiple active IDs, not EPL alone.

### CD-007 — Boxing no provider key

Expected:

```text
NO_CONFIGURED_DISCOVERY_FEED
```

when no alternate authorized source is available.

Never guessed TheRundown key.

### CD-008 — Preseason regime protection

NFL preseason event discovered on ID 25.

Only regular-season NFL fitted model exists.

Expected:
- discovery row retained;
- no automatic regular-season model substitution;
- explicit regime/model blocker.

### CD-009 — Empty slate semantics

Configured provider request succeeds and returns zero events.

Expected:

```text
NO_EVENTS_RETURNED
```

Not `NO_CONFIGURED_DISCOVERY_FEED`.

### CD-010 — Provider failure semantics

Configured provider request fails.

Expected explicit provider failure status.

Do not report zero-event success.

## 12. Final governance decision

At this stage:

```text
MLB certification: preserve existing active governance state
NFL runtime registration: ACCEPTABLE AFTER DEPLOYMENT/SMOKE PASS
NFL certification: DEFERRED
other sports certification: unchanged / not certified unless separately proven
```

The next certification action after deployment is to audit the NFL fitted artifact and its calibration/prospective evidence under the active V17 certification process.

## 13. Execution invariant

Always:

```text
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```
