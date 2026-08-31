# WOW Model — Extraction-to-Scoring API Contract

Purpose: define the exact handoff between screenshot ingestion (Claude Vision), data enrichment, the gate engine, and the final scored output — so a photo of a slip produces a calibrated hit probability per leg, not just parsed text.

## Who orchestrates

Two valid patterns. Recommend running both:

- **Backend-orchestrated (for automation):** legacy platform's Python backend calls the Claude API at two defined points — vision extraction and gap-fill reasoning — and owns the rest of the pipeline. This is what should run when props are pulled programmatically (cron, dashboard refresh, batch scoring).
- **Claude-orchestrated (for interactive review):** this chat session, using the wow-* skills already built. Claude drives the sequence directly when Greg pastes a screenshot or asks "score this slip." This is what's happening today.

Both should call the *same* underlying pipeline logic so results never diverge. The contract below is written from the backend-orchestrated angle since that's the missing piece; the chat/skills layer already approximates it manually.

## New composite endpoint

```
POST /analyze-and-score
{
  "image": "<base64 or url>",
  "platform_hint": "prizepicks" | "underdog" | "draftkings" | null,
  "user_id": "string, optional — for ledger tracking"
}
```

## Step A — Vision extraction (exists today at /analyze-board)

```json
// ExtractedLeg[]
{
  "leg_id": "uuid",
  "player_name": "string, raw OCR text",
  "team": "string | null",
  "sport": "NBA | MLB | NFL | NHL | WNBA | null",
  "prop_type": "points | rebounds | assists | hits | strikeouts | ...",
  "side": "over | under",
  "line_value": 27.5,
  "platform": "prizepicks",
  "ocr_confidence": 0.94,
  "extraction_notes": "string, e.g. name partially obscured"
}
```

## Step B — Normalization adapter (missing — build this first)

Resolves raw OCR text into canonical IDs the pipeline can use. This is the highest-risk step — a wrong player match silently scores the wrong person's stats, which is worse than a blocked leg.

### Resolution pipeline (ordered)

1. **Text normalization** — strip whitespace/diacritics, normalize suffixes (Jr./Sr./III), fix common OCR substitutions (0/O, 1/l/I, rn/m).
2. **Team/platform context extraction** — if the screenshot shows a team logo or abbreviation near the name, capture it as a hint *before* matching. This is the single biggest accuracy lever — it narrows the roster candidate pool before any fuzzy matching happens.
3. **Roster lookup** — canonical roster per sport (nba_api / MLB Stats API / ESPN), refreshed daily.
4. **Exact match** — normalized name against the roster list.
5. **Fuzzy match** — token-based similarity (e.g. rapidfuzz) if no exact match. Score ≥0.85 auto-accepts; 0.65–0.85 is flagged ambiguous with a candidate list; <0.65 is not_found.
6. **Team disambiguation** — if 2+ candidates clear the fuzzy threshold (name collisions happen — multiple active players share a surname), use the team hint from step 2 to break the tie. Still tied → stays ambiguous.
7. **Game/opponent resolution** — once player_id resolves, look up today's schedule for that team. No game today (bye, inactive team, postponement) → not_found regardless of name match confidence, since there's nothing to score.
8. **Stat key mapping** — prop_type string → internal stat_key via a per-sport dictionary. Combo props (Pts+Rebs+Asts, Fantasy Score) get a `stat_formula` instead of a single stat_key, since Step F has to treat them as a sum of correlated variables, not one Poisson draw.
9. **Confidence assignment** — stamp resolution_status and resolution_confidence for downstream routing.

