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
- Two source entries for NHL already exist in
  `historical_source_manifest_v1.json`: `NHL_PUBLIC_API` (candidate,
  `LICENSE_REVIEW_REQUIRED`) and `SPORTRADAR_NHL` (`CONTRACT_REQUIRED`,
  coverage from 2013). Neither `grants_model_capability`. This design
  extends that same manifest with skater-shot-specific rows rather than
  inventing a parallel one.
- `v17/model_source_entitlements.py` already registers
  `NHL_PUBLIC_WEB_API` as `CANDIDATE_FIRST_PARTY_UNDOCUMENTED` with
  `certification_source_review_required=True`. Any new NHL skater-data
  source added under this design inherits the same review gate — no
  source becomes trusted for training just by being named here.
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
| `season` | string | `"YYYY-YYYY"` matching `nhl_candidate_pipeline.py`'s `season_id` convention (e.g. `20262027`), never a bare year |
| `game_id` | string | NHL official `gamePk`/`id` from `api-web.nhle.com`, stored verbatim as string (never re-derived or guessed); a third-party provider's own game ID is a separate `provider_game_id`, never substituted here |
| `game_start_time` | ISO-8601 UTC | source-provided; never client-supplied, never inferred from local scoreboard time |
| `home_team_id` / `away_team_id` | string | official NHL team abbreviation from the existing `TEAM_ABBREVIATIONS` table; relocated/renamed franchises (Arizona→Utah) resolve to the **current** franchise ID for post-relocation games and the **historical** ID for pre-relocation games — never unified retroactively |
| `team_id` | string | the skater's team for *this* game (handles in-season trades; never the season-opening team) |
| `player_id` | string | official NHL player ID (numeric, from the same public API family used for game IDs) |
| `player_name` | string | display name is evidence/labeling only, never an identity key; see Section B |
| `position` | enum | `C`/`LW`/`RW`/`D` (goalies excluded from this vertical entirely — SOG is a skater-only stat) |
| `stat_type` | const | canonical value `SHOTS_ON_GOAL` (see alias table below) |
| `line` | decimal | exact numeric line as offered, 0.5-increment typical; stored at source precision, never rounded |
| `direction` | enum | `MORE` / `LESS`, matching the existing cross-sport convention in `prop_distribution_contract.py` |
| `platform` | string | e.g. `PRIZEPICKS`, or `UPLOADED_BOARD` for manually supplied boards |
| `offer_type` | string | e.g. `STANDARD`, `FLEX`, `GOBLIN`/`DEMON` equivalents if platform-specific line adjustments exist — captured but never silently folded into `line` |
| `observed_at` | ISO-8601 UTC | when the offer/data point was actually retrieved (source-clock or ingestion-clock, whichever is source-verifiable) |
| `as_of` | ISO-8601 UTC | the caller-declared evaluation instant for this request (matches `PropInferenceRequest.as_of_timestamp`) |
| `source` | string | the specific provider/source ID (see Section C); never a generic label like `"web"` |

**Canonical stat alias resolution** (all must map to `SHOTS_ON_GOAL`, case/whitespace-normalized before lookup):
`"Shots on Goal"`, `"SOG"`, `"Shots"`, `"Player Shots on Goal"`,
`"shots_on_goal"`. Any alias not in this explicit table is a typed
blocker (`PROP_STAT_ALIAS_UNRECOGNIZED`), never a best-guess match —
"shots" alone is ambiguous with "shot attempts"/Corsi and must not be
silently coerced.

**Canonical prop key** (mirrors the existing MLB exact-line identity
shape used for settlement matching): `(league, game_id, player_id,
stat_type, line, direction, period)` where `period` is fixed to
`FULL_GAME` for V1 (see Section D on why period-level SOG, e.g.
1st-period-only offers, is out of scope for V1 and must fail closed as
`LINE_OUTSIDE_CERTIFIED_SUPPORT` rather than silently scored as full-game).

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
- **Team abbreviations**: resolved through the existing
  `TEAM_ABBREVIATIONS` table in `nhl_candidate_pipeline.py`, extended
  (not replaced) if a new source uses different abbreviations (e.g.
  three-letter vs. city-based) — a translation table per source, mapping
  into the one canonical set.
