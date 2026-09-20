# WOW V17 Prop Hydration Audit — 2026-09-20

Status: repair branch `fix/v17-prop-hydration-identity-audit`

Governance invariants preserved:

- player/scalar props remain in `WOW_PROP_LANE`;
- team/event probability remains owned by LLP and is not imported into prop scoring;
- provider event IDs are aliases, not model probabilities and not canonical identity by guess;
- no fitted-model coefficients, calibration artifacts, probability outputs, ranking thresholds, terminal authority, or settlement rules are changed;
- `V17_TERMINAL_REDUCER` remains the global terminal authority;
- `can_execute=false` remains invariant.

## Audit findings

### H1 — Canonical event identity was dropped during interactive hydration

`v17/interactive_pick_hydration.py` included `row.event_id` in its hydration cache key but did not forward that ID into evidence acquisition. The event therefore participated in deduplication while being absent from the actual hydration contract.

Repair: interactive hydration now forwards `canonical_event_id=row.event_id` and continues to forward the row opponent.

### H2 — Canonical scorer fallback also omitted canonical event identity and opponent

The producing core predates the current canonical identity contract and invokes the generic hydrator without `event_id` or `opponent`. Editing model/scoring behavior is unnecessary; the V17 facade now binds the batch's immutable row identities for the duration of the canonical call. The shared router inherits those values only when explicit arguments are absent.

Repair: request-scoped `hydration_request_context`; explicit hydrator arguments always override inherited context.

### H3 — NFL ESPN IDs were exposed as generic event IDs

The NFL hydrator resolves an ESPN event ID. That value is useful evidence, but V17 provider-neutral identity rules do not permit an ESPN alias to overwrite or compete with a WOW canonical event ID.

Repair: the shared binding layer preserves the legacy ESPN `event_id` for backward compatibility while adding:

- `role_status.canonical_event_id = <WOW canonical id>`
- `role_status.provider_event_ids.ESPN = <ESPN event id>`
- `role_status.identity_binding_status = PASS`

Opaque WOW NFL event IDs are deliberately not string-compared to ESPN IDs.

### H4 — NFL opponent verification in the provider module was ineffective

The provider module computed the requested opponent key but did not actually compare it to the other event participant. That allowed a bad opponent to survive provider acquisition.

Repair: the shared event-binding layer now verifies the hydrated NFL opponent against the requested opponent, including current team abbreviations and common full-name aliases. A mismatch terminates with `PROP_EVENT_IDENTITY_CONFLICT` before evidence can be frozen or scored.

### H5 — WNBA simultaneous tip times could create false identity conflicts

The WNBA base hydrator first resolved an event by start time alone and required one unique nearest game. When multiple games shared a tip time, it could fail before using player/team identity, even though the player uniquely identified one event.

Repair: the sport-aware router wraps the official WNBA schedule resolver. The original fast path remains unchanged when time uniquely identifies the game. On an identity tie only, the wrapper uses official current roster/player-team evidence plus the requested opponent to resolve exactly one event. Ambiguity still fails closed with `PROP_EVENT_IDENTITY_CONFLICT`.

### H6 — MLB canonical Daily IDs were not reconciled with StatsAPI gamePk

Daily already creates canonical IDs as `MLB:<gamePk>`, but generic evidence hydration did not verify that prefix against the official hydrated game.

Repair: the shared identity binder compares `MLB:<gamePk>` with `role_status.official_game_pk`. A mismatch is `PROP_EVENT_IDENTITY_CONFLICT`. Daily now also carries the official schedule opponent into hydration and the immutable row.

### H7 — Unsupported sports entered the MLB fallback dispatcher before being rejected

The base MLB hydrator ultimately rejected non-MLB sports, so it did not fabricate MLB evidence. However, the shared router still dispatched unknown sports into the MLB fallback and `provider_for_sport()` mislabeled them as the MLB provider. This obscured the real capability state and made CFB/NCAAF failures look like provider problems.

Repair: non-MLB/WNBA/NFL sports now fail in the shared router with `PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE` and provider `UNREGISTERED_PROP_HYDRATION_PROVIDER`. The MLB hydrator is not invoked.

Important: this does **not** add NCAAF/CFB model or hydration capability. Current lifecycle registration remains authoritative. A future CFB prop lane must register its own reviewed exact-route hydrator and fitted model rather than borrowing another sport's evidence path.

### H8 — Automatic hydration receipts mislabeled NFL/WNBA as MLB

The historical core stamped `MLB_STATS_API_OFFICIAL_V1` into generic acquisition receipts regardless of sport.

Repair: the V17 receipt facade normalizes FULL response acquisition telemetry through `provider_for_sport(sport, canonical_stat)`. This is telemetry only; terminal/model semantics are unchanged.

### H9 — Frozen evidence did not verify a supplied canonical binding

The historical validator trusts the request `event_id` and validates timing/log/opportunity evidence, but it did not compare an evidence packet that explicitly carried its own canonical binding.

Repair: the V17 facade extends the validator so a present `role_status.canonical_event_id` must exactly equal the row `event_id`. A mismatch fails before snapshot persistence/scoring.

## Shared contract after repair

For automatic hydration on a governed prop request:

1. the row enters with WOW canonical `event_id`;
2. the shared router receives that ID explicitly or through the request-scoped identity context;
3. the sport provider resolves official player/team/event evidence;
4. provider event IDs remain typed aliases in `provider_event_ids`;
5. requested opponent is reconciled against the official hydrated event;
6. safely parseable canonical/provider pairs (currently `MLB:<gamePk>`) are reconciled directly;
7. the frozen evidence packet retains the canonical binding;
8. validation rejects any attempt to rebind that evidence to another event;
9. only then can Scout/Research and the exact fitted specialist proceed.

No team-event/LLP probability participates in these steps. A Daily/team-event lane may be used upstream to recover an official canonical event identity, but its moneyline result is not evidence for the player prop probability.

## Regression coverage added

`test_v17_prop_hydration_identity_audit.py` covers:

- NFL canonical ID + ESPN alias coexistence;
- NFL full-name opponent alias acceptance;
- NFL opponent mismatch fail-closed behavior;
- canonical identity recovery in scorer fallback context;
- MLB `MLB:<gamePk>` mismatch detection;
- NCAAF/CFB rejection before MLB hydration;
- WNBA same-tip player/team disambiguation;
- interactive forwarding of canonical ID/opponent;
- frozen-evidence canonical rebind rejection;
- sport-correct acquisition provider telemetry;
- Daily MLB canonical ID + opponent extraction.

## Remaining capability boundary

This audit repairs plumbing for routes that already have reviewed hydration/model capability. It intentionally does not certify new prop categories. In particular, CFB/NCAAF stays typed `PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE` until its exact route lifecycle proves a sport-specific provider, fitted artifact, calibration, certification, runtime registration, and Action canary.
