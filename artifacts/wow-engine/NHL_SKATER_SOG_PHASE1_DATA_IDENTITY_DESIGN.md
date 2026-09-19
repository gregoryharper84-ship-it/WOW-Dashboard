# NHL Skater Shots on Goal — Phase 1: Data Source + Identity Resolution Design

Authority: this document is a design spec only. It grants no capability,
certifies no model, and changes no code. `can_execute=false`. Per audit
(`NHL_SOG_AUDIT_2026-09-19`, classification `NO_EXISTING_VERTICAL`,
2/23 ≈ 8.7% complete), this is a genuine blank-slate vertical. Nothing in
this document may be read as authorizing the manifest entry, a specialist
placeholder, a fitted artifact, calibration, or a scorer — those are later
phases, gated on this design being reviewed.

## 0. What already exists and must be reused, not re-derived

- **NHL is already a declared V17 sport** with a working team/game
  ingestion lane: `nhl_candidate_pipeline.py` (`SOURCE_ID =
  "NHL_PUBLIC_WEB_API"`, `BASE_URL =
  "https://api-web.nhle.com/v1/club-schedule-season/{team}/{season_id}"`).
  This is a **D1 research/candidate lane only**
  (`PROBABILITY_PUBLISHABLE = False`) for team-winner Elo features, not
  props — but its game identity, team-abbreviation table, and franchise
  relocation handling (`TEAM_ABBREVIATIONS` already includes `ARI`/`UTA`
  for the Coyotes→Utah relocation) are the correct foundation to extend to
  skater-level identity, not replace.
- **Naming drift to reconcile before PR 1, not after**:
  `historical_source_manifest_v1.json` lists this same source as
  `NHL_PUBLIC_API` (candidate, `LICENSE_REVIEW_REQUIRED`), while
  `v17/model_source_entitlements.py` and `nhl_candidate_pipeline.py`
  (`SOURCE_ID = "NHL_PUBLIC_WEB_API"`) both use `NHL_PUBLIC_WEB_API`
  for the identical underlying source (`api-web.nhle.com`
  box-score/play-by-play/game-log). These are two string IDs for one
  logical source, not two distinct contracts. **Decision for this
  design: `NHL_PUBLIC_WEB_API` is canonical** (it matches the code that
  is actually wired and entitlement-gated); `NHL_PUBLIC_API` in the
  manifest is a legacy label for the same source and must be corrected
  to an alias of, or renamed to, `NHL_PUBLIC_WEB_API` as an explicit
  acceptance criterion of PR 1 (see Section J.10) — not silently
  duplicated into a second logical source.
- `SPORTRADAR_NHL` (`CONTRACT_REQUIRED`, coverage from 2013) remains a
  separate, genuinely distinct licensed source. Neither source
  `grants_model_capability`. This design extends the manifest with
  skater-shot-specific rows under the reconciled `NHL_PUBLIC_WEB_API`
  ID rather than inventing a parallel one.
- `v17/model_source_entitlements.py` already registers
  `NHL_PUBLIC_WEB_API` as `CANDIDATE_FIRST_PARTY_UNDOCUMENTED` with
  `certification_source_review_required=True`. Any new NHL skater-data
  source added under this design inherits the same review gate — no
  source becomes trusted for training just by being named here, and
  **ingestion working is not the same fact as the source being
  certified** (see Section J.10).
- **Generic identity-reconciliation contract already exists and should be
  reused, not reinvented**: `v17/research_identity_reconciliation.py`'s
  `IdentityResolution` states (`LINKED` / `IDENTITY_UNRESOLVED` /
  `IDENTITY_CONFLICT`, requiring an explicitly `verified` provider→canonical
  mapping row before `LINKED` is possible) is exactly the fail-closed shape
  Section B below needs for skater identity. No fuzzy match should ever
  auto-promote to `LINKED`.
- The canonical exact-line request shape already used across every shipped
  prop (MLB, WNBA) is `prop_distribution_contract.PropInferenceRequest`:
  `event_id, player_id, sport, league_season, stat_type,
  evidence_snapshot_id, market_identity_id, as_of_timestamp, request_id,
  feature_schema_version` — all required, all fail closed
  (`PROP_INFERENCE_IDENTITY_INCOMPLETE`) if any is blank. Section A's
  canonical contract is written to slot directly into this shape.

## A. Canonical identity contract