- **Relocated/renamed franchises**: Arizona Coyotes → Utah (already
  represented as `ARI`/`UTA` in the existing table) is the only currently
  relevant case; historical games keep the historical team ID, matching
  how the existing D1 pipeline already treats it. Any future
  relocation/rename follows the same pattern: add the new ID, never
  rewrite historical rows.
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
| Market/PrizePicks line | `observed_at` within the platform's normal refresh cadence | Line stale relative to a since-changed official board | Line confirmed removed/changed at final board refresh (Section 17 of the audit) → existing settlement/market blockers apply (`PRICE_STALE`, `MARKET_DATA_UNAVAILABLE`) |

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
  *dataset*, but at *inference* time their row must fail closed via
  `MODEL_INPUTS_INSUFFICIENT` (proposed `NHL_RECENT_GAMES_INSUFFICIENT`)
  rather than being scored on a league-average fallback that would look
  like fabricated evidence.
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
| Team implied game environment (market-derived) | DO_NOT_USE / LEAKAGE **as a governed model input** | Per report and CLAUDE.md: market context may inform evidence/explanation, never enter the fitted probability itself |

## H. Failure taxonomy proposal (mapping only, no new codes registered)

| Proposed NHL-SOG condition | Canonical category | Existing code to reuse, or proposed new code (mirroring an existing MLB pattern) |
|---|---|---|
| Unresolved player identity | MODEL_INPUTS_INSUFFICIENT (pre-model) | Reuse `PROP_PLAYER_IDENTITY_UNRESOLVED` / `PROP_IDENTITY_UNRESOLVED` (existing, sport-agnostic) |
| Unresolved game identity / cross-provider conflict | MODEL_INPUTS_INSUFFICIENT (pre-model) | Reuse `PROP_EVENT_IDENTITY_CONFLICT` (existing) |
| Stale lineup status | MODEL_INPUTS_INSUFFICIENT | Propose `NHL_LINEUP_STATUS_UNRESOLVED`, mirroring `MLB_STARTER_STATUS_UNRESOLVED` |
| Insufficient historical sample | MODEL_INPUTS_INSUFFICIENT | Propose `NHL_RECENT_GAMES_INSUFFICIENT`, mirroring `MLB_RECENT_STARTS_INSUFFICIENT` |
| Missing role/TOI context | MODEL_INPUTS_INSUFFICIENT | Propose `NHL_ROLE_CONTEXT_INSUFFICIENT` (no direct MLB analogue; closest precedent is opponent-context being evidence-only when absent, per `prop_model_adapters.py`) |
| Post-start hydration attempt | EVENT blocker | Reuse `EVENT_ALREADY_STARTED` (existing, sport-agnostic) |
| Unsupported exact line (e.g. period-level SOG in V1) | Pre-model model-contract rejection | Reuse `LINE_OUTSIDE_CERTIFIED_SUPPORT` (existing, generic form of `MLB_1IP_LINE_OUTSIDE_CERTIFIED_SUPPORT`) |
| Fitted artifact unavailable | MODEL_UNAVAILABLE | Reuse `MODEL_ARTIFACT_NOT_REGISTERED` / `PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND` (existing) — this is exactly the code the manifest placeholder deferred in this phase would have returned |

No new code is registered by this document. Any code marked "propose"
above requires review before being added to
`prop_terminal_reducer_v2.py`'s blocker sets in a later PR, per the same
disjointness discipline verified for MLB strikeouts.

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

## Explicitly out of scope for this document

Manifest entry, `NHL_SKATER_SOG_EXPERT` placeholder, fitted artifact,
calibration layer, `/score-pick-request` wiring, settlement, monitoring,
postmortem integration. All deferred to PRs 1–5 as sequenced by the
requester, each independently reviewed.