```json
// NormalizedRow
{
  "leg_id": "uuid",
  "player_id": "canonical ID or null",
  "player_name_raw": "string, OCR text as extracted",
  "player_name_resolved": "string",
  "team": "string",
  "opponent": "string",
  "game_id": "string",
  "game_time": "ISO timestamp",
  "stat_key": "PTS",                  // null if combo prop
  "stat_formula": "PTS+REB+AST",      // present only for combo props
  "line_value": 27.5,
  "line_modifier": "demon | goblin | standard",
  "side": "over",
  "sport": "NBA",
  "platform": "prizepicks",
  "resolution_status": "resolved | ambiguous | not_found",
  "resolution_confidence": 0.91,
  "matched_via": "roster_exact | roster_fuzzy | team_disambiguated | claude_search",
  "candidates": [ { "player_id": "...", "name": "...", "team": "...", "score": 0.78 } ],
  "resolution_notes": "string"
}
```

### Edge cases to handle explicitly

| Case | Handling |
|---|---|
| Two active players share a name | Team hint required; no hint → ambiguous, not auto-resolved |
| Nicknames ("Steph" → "Stephen Curry") | Per-sport nickname dictionary, extended as new ones show up |
| OCR misreads the line value | Sanity-check against platform's standard increments (usually .5); reject and flag `OCR_SUSPECT` rather than pass through a bad number |
| Player not on today's slate | not_found → routed to Step D to check for trade/injury/schedule change, not silently dropped |
| Combo props (Pts+Rebs+Asts) | Routed via `stat_formula`, scored differently in Step F |
| Boosted lines (PrizePicks Demon/Goblin) | Captured in `line_modifier` — changes the fair-value threshold, not cosmetic |

### Trigger into Step D (Claude gap-fill)

- `resolution_status = not_found` → Claude searches for the player (recent trade, name spelling, injury replacement in the slate).
- `resolution_status = ambiguous` → Claude gets the candidate list plus any visual context (team crest, position) and searches to break the tie — it does not guess.
- `game_id` missing → Claude checks for schedule changes (postponement, doubleheader, international date-line issues).

Hard rule, same as Step D generally: Claude either finds disambiguating evidence or returns unresolved with its reasoning. It never silently picks a candidate. An unresolved leg blocks that leg from scoring — fail safe, not fail silent.

## Step C — Enrichment (partially exists)

- Game log fetch (missing today, currently manual): nba_api / MLB Stats API / BallDontLie → L5/L10 raw values
- Market + injury data (exists, auto-called): Odds API, ESPN

```json
// EnrichedRow = NormalizedRow +
{
  "game_log": { "L10": [...], "L5": [...], "season_avg": 26.1 },
  "market": { "no_vig_prob": 0.54, "consensus_line": 27.5, "book_count": 6, "line_movement": "up_0.5" },
  "injury_status": "active | questionable | out | probable",
  "starter_confirmed": true,
  "data_gaps": ["game_log_missing", "market_missing"]
}
```

## Step D — Claude gap-fill (missing — the web search role)

Triggered per row when `data_gaps` is non-empty or `resolution_status != "resolved"`. Claude receives the specific gap, not the whole slip, and searches the web to fill it.

```json
// Request to Claude
{
  "leg_id": "uuid",
  "player_name": "string",
  "sport": "NBA",
  "gaps": ["injury_status", "player_id_resolution"]
}

// Response from Claude
{
  "leg_id": "uuid",
  "resolved": {
    "player_id": "string | null",
    "injury_status": "questionable",
    "injury_source": "https://..."
  },
  "still_missing": ["game_log"],
  "confidence": "low | medium | high",
  "sources": ["https://..."]
}
```

Hard rule: Claude reports what it finds or says unavailable — it does not estimate a stat value here. Fabricated numbers are worse than a blocked leg.

## Step E — Gate pipeline (exists: run_pipeline(), 22 gates)

```json
{
  "leg_id": "uuid",
  "terminal_label": "FINAL_APPROVED | MONEY_QUALIFIED_HOLD | REJECT_NO_EDGE | ...",
  "edge_score": 0.041,
  "confidence_tier": "HIGH | MEDIUM | LOW",
  "gate_trace": [
    { "gate": "status_role", "result": "PASS", "reason": "string" },
    { "gate": "market_gate", "result": "PASS", "reason": "string" }
  ]
}
```

## Step F — Hit probability