| Field | Type | Normalization rule |
|---|---|---|
| `league` | const | `"NHL"` |
| `season_label` | string | Human-readable `"2026-2027"` form, display/reporting only, never used as a join key |
| `provider_season_id` | string | The actual join key, matching `nhl_candidate_pipeline.py`'s `season_id` convention verbatim (e.g. `20262027`); `season_label` is derived from this, never the reverse |
| `game_id` | string | NHL official `gamePk`/`id` from `api-web.nhle.com`, stored verbatim as string (never re-derived or guessed); a third-party provider's own game ID is a separate `provider_game_id`, never substituted here |
| `game_start_time` | ISO-8601 UTC | source-provided; never client-supplied, never inferred from local scoreboard time |
| `home_team_id` / `away_team_id` | string | **stable canonical/provider team identifier** (e.g. the official numeric or franchise-scoped ID from `api-web.nhle.com`), not an abbreviation; a relocated/renamed franchise (Arizona→Utah) is either the same stable ID carried across the relocation or a new stable ID with an explicit franchise-succession mapping, decided during source review — but never represented only as a changing abbreviation string |
| `home_team_abbreviation` / `away_team_abbreviation` | string | display/reporting/normalization-evidence field, sourced from the existing `TEAM_ABBREVIATIONS` table; historical-period abbreviation is preserved for historical games (e.g. `ARI` pre-relocation, `UTA` post-relocation) even though `*_team_id` above is the stable identifier — abbreviation is derived evidence, never the join key |
| `team_id` | string | the skater's stable team identifier for *this* game (handles in-season trades; never the season-opening team); same stable-ID rule as `home_team_id`/`away_team_id`, not an abbreviation |
| `player_id` | string | official NHL player ID (numeric, from the same public API family used for game IDs) |
| `player_name` | string | display name is evidence/labeling only, never an identity key; see Section B |
| `position` | enum | `C`/`LW`/`RW`/`D` (goalies excluded from this vertical entirely — SOG is a skater-only stat) |
| `stat_type` | const | canonical value `SHOTS_ON_GOAL` (see alias table below) |
| `period` | const (V1) | fixed to `FULL_GAME` for V1; a first-class schema field, not prose-only — see canonical prop key below |
| `line` | decimal | exact numeric line as offered, 0.5-increment typical; stored at source precision, never rounded |
| `direction` | enum | `MORE` / `LESS`, matching the existing cross-sport convention in `prop_distribution_contract.py` |
| `platform` | string | e.g. `PRIZEPICKS`, or `UPLOADED_BOARD` for manually supplied boards |
| `offer_type` | string | e.g. `STANDARD`, `FLEX`, `GOBLIN`/`DEMON` equivalents if platform-specific line adjustments exist — captured but never silently folded into `line` |
| `market_identity_id` | string | immutable exact-offer identity, matching `PropInferenceRequest.market_identity_id`'s existing role — the non-lossy anchor for this specific offer, independent of the derived prop key below |
| `settlement_rule_version` | string | the specific settlement-rule version in effect for this offer (push/void/exact-line rules can change over time); required alongside `market_identity_id` so settlement never re-derives a rule from a mutable "current" table |
| `observed_at` | ISO-8601 UTC | when the offer/data point was actually retrieved (source-clock or ingestion-clock, whichever is source-verifiable) |
| `as_of` | ISO-8601 UTC | the caller-declared evaluation instant for this request (matches `PropInferenceRequest.as_of_timestamp`) |
| `source` | string | the specific provider/source ID (see Section C); never a generic label like `"web"` |

**Canonical stat alias resolution** (global aliases, case/whitespace-normalized before lookup, all mapping to `SHOTS_ON_GOAL`):
`"Shots on Goal"`, `"SOG"`, `"Player Shots on Goal"`, `"shots_on_goal"`.
**`"Shots"` alone is deliberately excluded from this global table** — it
is genuinely ambiguous with shot attempts/Corsi and must never be a
universal alias. If a specific platform is confirmed to use bare
`"Shots"` to mean SOG (e.g. PrizePicks, pending verification), that
mapping is registered as a **source-scoped alias**
(`platform="PRIZEPICKS", raw_label="Shots" -> SHOTS_ON_GOAL`), not added
to the global table. Any alias not found in either the global table or a
verified source-scoped table is a typed blocker
(`PROP_STAT_ALIAS_UNRECOGNIZED`), never a best-guess match.

