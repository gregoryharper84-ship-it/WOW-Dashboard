# WOW Model — Extraction-to-Scoring API Contract

Purpose: define the exact handoff between screenshot ingestion (Claude Vision), data enrichment, the gate engine, and the final scored output — so a photo of a slip produces a calibrated hit probability per leg, not just parsed text.

## Who orchestrates

Two valid patterns. Recommend running both:

- **Backend-orchestrated (for automation):** Replit's Python backend calls the Claude API at two defined points — vision extraction and gap-fill reasoning — and owns the rest of the pipeline. This is what should run when props are pulled programmatically (cron, dashboard refresh, batch scoring).
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

Confirmed against the live codebase by Replit — build order and formulas match.

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

## Build order

1. Step B (normalization adapter) — nothing downstream works without it.
2. Step C game log auto-fetch — removes the manual data-entry bottleneck.
3. Step D gap-fill — wire Claude + web search into the two specific failure modes (player resolution, missing injury/market data).
4. Step F non-MLB probability — start with Claude computing it directly; codify into Python formulas per sport once the approach is validated against results.
5. Wire MLB's existing formula into the live pipeline (currently built but not called).

Each step is independently shippable and testable — the chain doesn't need to be built end-to-end before any of it is useful.
