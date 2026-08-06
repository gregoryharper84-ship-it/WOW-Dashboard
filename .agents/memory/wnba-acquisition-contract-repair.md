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

## BUG-005 — ESPN scoreboard team-name tokenization (event_status resolver)
**Rule:** `fetch_event_status()` must tokenize each team's `displayName` into individual
words before set-intersecting against game_str tokens.  "indiana fever" as a whole-string
token NEVER matches the word "indiana" — overlap is always 0 → REQUEST_EMPTY.  After fix:
"Indiana Fever" → {"indiana", "fever"}.

**Why:** The pre-fix code built `all_tokens = team_abbrs | team_names` where `team_names`
contained full display strings.  Set intersection compared individual tokens against whole
strings → zero overlap for every game → event_status stayed None → PACKET_INCOMPLETE_REJECTED.

**How to apply:** Any future adapter that matches by team name must tokenize into individual
words (split on spaces, len≥2).  Never intersect single words against multi-word strings.

**Ambiguity guard:** If two events on the same date share the same max-overlap score,
return REQUEST_FAILED with `event_status="EVENT_MATCH_AMBIGUOUS"` and `ambiguous_event_ids`.
A broad first-event fallback is explicitly prohibited.

**STATUS_PRE_GAME:** Added to the canonical status map → "PREGAME".

**Slate-date wiring:** `_attempt_event_status()` (fallback_router) now extracts
`enr["slate_date"]` from the enrichment dict and converts YYYY-MM-DD → YYYYMMDD before
passing as `date_str` to `fetch_event_status()`.  `evidence_acquisition.run()` copies
`row["slate_date"]` → `enr["slate_date"]` early so the field is available to the router.

**Provenance stamp:** On success, `packet["event_status_provenance"]` is written with
provider, event_id, match_method, match_confidence, espn_status_raw, and competitors.

**Role-status write-back:** `projected_minutes` was previously omitted from the role-status
write-back block in `evidence_acquisition.run()`.  Fixed alongside BUG-005.

## Test suite
28 original + 14 BUG-001/002/003 + 1 BUG-004 + 8 BUG-005 = **51 total, all pass**.

## Live proof (2026-08-06)
- BUG-001/002/003: box_score_log=0→10, l5=0→5, l10=0→10; PAID key; ESPN v2 athlete
- BUG-004: parser top-level "names" key; rebounds/assists non-null; A'ja Boston id=4066407
- BUG-005: event_status=None→SCHEDULED; fields_unresolved=[]; PACKET_INCOMPLETE_REJECTED → PACKET_RECONSTRUCTED_COMPLETE