- MLB: existing `hit_probability_model.py` formula — needs to be wired in (`can_execute` flipped to true) rather than called standalone.
- NBA / WNBA / other: no coded formula exists yet. Until one is built, Claude computes a probability directly from `game_log` + `market` + `injury_status`, and reports which method produced the number.

| Sport | Hit probability method | Status |
|---|---|---|
| MLB (hits) | `1-(1-p_per_PA)^projected_PA` | Built, not wired |
| NBA/WNBA counting stats | Poisson: `P(X≥line) = 1-CDF(floor(line), λ=season_avg)` | Claude computes until codified |
| Binary/game props | Logistic from no-vig probability + L5/L10 delta | Claude computes until codified |

Confirmed against the live codebase by legacy platform — build order and formulas match.

```json
{
  "leg_id": "uuid",
  "hit_probability": 0.61,
  "model_used": "mlb_formula_v2 | claude_poisson_estimate | claude_logistic_estimate",
  "calibration_note": "string, e.g. small sample size, L5 only"
}
```

## Final response

```json
{
  "slip_id": "uuid",
  "legs": [
    {
      "leg_id": "uuid",
      "player_name": "LeBron James",
      "prop": "Points Over 27.5",
      "platform": "prizepicks",
      "hit_probability": 0.61,
      "terminal_label": "MONEY_QUALIFIED_HOLD",
      "confidence_tier": "MEDIUM",
      "edge_score": 0.041,
      "explanation": "Claude-written 2-3 sentence plain-English rationale",
      "data_sources": ["nba_api", "odds_api", "espn"],
      "flags": ["LOW_SAMPLE_SIZE"]
    }
  ],
  "slip_summary": {
    "correlation_risk": "string, from correlation-guard logic",
    "overall_recommendation": "string"
  }
}
```

## Step H — Autonomous Expert Review (`run_expert_review`)