**Canonical prop key** (mirrors the existing MLB exact-line identity
shape used for settlement matching, extended per V17's non-lossy exact-offer
requirement): `(league, game_id, player_id, stat_type, line, direction,
period, platform, market_identity_id, settlement_rule_version)`. `period`
is fixed to `FULL_GAME` for V1 (period-level SOG, e.g. 1st-period-only
offers, is out of scope for V1 and must fail closed as
`LINE_OUTSIDE_CERTIFIED_SUPPORT` rather than silently scored as
full-game). `platform` and `market_identity_id` are included because the
same numeric line/direction on two different platforms, or two different
offers on the same platform under different settlement rules, are
distinct offers and must never be merged into one settlement identity.

## B. Identity reconciliation

Built as an NHL-specific application of the existing
`IdentityResolution` contract (`v17/research_identity_reconciliation.py`),
not a new state machine:

- **Player name variants / suffixes / accents / punctuation**: resolution
  is never by name. `player_name` is a display label only. The only
  authoritative key is `player_id` from a verified provider→canonical
  mapping row (`provider_entity_map`-style table, extended to
  `sport_key="NHL"`, `entity_type="PLAYER"`). A name-based lookup that
  finds zero or >1 verified candidate returns `IDENTITY_UNRESOLVED` /
  `IDENTITY_CONFLICT` respectively — never an automatic best-match.
- **Traded players / call-ups**: `team_id` is resolved per game from the
  official game roster/boxscore for that specific `game_id`, never from a
  cached season-opening roster. A player appearing for a new team is a
  normal `LINKED` resolution with an updated `team_id`, not a new
  `player_id`.
- **Scratches**: a scratch is a lineup-status fact (Section D/E), not an
  identity fact — the player's identity remains `LINKED`; the row instead
  fails closed at the lineup-confirmation gate (`MLB_STARTER_STATUS_UNRESOLVED`'s
  NHL analogue, proposed as `NHL_LINEUP_STATUS_UNRESOLVED` in Section H).
- **Same/similar names**: two distinct verified `player_id`s with similar
  or identical display names must never collapse into one canonical
  entity; the verified-mapping table is keyed by provider ID, not name,
  precisely to prevent this.
- **Team abbreviations**: `team_abbreviation` (display/normalization
  evidence only, per Section A) is resolved through the existing
  `TEAM_ABBREVIATIONS` table in `nhl_candidate_pipeline.py`, extended
  (not replaced) if a new source uses different abbreviations (e.g.
  three-letter vs. city-based) — a translation table per source, mapping
  into the one canonical abbreviation set. This is separate from
  resolving the stable `team_id`, which never changes based on which
  abbreviation a given source happens to use.
- **Relocated/renamed franchises**: Arizona Coyotes → Utah is the only
  currently relevant case. The existing D1 pipeline represents this only
  as an abbreviation change (`ARI`/`UTA`); this design requires the
  stable `team_id` question be resolved explicitly during source review
  before PR 1 ships franchise-spanning historical rows — either the
  stable ID is carried across the relocation (same franchise, new
  abbreviation) or an explicit franchise-succession mapping links two
  stable IDs. Historical `team_abbreviation` is always preserved
  per-period (`ARI` pre-relocation, `UTA` post-relocation) regardless of
  which stable-ID approach is chosen. Any future relocation/rename
  follows the same pattern: never rewrite historical rows.
- **Postponed/rescheduled games**: the canonical `game_id` and
  `game_start_time` always reflect the game as actually played. A
  snapshot taken against a since-postponed `game_start_time` is stale
  evidence (Section D) and must be re-validated at final board refresh,
  not silently carried forward.
- **Doubleheaders**: confirmed irrelevant — the NHL does not schedule
  doubleheaders; `game_id` uniqueness per team per day is not a concern
  this design needs to handle.
- **Source game-ID mapping across providers**: each source's native game
  ID is stored as `provider_game_id` alongside the canonical `game_id`;
  cross-provider reconciliation requires an explicit verified mapping
  (date + both team IDs + start time within tolerance), and an
  unresolvable cross-provider match is a typed blocker
  (`PROP_EVENT_IDENTITY_CONFLICT`, already a canonical V17 code), never a
  best-effort fuzzy join.

## C. Source inventory and hierarchy

