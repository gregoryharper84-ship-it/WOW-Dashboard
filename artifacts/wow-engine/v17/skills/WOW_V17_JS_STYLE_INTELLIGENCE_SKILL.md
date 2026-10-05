# WOW V17 JS Style Intelligence Skill

skill_id=WOW_V17_JS_STYLE_INTELLIGENCE
status=RESEARCH_ONLY
lane=DISCOVERY_AND_CONSTRUCTION_INTELLIGENCE
owner=WOW_BETTING_ENGINE
probability_authority=CONTROLLING_SPECIALIST_ONLY
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false

## Purpose

Learn and operationalize the recurring "JS Style" prop-selection pattern as a V17-native research overlay without reviving the legacy v16 JS patch as probability authority.

The overlay asks:

- Does the posted threshold require elevated opportunity, role, workload, efficiency, or game environment to beat?
- Does ordinary deployment favor the selected side?
- Is the candidate supported by a coherent opponent/game-script thesis?
- Are multiple same-event legs expressing one thesis, conflicting theses, or unresolved shared fragility?

The overlay never creates or changes sporting probability.

Hard invariant:

JS_STYLE_SIGNAL != GOVERNED_MODEL_PROBABILITY

## Primary archetypes

1. JS_OPPORTUNITY_CEILING_LESS
   - LESS wins when ordinary opportunity volume remains below the threshold.
   - Examples of opportunity families: attempts, touches, routes, minutes, time on ice, shots faced, goalkeeper saves, clearances, passes attempted.

2. JS_ROLE_CEILING_LESS
   - The posted line implies more role/minutes/usage than the current role normally supports.

3. JS_COMPOSITE_CEILING_LESS
   - PRA, rebounds+assists, fantasy score, or similar composite totals where total involvement must run hot to clear.

4. JS_MATCHUP_SUPPRESSION_LESS
   - Opponent/style/game environment suppresses the exact opportunity or stat path.

5. JS_WINDOW_LESS
   - First-half, second-half, period, or other shortened-window line whose threshold is demanding relative to expected exposure.

6. JS_GAME_SCRIPT_LESS
   - The LESS benefits from a plausible script such as blowout minute compression, low pass volume, possession suppression, low pace, or low shot volume.

7. JS_DEFENSIVE_VOLUME_LESS
   - Defensive/volume markets such as goalie saves, goalkeeper saves, clearances, tackles, passes attempted, and time on ice where opportunity generation is the main driver.

8. JS_FLOOR_MORE
   - A low threshold with multiple ordinary paths to clearing it.

9. JS_PROMO_ANCHOR
   - A materially reduced/promo threshold. This is a research/construction tag only and never bypasses line-shape, binary-event, specialist, calibration, or terminal governance.

## Research-priority score

The initial v1 score is intentionally heuristic and research-only:

- threshold asymmetry: 25
- opportunity ceiling: 20
- matchup suppression: 15
- distribution support: 15
- stat-path robustness: 10
- window fit: 5
- game-thesis coherence: 10

Total = 0-100 research priority.

This score is not a probability, edge, lower bound, approval label, or execution signal.

## Opportunity Burden Ledger

Where evidence exists, retain:

- exact line and selected side;
- role-adjusted median;
- robust dispersion;
- threshold burden z-score;
- expected minutes/exposure;
- expected opportunity distribution;
- opportunity volume required to beat the line;
- minutes/usage/opportunity stability;
- matchup suppression evidence;
- game-script dependency;
- period/window fit;
- stat-path robustness;
- cluster thesis and shared driver.

Missing fields remain missing. Do not invent them.

## Same-event thesis clustering

Group same-event candidates for portfolio research only.

Allowed dependence labels:

- THESIS_COHERENT
- THESIS_NEUTRAL
- THESIS_CONFLICTING
- SHARED_FRAGILITY
- UNRESOLVED_DEPENDENCE

A coherent thesis may raise research interest, but it does not increase any leg's governed sporting probability.

Unresolved/dependent legs must not receive a fabricated correlation coefficient or independent joint probability.

## Current-selection integrity

Current JS candidate scoring is pregame-only.

Reject current-selection scoring when:

- event is LIVE / STARTED / FINAL / SETTLED;
- live current values are injected as features;
- official result, settled value, or other postgame fields are supplied as pregame evidence.

Historical examples may be stored for learning, but pregame features and outcomes must remain separated.

If no immutable pregame feature snapshot exists:

feature_replay_required=true

Do not reverse-engineer pregame features from the result.

## Historical learning

Store both winning and losing JS examples.

Do not train a "winning-pick model" from selected winners only.

Preferred future challenger target:

P(JS selects candidate | pregame board features)

That selector model remains separate from:

P(prop wins | governed fitted sporting model)

A fitted JS selector requires positive and negative board examples. Profitable screenshots alone are insufficient because they do not reveal all rejected rows.

## Workflow integration

### PrizePicks board

full readable board
-> exact row extraction
-> optional JS annotation on every eligible pregame candidate
-> canonical MORE/LESS expansion where required
-> exact controlling sport/stat specialist
-> governed calibration/lower bound
-> verified pool
-> JS construction/context overlay
-> dependency/weakest-leg governance
-> V17 terminal reducer

JS may prioritize research or explain why a row resembles the observed style.

JS may not:
- suppress non-JS rows from Full Model scoring;
- elevate an unsupported row;
- modify model probability;
- modify calibrated probability;
- modify calibrated lower bound;
- override a typed blocker;
- override current-board refresh.

### Scout

Scout discovery remains additive.

JS Style Intelligence may annotate Scout prop candidates after canonicalization/research enrichment and before handoff. It does not become the controlling specialist.

## User-facing output

When useful, expose:

- JS archetype(s)
- JS research priority
- threshold burden
- opportunity/role ceiling note
- shared game thesis
- dependence type

Keep these fields visually and semantically separate from governed model probability.

## Regression requirements

1. Opportunity-ceiling LESS can receive high JS research priority while all probability fields remain null.
2. Composite LESS can be tagged without changing calibration.
3. Floor MORE/promo-anchor remains research-only.
4. Live/final rows cannot be current JS-selection candidates.
5. Postgame values cannot feed pregame JS scoring.
6. Same-event coherent clusters do not manufacture joint probability.
7. Mixed-driver same-event clusters are unresolved dependence.
8. Historical examples without pregame snapshots require replay.
9. Full-board scoring still processes non-JS rows.
10. V17_TERMINAL_REDUCER remains sole terminal authority.
11. can_execute=false remains invariant.
