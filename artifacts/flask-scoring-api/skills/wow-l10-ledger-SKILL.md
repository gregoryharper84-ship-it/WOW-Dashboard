# Skill: wow.l10-ledger

## Skill Name

**WOW L5/L10 Ledger Builder**

## Short Description

Build exact L5 and L10 game-log ledgers for one or more props across any supported sport, then produce a combined cross-sport ranking by ledger strength. When a request spans multiple sports or multiple props, all ledgers are built in a single response — never spread across turns.

---

## Purpose

This skill answers:

```text
Which of these props has the cleanest recent ledger, and what does the
exact hit-rate history show at this line?
```

It produces a reproducible, source-grounded ledger entry per prop — not a narrative summary. Every row in every ledger must be individually sourced. A row without a verified source is not included.

---

## Governance

```text
WOW_VERSION=WOW_v16_CLEAN_CORE
lane_status=RESEARCH_ONLY_FORWARD_TEST
can_execute=false
stake=0
money_label_allowed=false
final_approval_allowed=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS
```

A ledger entry is research output only. It may not be used as a standalone basis for a FINAL_APPROVED or MONEY_QUALIFIED decision without the full gate pipeline running separately.

---

## Multi-Sport Rule — one response, not multiple turns

**When a request contains props from more than one sport, or more than one player, build every ledger in the same response.** Do not stop after the first sport and wait to be asked for the next one.

Correct sequence:
1. Receive the full prop list (any mix of sports, any number of props).
2. Build all ledgers — one per prop — in a single response.
3. Close the response with a cross-sport combined ranking (see Ranking section below).

Incorrect sequence (do not do this):
- Build WNBA ledgers → stop → wait for "now do MLB" → build MLB ledgers → stop → wait for "give me the top 10."

The underlying ledger work is independent per prop. Nothing about MLB requires the WNBA results first. The combined ranking needs both ledgers to exist — which they do at the end of step 2. There is no reason to split this across turns.

**If a prop list arrives one sport at a time across separate messages,** still close each response by noting whether the combined ranking can now be produced ("All requested ledgers are now built — combined ranking follows" or "Waiting on MLB ledgers before ranking").

---

## Ledger Format

One ledger per prop. Each ledger is a fixed-column table:

```
Player       | Stat  | Line  | Side | Game Date | Opponent | Result | Hit?
-------------|-------|-------|------|-----------|----------|--------|-----
[name]       | [key] | [val] | MORE | YYYY-MM-DD| [opp]    | [X.X]  | ✓/✗
```

Required fields per row:
- **Game Date** — exact calendar date (not "last Tuesday")
- **Opponent** — the opposing team that game
- **Result** — the player's actual stat value that game
- **Hit?** — did the result clear the line (MORE) or stay under (LESS)?
- **Source** — the data source for this row (nba_api, MLB Stats API, BallDontLie, etc.)

At the bottom of each ledger, include:

```
L5 hit rate:  N/5  (NN%)
L10 hit rate: N/10 (NN%)
Data source:  [source name and date pulled]
Gaps:         [any missing rows and why]
```

If fewer than 5 verified rows exist, label it L[n] and note the gap explicitly. Do not pad with estimates.

---

## Combined Ranking (required when ≥ 2 props are built)

After all individual ledgers, append a combined ranking table sorted by ledger strength. Ledger strength = L10 hit rate (primary), L5 hit rate (tiebreaker), sample completeness (second tiebreaker).

```
Rank | Player          | Sport | Prop           | Line | L5    | L10   | Gaps
-----|-----------------|-------|----------------|------|-------|-------|------
1    | [name]          | NBA   | Points MORE    | 27.5 | 4/5   | 8/10  | none
2    | [name]          | MLB   | Hits MORE      | 1.5  | 4/5   | 7/10  | none
...
```

Include every prop from the request, even weak ones — the ranking exists to compare, not to pre-filter. Weak props appear at the bottom with their actual numbers, not omitted.

After the table, add a one-sentence summary of the top 3:

```
Top 3 by ledger strength: [Player A] (NBA, 8/10), [Player B] (MLB, 7/10), [Player C] (WNBA, 7/10 but L5 only 3/5).
```

---

## Source Rules

1. **Use the canonical data source for each sport:** nba_api for NBA/WNBA, MLB Stats API for MLB, nfl-data-py for NFL, Jeff Sackmann ATP/WTA CSV for Tennis.
2. **State the source per ledger, not per row** — one source declaration at the bottom of each ledger is sufficient.
3. **Do not mix sources within a single ledger.** If two sources disagree on a result, use the official stats provider and note the discrepancy.
4. **Stale source = gap.** If the most recent game in the source is more than 48 hours old for an in-season sport, mark the most recent row as potentially stale.

---

## No Web Search for Data Gaps

**Web search is not used to fill missing ledger rows.** This is a hard rule, not a default that degrades gracefully.

If a row is missing from the canonical data source:
- Mark it as a gap in the ledger (`—` in Result, `GAP` in Hit?)
- State the reason (player DNP, game postponed, data source lag, player not in dataset)
- Count it against the sample size (L8, not L10, if 2 rows are missing)

Web search may only be called when **explicitly requested by the user** in the same message ("also check ESPN for that game" / "use web search to fill the gap"). Absent that explicit instruction, gaps stay gaps.

**Why:** a ledger built from a mix of structured API data and web-searched text results is not a reproducible ledger — two runs of the same query can produce different rows. The value of the ledger is that it can be audited against a known source. A web-filled row cannot be audited the same way, and a confidently wrong row is worse than a visible gap.

This applies to all gap types:
| Gap type | Correct handling |
|---|---|
| Missing game (DNP, postponed) | Mark gap, note reason |
| Player not found in data source | LEDGER_UNAVAILABLE for that prop |
| Data source lag (game played but not yet in API) | Mark as pending, note lag |
| Stat not available for that game in the source | Mark gap, do not web-search the box score |
| Historical game outside source's coverage window | Mark gap, note coverage limit |

---

## Failure Modes

| Situation | Correct handling |
|---|---|
| Fewer than 3 verified rows exist | Build the partial ledger, label it L[n], note the gap |
| Player not found in the data source | LEDGER_UNAVAILABLE — state the source checked and the failure reason |
| Conflicting results across sources | Use official source, note conflict in the gaps field |
| Line not confirmed on today's board | Build the ledger against the provided line, flag it as BOARD_LINE_UNVERIFIED |
| Sport not yet supported | LEDGER_UNAVAILABLE — state the unsupported sport and do not fabricate |

A LEDGER_UNAVAILABLE prop still appears in the combined ranking at the bottom, labeled UNAVAILABLE — it is not silently dropped.

---

## What This Skill Does Not Do

- It does not compute hit probability (that is `wow.probability-ev-auditor`).
- It does not run the gate pipeline or assign terminal labels.
- It does not approve, stake, or recommend action.
- It does not fill missing rows with estimates, averages, or "likely" values.
- It does not combine multiple props into a parlay recommendation.

The ledger is an input to those processes, not a substitute for them.