| # | Need | Candidate source(s) | Official? | Historical depth | Refresh cadence | Timestamps? | IDs | Rate limits | Cost/license | Reliability | Pregame available? | Leakage risk | Fallback | Priority |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Schedule/game IDs | `NHL_PUBLIC_WEB_API` (`api-web.nhle.com`, already integrated) | Official | Full modern era | Daily/live | Yes | Native | Undocumented, must be rate-limited defensively (per existing `CANDIDATE_FIRST_PARTY_UNDOCUMENTED` entitlement) | Free | Unreviewed for production (per entitlement gate) | Yes | None | `SPORTRADAR_NHL` | 1 |
| 2 | Player/team IDs | Same (`NHL_PUBLIC_WEB_API`) | Official | Full modern era | Static/roster-cadence | Yes | Native | Same | Free | Same review status | Yes | None | `SPORTRADAR_NHL` | 1 |
| 3 | Historical skater SOG | `NHL_PUBLIC_WEB_API` boxscore/gamelog endpoints (not yet integrated — pipeline currently pulls schedule/results only, per audit item 7) | Official | Since public API existed (~2010s+; exact coverage TBD in source review) | Post-game | Yes | Native | Same | Free | Unreviewed | No (post-game only) | High if misused pregame — historical rows only | `SPORTRADAR_NHL` (2013+, `CONTRACT_REQUIRED`) | 1 |
| 4 | Shot attempts/Fenwick/Corsi | `NHL_PUBLIC_WEB_API` play-by-play, or `SPORTRADAR_NHL` | Official/licensed | Varies | Post-game | Yes | Native | Same | Free/licensed | Unreviewed/contract | No | High if pregame-derived from live state | Third-party advanced-stats aggregator (`DO_NOT_USE` pending review) | 2 |
| 5 | Individual xG/shot quality | Licensed only (e.g. Sportradar advanced package) — **no free-tier source confirmed** | Licensed | Contract-dependent | Contract-dependent | TBD | TBD | TBD | Paid | TBD | No | TBD | None confirmed — treat as `EXPERIMENTAL`/unavailable for V1 | 3 (V1: not required) |
| 6 | Ice time (TOI/EV/PP) | `NHL_PUBLIC_WEB_API` boxscore | Official | Modern era | Post-game (actuals); no confirmed pregame *projection* source identified | Yes | Native | Same | Free | Unreviewed | Actuals: no. Projection: source TBD | High if actual TOI leaks into a pregame feature | None confirmed for pregame projection | 1 (actuals for training), open item (pregame projection) |
| 7 | EV line assignment | Same boxscore + line-combination trackers (e.g. public line-combo sites) — **no official structured source confirmed** | Unofficial for line combos | Varies | Daily (unofficial sites) | Inconsistent | Name-based, needs mapping | Site-dependent | Free (unofficial) | Low-medium, needs verification | Yes (pregame projected combos exist on some sites) | Medium — must be timestamped and versioned like any other pregame evidence | Confirmed boxscore icetime as post-hoc validation only | 2 |
| 8 | PP-unit assignment | Same unofficial line-combo sources | Unofficial | Varies | Daily | Inconsistent | Name-based | Site-dependent | Free | Low-medium | Yes (projected) | Medium | Same | 2 |
| 9 | Scratches/injuries | `NHL_PUBLIC_WEB_API` (official injury report, if exposed) or league injury report page | Official (if exposed via API) else official page | N/A | Daily/pregame | Yes if API-sourced | Native | Same | Free | TBD | Yes | Low if genuinely pregame and timestamped | Manual official injury report scrape | 1 |
| 10 | Confirmed projected lineups | Same line-combo/lineup sites, cross-checked against official scratches | Unofficial, cross-checked | N/A | Pregame, updated close to puck drop | Inconsistent | Name-based | Site-dependent | Free | Medium | Yes | Medium-high if not re-validated close to game time | Neutral/no-lineup-assumption fallback (fail closed, never fabricate) | 1 for gating, not for silent trust |
| 11 | Opponent shots allowed/suppression | Derived from official boxscore history (opponent's own shots-against rate) | Official (derived) | Same as #3 | Rolling, recomputed | Yes | Native | Same | Free | Same as #3 | Yes (as a rolling historical rate) | Low if computed only from strictly-prior games | None needed (self-derived) | 1 |
| 12 | Goalie starter/status | Same official source family as scratches/lineups | Official (if exposed) else cross-checked unofficial | N/A | Pregame, updated close to puck drop | Yes/TBD | Native | Same | Free | TBD | Yes | Medium — goalie confirmation often finalizes late | Neutral goalie-context fallback (fail closed on unresolved status, do not assume backup vs. starter) | 1 |
| 13 | Rest/travel/back-to-back | Derived from official schedule (`game_start_time` deltas, already computed for the team-winner Elo lane as `rest_days_delta`, `home_back_to_back`, `away_back_to_back`) | Official (derived) | Full | Static per schedule | Yes | Native | Same | Free | High — already shipped for the game-winner lane | Yes | None | None needed | 1 |
| 14 | Venue/home-away | Official schedule | Official | Full | Static | Yes | Native | Same | Free | High | Yes | None | None needed | 1 |
| 15 | PrizePicks/uploaded-board exact-line offers | Existing prop-offer ingestion pattern (platform-specific, already used for MLB/WNBA) | Third-party platform | N/A | Live/frequent | Yes | Platform-native, requires player-name reconciliation (Section B) | Platform-dependent | Free/existing integration | Existing, reused | Yes | Low if `observed_at` is honestly captured | None needed | 1 |

**Conflict-resolution priority (general rule):** official first-party
(`NHL_PUBLIC_WEB_API`) > licensed official-adjacent
(`SPORTRADAR_NHL`) > cross-checked unofficial (line combos/lineups) >
single unofficial source alone. A cross-checked unofficial source may
gate lineup-confirmation *evidence* (Section D/H) but must never alone
satisfy a hard identity or settlement fact.

**Open item flagged, not resolved here:** #5 (individual xG/shot quality)
and the *pregame projection* half of #6–#8 (ice time, line, PP-unit
*projections* rather than post-game actuals) have no confirmed free
first-party source. This is a genuine data-source decision the team must
make before Phase 2 feature hydration can be fully scoped — V1 can ship
without #5 entirely (Section G marks it `EXPERIMENTAL`/`DO_NOT_USE`), but
#6–#8 pregame projections are `HIGH_VALUE_OPTIONAL` at best without a
confirmed source, and role/TOI context may need to launch V1 on
*confirmed* (not projected) recent-role history instead (see Section G).