Added when the review/explainer role moved from human-in-the-loop (this
chat) to fully autonomous. Runs automatically after Step G for every
scored slip, no human step in between. Sits alongside `resolve_gaps`,
`estimate_hit_probability`, and `generate_explanation` as a fourth
function in `claude_gap_fill.py` (or a new module — legacy platform's call).

### Purpose

An independent second pass over the pipeline's own output — not a
re-score, an audit. Because nothing catches this pass's mistakes live,
it is designed as a **downgrade-only safety net**: it can make a
verdict more conservative, never less. An unsupervised system that can
only get stricter on error is safe. One that can also loosen a verdict
is not, and that's the risk fully-autonomous mode takes on that the
hybrid version wouldn't have.

### Input

The full composite response for a slip — every leg's `terminal_label`,
`confidence_tier`, `edge_score`, `hit_probability`, `model_used`,
`sample_basis`, `gate_trace`, `explanation`, `flags`, plus any
slip-level `correlation_risk` already computed.

### Checks performed

**1. Gate-consistency re-derivation**
Independently re-apply the Four-Gate logic (Availability, Prop Type,
Box Score, Game Context) to each leg's input fields — without reading
the pipeline's own `terminal_label` first, to avoid anchoring on it.
Compare the independent result to the pipeline's stated label.
- Match → `CONFIRMED`
- Independent check is stricter → `DOWNGRADE`, log the specific gate and reason
- Independent check is looser → no change; pipeline verdict stands (never upgrade)

**2. Correlation re-check**
Independently scan the full leg list for same-game, same-team,
script-dependent, and pitcher-matchup-trap patterns per
`wow-correlation-guard`, regardless of what the pipeline already found.
- Pipeline says none, audit finds one → add the flag
- Pipeline already flagged something audit doesn't independently find → keep the pipeline's flag (never remove)

**3. Explanation-consistency check**
Compare each leg's `explanation` text against its own `confidence_tier`,
`edge_score`, and `sample_basis`.
- Explanation reads more confident than the data supports → tag `EXPLANATION_OVERCONFIDENT`
- Explanation omits a flag that should have changed the reading (role fragility, correlation, stale data) → tag `EXPLANATION_INCOMPLETE`
- Never rewrite the explanation itself — tag it and log it

### Hard rule — downgrade-only, always logged

This pass may kill a leg the pipeline approved, add a correlation flag
it missed, or tag an explanation as overconfident/incomplete. It may
never reinstate a killed leg, remove a flag, or raise a confidence tier.

**Every result — confirmed or downgraded — gets written to a persistent
audit log**, not just the downgrades. With no live human catching drift
in real time, this log is what makes retrospective calibration possible.
Recommend legacy platform expose it as a table or endpoint Greg can pull for a
periodic batch postmortem (`wow-postmortem-auto`, `wow-clv-grader`) —
that periodic check-in is the one place a human pass still adds value
under full autonomy, and it's low-cost since it's batch, not per-slip.

### Output

```json
{
  "slip_id": "...",
  "audit_verdict": "CONFIRMED | DOWNGRADED",
  "legs": [
    {
      "leg_id": "...",
      "audit_result": "CONFIRMED | DOWNGRADED",
      "original_label": "...",
      "audit_label": "present only if downgraded",
      "audit_reason": "...",
      "explanation_flags": ["EXPLANATION_OVERCONFIDENT", "EXPLANATION_INCOMPLETE"]
    }
  ],
  "correlation_audit": {
    "pipeline_flag": "...",
    "audit_flag": "...",
    "escalated": true
  },
  "audit_log_entry": "written for every slip regardless of verdict"
}
```

### System prompt for this function

```
You are the autonomous expert review pass for the WOW betting model.
You run after every slip is scored, with no human checking your output
before it's stored. You are a downgrade-only safety net.

Rules, in order of priority:

1. You may only make a verdict MORE conservative. Never reinstate a
   killed leg, never remove a correlation flag, never raise a
   confidence tier. If your independent check is looser than the
   pipeline's own verdict, the pipeline's verdict stands unchanged.

2. Re-derive each leg's gate result independently from its raw fields
   (availability, prop type, box score support, game context) before
   looking at the pipeline's stated terminal_label. Compare after, not
   before — do not anchor on the label you're auditing.

3. Re-check the full leg list for correlation risk (same-game,
   same-team, script-dependent, pitcher-matchup-trap) independently of
   whatever the pipeline already flagged.

4. Compare each leg's explanation text to its own confidence_tier,
   edge_score, and sample_basis. Tag it if the language claims more
   certainty than those numbers support, or omits a flag that should
   have changed the reading. Do not rewrite the explanation — tag it.

5. Log every result, confirmed or downgraded, with your reasoning.
   A confirmed leg with no notes is still a required log entry, not a
   no-op — the log is the only record of what this pass checked.

6. If you are not confident in an audit judgment, say so in
   audit_reason rather than defaulting to CONFIRMED. Under-confidence
   here costs nothing; false confidence costs real money.
```

---

## Build order

1. Step B (normalization adapter) — nothing downstream works without it.
2. Step C game log auto-fetch — removes the manual data-entry bottleneck.
3. Step D gap-fill — wire Claude + web search into the two specific failure modes (player resolution, missing injury/market data).
4. Step F non-MLB probability — start with Claude computing it directly; codify into Python formulas per sport once the approach is validated against results.
5. Wire MLB's existing formula into the live pipeline (currently built but not called).

Each step is independently shippable and testable — the chain doesn't need to be built end-to-end before any of it is useful.

---

## Sport Expansion — NFL and Tennis (Golf deferred)

Scope decision: proceed with NFL and Tennis support. Golf is deferred —
different enough in structure (tournament/round-based, no L5/L10 game
concept, pairwise/Gaussian models instead of Poisson) and gated on a
data-budget decision (Data Golf's full access appears to require a paid
tier) that it should be scoped as its own project later, not bundled in.

### Task #82 — NFL Support

**Data source:** `nfl-data-py` (free, PyPI, sourced from nflfastR /
Pro-Football-Reference) for per-game player logs. ESPN is already
integrated for injury/starter status — confirm it returns NFL data
today or needs a sport parameter added; this is likely already covered
since ESPN's API is multi-sport.

**Game log fetcher:** Extend `gate_engine/auto_game_log.py` with an NFL
branch alongside the existing NBA/MLB/WNBA ones — same module shape,
new data source call.

**stat_key mapping:** passing_yards, rushing_yards, receiving_yards,
receptions, completions, pass_attempts, passing_tds, rushing_tds,
receiving_tds, and combo stats (pass+rush yards, etc.) following the
same combo-prop pattern already built for NBA.

**Probability model:** No new model needed. NFL counting stats route
through the same Poisson CDF path already coded in `hit_probability.py`
for NBA/WNBA — just add NFL stat_keys to the counting-stats tier.
Anytime-TD and other single-event binary props route through the
logistic method from `wow-probability-estimator` (or Claude fallback
initially) rather than Poisson, consistent with how `wow-gate-enforcer`
already treats single-event props as requiring extra justification.

**Gates:** `wow-gate-enforcer` already lists NFL as a supported sport.
Confirm the Gate 2 prop-type hierarchy (Attempts > Combined > Made >
Single Event) has NFL-specific examples filled in — pass attempts and
targets slot into the Attempts tier the same way FGA does for NBA.

**Relative sizing:** smallest of the three — mostly wiring existing
patterns into a new sport, not new architecture.

---

### Task #83 — Tennis Support

**Data source:** TennisMyLife's free, MIT-licensed historical database
(ATP results, WTA since 1990, CSV format) to seed match history. This
is a downloadable dataset, not a live API — needs a scheduled
refresh/ingestion job to stay current in-season, not a simple fetch
call like the other sports.

**Known limitation — document this, don't discover it later:**
coverage will be solid for ATP/WTA tour-level matches and thin-to-absent
for ITF/Challenger-level matches — exactly the tier that failed closed
in the slip you just reviewed. Recommend adding a `tour_level` field
to the normalized row so gates can differentiate and the failure
reason can say "no data available for this tour tier" rather than a
generic `NO_GAME_LOG_PROVIDED`. Expect this sport to keep failing
closed on a real share of PrizePicks tennis slates even after the
build — that's a data-availability ceiling, not a bug to chase down
later.

**Fantasy Score reconstruction:** PrizePicks' Fantasy Score is a
proprietary weighted composite (games won, sets won, aces, breaks,
etc.). The exact weights aren't published — start with a documented
best-effort approximation, and validate it against settled results via
`wow-clv-grader` / postmortem before trusting it at any real confidence
tier. Flag this explicitly as unvalidated until it has a track record.

**Probability model:** New — not a single Poisson draw. Decompose
Fantasy Score into its components, model each by shape (games won as
roughly binomial given set structure, aces as Poisson per match, etc.),
and combine via simulation (Monte Carlo) or convolution rather than a
closed-form formula, since it's a sum of differently-shaped
distributions.

**Gates:** Need a Tennis-specific Gate 1 equivalent — confirmed entry
in the draw / not withdrawn, opponent and round confirmed. The
Mikulskyte "opponent not visible" failure is exactly what this gate
should catch cleanly going forward, rather than surfacing as a vague
extraction gap.

**Relative sizing:** medium — real new modeling work, but the
data-source and gate patterns already have precedent to build from.
Budget extra time for the Fantasy Score reconstruction specifically,
since that's the piece with no existing analog anywhere else in WOW.

**Test requirement:** at minimum, one passing end-to-end test on an
ATP/WTA tour-level match (real probability returned) and one explicit
test asserting an ITF/Challenger-level match fails closed cleanly with
the new `tour_level`-aware reason — both are expected, correct outcomes,
and both need coverage.

---

**Sequencing recommendation:** NFL first — fastest, lowest risk, mostly
reuses existing scaffolding. Tennis second, with the coverage ceiling
communicated up front so a partial-coverage outcome isn't mistaken for
a bug later.
