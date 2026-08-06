---
name: WNBA acquisition contract repair
description: Four integration bugs fixed in WOW-PATCH-2026-08-06-WNBA-ACQUISITION-CONTRACT-REPAIR; key behaviors to preserve in future work.
---

# WNBA Acquisition Contract Repair

## BUG-001 — game_log alias in build_packet
**Rule:** `build_packet()` must accept BOTH `enr["box_score_log"]` and `enr["game_log"]`
with precedence box_score_log > game_log > [].  The scan flow delivers rows under
`"game_log"`, not `"box_score_log"`.

**Why:** Before fix, `box_score_log_raw = enr.get("box_score_log") or []` always produced
an empty list when the scan flow used the `"game_log"` key → l5/l10 always empty.

**How to apply:** Any new enrichment provider that delivers rows must use one of these two
canonical keys.  `box_score_audit.source_input_key` records which was consumed.

## BUG-002 — single-stat "stat" key normalization
**Rule:** `reconstruct_raw_ledger_rows(box_score_log, market_type=None)` maps
`row["stat"]` → the correct ledger field using `_MARKET_TO_STAT_KEY[market_type]`.
Never infer market from the numeric value.  Unsupported market_type → `stat_mapping_unresolved=True`.

**Why:** `services/player_logs.py` returns rows shaped as `{date, opponent, stat, line, hit}`
(single-stat, generic key).  The reconstructor only looked for `PTS/REB/AST` keys → all
canonical fields were None for every player_logs.py row.

**How to apply:** Any call to `reconstruct_raw_ledger_rows()` from the fallback router
(step 2/3 of `_attempt_box_score_log`) must pass `market_type=packet.get("market") or None`.
`build_packet()` already passes `market_type_ctx = market or None`.

**Registry location:** `gate_engine/wnba/acquisition_packet._MARKET_TO_STAT_KEY` — add new
markets here when new prop types are onboarded; do NOT infer from value.

## BUG-003a — Odds API credential bypass
**Rule:** `fetch_market_comparison()` must use `resolve_odds_api_key_with_source()` from
`services.odds_api` (lazy import inside the function).  No direct `os.environ.get("ODDS_API_KEY")`
in `external_adapters.py` is allowed — verified by test 37 (source-inspection invariant).

**Why:** `ODDS_API_KEY` contains a deactivated credential (HTTP 401).  Priority ladder:
ODDS_API_PAID_KEY → ODDS_API_FREE_KEY → ODDS_API_KEY (legacy).  The adapter was bypassing
the ladder and always picking the dead key.

**How to apply:** All Odds API consumers in the codebase must import and call
`resolve_odds_api_key_with_source()`.  The audit fields `credential_source_name` and
`credential_resolver_used=True` must appear in `normalized_fields` on all return paths
(including REQUEST_EMPTY — player not found in any prop market).

## BUG-003b — ESPN v2 athlete search
**Rule:** `_espn_search_wnba_athlete()` must use `https://site.api.espn.com/apis/search/v2`
with params `{"query": name, "limit": 5, "type": "player"}`.  Parse via uid (contains `~a:`)
and validate `description.upper() == "WNBA"`.

**Why:** The old v3 endpoint (`site.web.api.espn.com/apis/common/v3/search`) returned 0
athlete hits for every WNBA player tested.  The v2 endpoint (used by `services/player_logs.py`)
correctly resolves both A'ja Wilson (id=3149391) and Aliyah Boston (id=4066407).

**How to apply:** Do not re-introduce the v3 URL.  WNBA league validation from `description`
field is mandatory — prevents NBA/NCAAW name collisions.  HTTP 200 with 0 WNBA matches →
REQUEST_EMPTY (ATHLETE_NOT_FOUND), never REQUEST_FAILED.

## Test suite
28 original tests (unchanged) + 14 new regression tests (29–42) = 42 total.
All 42 pass.  Test 37 is a source-code inspection invariant (grep for direct ODDS_API_KEY read).

## Live proof (2026-08-06)
- Before: box_score_log=0, l5=0, l10=0, ODDS_API_KEY=LEGACY(401), ESPN v3 → 0 athletes
- After:  box_score_log=10, l5=5, l10=10, ODDS_API_PAID_KEY used, A'ja Wilson resolved (id=3149391)