## D. Freshness SLAs

| Data | Fresh (no action) | Degraded (feature marked low-confidence, scoring may proceed) | Typed blocker (scoring rejected) |
|---|---|---|---|
| Schedule/game identity | Any time before puck drop | N/A | Game already started/cancelled/postponed at `as_of` → `EVENT_ALREADY_STARTED`/`EVENT_CANCELLED`/`EVENT_POSTPONED` (existing canonical codes) |
| Player roster | Updated same day | Roster snapshot 24–48h old | Roster snapshot >48h old at `as_of` → `NHL_ROSTER_STATUS_STALE` (proposed) |
| Injuries | Updated within 6h of `as_of` | 6–24h old | >24h old, or absent entirely for a game within 3h of puck drop → `NHL_INJURY_STATUS_UNRESOLVED` (proposed) |
| Confirmed scratches | Confirmed within 2h of puck drop | Projected-only, 2–6h out | No scratch information at all within 2h of puck drop → `NHL_LINEUP_STATUS_UNRESOLVED` (proposed) |
| Line combinations | Confirmed same-day pregame skate/morning report | Prior-game combo carried forward, <48h old | No combo information within 48h, or a confirmed in-game trade/scratch invalidates the carried-forward combo → feature degrades to `HIGH_VALUE_OPTIONAL` absent, never fabricated |
| PP units | Same as line combinations | Same | Same |
| Projected/confirmed goalie | Confirmed within 3h of puck drop | Projected only, >3h out | Fully unresolved within 1h of puck drop → `NHL_GOALIE_STATUS_UNRESOLVED` (proposed) |
| Historical stats | N/A (immutable once settled) | N/A | Insufficient prior-game sample below minimum window (Section F) → `MODEL_INPUTS_INSUFFICIENT` (existing canonical code, NHL-specific reason `NHL_RECENT_GAMES_INSUFFICIENT` proposed, mirroring `MLB_RECENT_STARTS_INSUFFICIENT`) |
| Market/PrizePicks line | `observed_at` within the platform's normal refresh cadence | Line stale relative to a since-changed official board | Line confirmed removed/changed at final board refresh (Section 17 of the audit) → `PRICE_STALE`/`MARKET_DATA_UNAVAILABLE` block the **market/value/card publication lane only**; a sporting probability the specialist already completed against the originally-requested exact line is not automatically discarded or re-labeled `MODEL_UNAVAILABLE` — per V17's sporting/market separation, a completed sporting-probability package may be retained (e.g. for postmortem/evidence purposes, or re-offered if the same exact line reappears) exactly where the governing contract allows, while the market lane independently reports the offer as blocked |

## E. Leakage-safe snapshot design

Every NHL SOG pregame feature snapshot record carries:

```
{
  "schema_version": "NHL_SOG_FEATURE_SNAPSHOT_V1",
  "source_timestamp": "<when the underlying source recorded/published this fact>",
  "observed_at": "<when WOW's ingestion actually retrieved it>",
  "effective_at": "<the game/context instant this fact applies to>",
  "as_of": "<the evaluation instant this snapshot is valid for>",
  "provenance": {"source": "<source id from Section C>", "provider_game_id": "...", "provider_player_id": "..."},
  "freshness_status": "FRESH | DEGRADED | STALE",
  "transformation_version": "NHL_SOG_TRANSFORM_V1",
  "features": { ... }
}
```

Hard rule, mirroring the MLB pattern (`prop_auto_hydration.py` filtering
strictly to `game_date < event_start.date()`): **no fact whose
`effective_at` or `source_timestamp` is at or after this game's puck-drop
time may enter this game's feature snapshot**, full stop — including a
same-day post-game boxscore for an earlier game that day used to infer a
same-day lineup change; that must be captured as an explicit lineup-status
fact with its own `effective_at`, not silently merged into the numeric
feature set. A snapshot assembled after puck drop is rejected outright
(`EVENT_ALREADY_STARTED`), matching the existing MLB gate.

## F. Historical training dataset

**One training row = one (player × game).**
**Target = `actual_shots_on_goal`** (integer, from the official post-game
boxscore, provenance-tagged as settled/immutable, never a live or
in-progress count).

Minimum raw tables required to reconstruct a row without future
information:
1. Official schedule/game table (game_id, teams, start time, venue) — exists.
2. Official skater boxscore-by-game table (player_id, team_id, position,
   TOI splits, SOG, goals/assists if useful as auxiliary evidence) — **does
   not yet exist for NHL** (audit item 7: current pipeline is team-level
   only); must be built.
3. Roster-by-game table (who was on the active roster / who dressed for
   this specific game) — needed to distinguish "0 shots because scratched
   or DNP" from "0 shots while playing," which must never be conflated in
   training.
4. Line-combination/PP-unit-by-game table, where available, as auxiliary
   role context (may be sparse/lower-confidence pre-modern-era; see below).
5. Team-level shots-against-by-game table (derivable from #2, aggregated
   by opposing team).

**Proposed historical window:** start with the most recent 3–4 full NHL
seasons for initial fitting (balances sample size against realistic
concern about materially different role usage/rules further back);
extend backward only after the source's actual coverage and quality are
confirmed in source review (Section C flags coverage depth as partially
TBD).

**Representation rules:**
- **Trades**: a traded player contributes rows under both teams,
  `team_id` per game as defined in Section B; no season-long team-level
  aggregate may be applied across a trade boundary.
- **Rookies/small samples**: a player with fewer prior games than the
  minimum window (Section G thresholds) is not excluded from the
  *dataset*. At *inference* time, insufficient effective sample fails
  closed via `MODEL_INPUTS_INSUFFICIENT` (proposed reason
  `NHL_RECENT_GAMES_INSUFFICIENT`) **unless the certified specialist
  explicitly supports that cohort through fitted, validated model
  structure** (e.g. a hierarchical model with fitted shrinkage toward a
  population/role prior, analogous to the shrinkage already used in the
  MLB strikeouts adapter) — a generic, unfitted league-average fallback
  remains invalid and is never an acceptable substitute, but this
  document does not foreclose a future certified low-sample-capable
  model design.
- **Call-ups**: identical treatment to rookies for feature-sufficiency
  purposes — call-up status itself is not a separate flag needed in the
  schema beyond "how many qualifying prior games exist."
- **Role changes** (e.g. moved to a top line or PP1 mid-season): captured
  through the recency-weighted rolling features in Section G (e.g. a
  short-window role/usage feature), not through a manual "role change"
  indicator variable, to avoid an unreviewed subjective label entering
  training.

## G. Feature availability matrix

| Feature | Classification | Notes |
|---|---|---|
| SOG/game (rolling) | REQUIRED_V1 | Core signal; needs #2 (skater boxscore table, not yet built) |
| Shot attempts/game (Corsi-adjacent) | HIGH_VALUE_OPTIONAL | Available from PBP/advanced sources (Section C #4); not required for a V1 fit |
| Shots/60 | REQUIRED_V1 | Rate-normalized version of SOG/game; needed to compare across differing TOI |
| Attempts/60 | HIGH_VALUE_OPTIONAL | Same caveat as shot attempts |
| TOI (actual, historical) | REQUIRED_V1 | For rate normalization and role proxy; source confirmed (boxscore) |
| EV TOI | HIGH_VALUE_OPTIONAL | Refinement of TOI; source confirmed |
| PP TOI | HIGH_VALUE_OPTIONAL | Refinement of TOI; source confirmed |
| Line assignment (pregame projected) | EXPERIMENTAL | No confirmed official pregame source (Section C open item); usable only once a source is confirmed and review-gated |
| PP unit (pregame projected) | EXPERIMENTAL | Same caveat |
| Teammate effects | EXPERIMENTAL | Requires line-combination history at a maturity this design does not yet establish; defer past V1 |
| Recent role change (rolling TOI/shots trend) | HIGH_VALUE_OPTIONAL | Derivable from #2 alone (no projected-lineup dependency), so more achievable for V1 than raw line/PP projections |
| Opponent shot suppression | REQUIRED_V1 | Derived from official boxscore history only (Section C #11); no external dependency |
| Opponent positional suppression (vs. this position specifically) | HIGH_VALUE_OPTIONAL | Needs larger sample; may be noisy in V1 |
| Goalie context (starter identity/quality) | HIGH_VALUE_OPTIONAL | Depends on Section C #12 pregame confirmation source maturity; SOG is a shot-generation stat largely independent of goalie quality, so this is lower priority than for save-based props |
| Score-state tendency (pregame-derived only, e.g. team's typical game script) | EXPERIMENTAL | Must be constructed only from strictly-prior games, never live score state; high leakage risk if implemented carelessly, so flagged experimental pending a specific leakage-safe design |
| Home/away | REQUIRED_V1 | Trivial, always available |
| Rest/back-to-back | REQUIRED_V1 | Already computed for the team-winner lane; reusable pattern |
| Travel | HIGH_VALUE_OPTIONAL | More complex (distance/time-zone deltas); defer past V1 unless rest/back-to-back alone proves insufficient |
| Pace (team possession/shot-pace context) | HIGH_VALUE_OPTIONAL | Derivable from official data; not required for V1 |
| Team implied game environment (market-derived) | DO_NOT_USE / MARKET_SEPARATION **as a governed model input** | Not temporal leakage in the strict sense (the market data can genuinely be pregame) — excluded on a separate governance ground: V17 requires sporting probability and market evidence to remain independent, so market-implied signals may inform evidence/explanation only, never enter the fitted probability itself |

## H. Failure taxonomy proposal (mapping only, no new codes registered)

Preference order for every condition below: (1) an existing sport-agnostic
canonical code, used as-is; (2) an existing canonical code plus a
structured, non-code `reason` metadata field (e.g. `reason:
"NHL_RECENT_GAMES_INSUFFICIENT"` carried as data on a
`MODEL_INPUTS_INSUFFICIENT` result, not as a new top-level blocker
string); (3) only if neither suffices, a genuinely new top-level code
proposed for explicit review and registration — this document proposes
none at tier 3.

| NHL-SOG condition | Canonical category (existing code, used as-is) | Structured reason metadata (not a new top-level code) |
|---|---|---|
| Unresolved player identity | `PROP_PLAYER_IDENTITY_UNRESOLVED` / `PROP_IDENTITY_UNRESOLVED` (existing, sport-agnostic) | — (existing code is specific enough) |
| Unresolved game identity / cross-provider conflict | `PROP_EVENT_IDENTITY_CONFLICT` (existing) | — |
| Stale lineup status | `MODEL_INPUTS_INSUFFICIENT` (existing) | `reason: "NHL_LINEUP_STATUS_UNRESOLVED"`, mirroring the same relationship `MLB_STARTER_STATUS_UNRESOLVED` has to its category |
| Insufficient historical sample | `MODEL_INPUTS_INSUFFICIENT` (existing) | `reason: "NHL_RECENT_GAMES_INSUFFICIENT"`, mirroring `MLB_RECENT_STARTS_INSUFFICIENT` |
| Missing role/TOI context | `MODEL_INPUTS_INSUFFICIENT` (existing) | `reason: "NHL_ROLE_CONTEXT_INSUFFICIENT"` |
| Post-start hydration attempt | `EVENT_ALREADY_STARTED` (existing, sport-agnostic) | — |
| Unsupported exact line (e.g. period-level SOG in V1) | `LINE_OUTSIDE_CERTIFIED_SUPPORT` (existing, generic form of `MLB_1IP_LINE_OUTSIDE_CERTIFIED_SUPPORT`) | — |
| Fitted artifact unavailable | `MODEL_ARTIFACT_NOT_REGISTERED` / `PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND` (existing) | — this is exactly the code the manifest placeholder deferred in this phase would have returned |

No new top-level code is registered by this document, and this design's
default is that none should be needed for V1 — the existing categories
plus structured reason metadata should cover every condition above. If a
later PR finds a condition that genuinely cannot be expressed this way,
that specific gap requires its own review before being added to
`prop_terminal_reducer_v2.py`'s blocker sets, per the same disjointness
discipline verified for MLB strikeouts and per the V17 process
addendum's requirement that failure codes be registered, not proliferated
ad hoc.

## I. Output summary

1. **Recommended source stack (V1)**: `NHL_PUBLIC_WEB_API` as primary for
   schedule/game/player identity, skater boxscore (once built), and
   derived opponent-suppression rates; `SPORTRADAR_NHL` as the licensed
   fallback/upgrade path; a cross-checked unofficial lineup/line-combo
   source for pregame lineup-confirmation *gating evidence* only (never
   as a silent training feature source without independent boxscore
   corroboration).
2. **Canonical schema**: Section A.
3. **Identity-resolution contract**: Section B, built on the existing
   `IdentityResolution` fail-closed contract.
4. **Source precedence matrix**: Section C.
5. **Freshness SLA table**: Section D.
6. **Training-row schema**: Section F.
7. **Feature snapshot schema**: Section E.
8. **Leakage rules**: Section E (hard rule) + Section G (per-feature
   experimental/do-not-use flags).
9. **Proposed failure mappings**: Section H.
10. **Smallest first coding PR after this design is approved**: **PR 1 —
    NHL canonical identity + historical SOG ingestion**, exactly as
    scoped in the follow-up plan — canonicalization + `SHOTS_ON_GOAL`
    stat mapping + historical player-game SOG rows (building the missing
    skater boxscore table, item F.2) + provenance/timestamps +
    leakage-safe snapshot primitives + tests only, no model, no manifest
    entry, no scorer. This keeps the same narrow vertical-slice discipline
    used for MLB strikeouts and avoids creating a route before the
    underlying data model is proven.

    **PR 1 acceptance criteria, explicitly required, not optional:**
    - Reconcile `NHL_PUBLIC_API` (manifest) vs `NHL_PUBLIC_WEB_API`
      (entitlements/pipeline) into one canonical source ID
      (`NHL_PUBLIC_WEB_API`) before any new skater-data manifest rows are
      added, so PR 1 cannot accidentally create a duplicate logical
      source under a second name.
    - Ingested/persisted historical player×game SOG rows are **research
      evidence only**. `NHL_PUBLIC_WEB_API` remains
      `certification_source_review_required=True` throughout PR 1 — the
      ingestion working, being tested, and being persisted does **not**,
      by itself, mark the source certified or promote it to a trusted
      training source. That promotion is a separate, later, explicitly
      reviewed step, matching how `grants_model_capability: false` is
      already treated elsewhere in `historical_source_manifest_v1.json`.

## Explicitly out of scope for this document

Manifest entry, `NHL_SKATER_SOG_EXPERT` placeholder, fitted artifact,
calibration layer, `/score-pick-request` wiring, settlement, monitoring,
postmortem integration. All deferred to PRs 1–5 as sequenced by the
requester, each independently reviewed.
