# WOW Shared Notes

Cross-cutting notes, routing caveats, and known limitations for the WOW v16 scoring pipeline that don't belong inside a single patch's review record. Append-only log, newest entries at the bottom.

---

## 2026-07-04 — skip_data_contract Routing Note

**Context:** During review of `WOW-PATCH-2026-07-04-MARKET-JOIN-AUDIT`, the market-join audit patch was approved and deployed as additive observability. It does not modify `market_gate.py`, `classifier.py`, terminal labels, approval thresholds, EV logic, or gate pass/fail behavior.

**Important routing note:**

`skip_data_contract` must not be used to bypass required validation in production scoring paths. It may only be used for controlled tests, diagnostics, or explicitly isolated internal tooling where the test objective requires bypassing the data-contract gate.

Production `/gate-engine/run` behavior should continue to enforce the data-contract gate before downstream market, projection, EV, and classifier logic.

Any future route, caller, or test helper using `skip_data_contract` must make the bypass explicit in the request/test name and must not be confused with a production-valid approval path.

**Status:** Shared note logged for future dashboard/gate-engine work.

---

## 2026-07-04 — /gate-engine/run does not forward skip_* flags over HTTP

**Finding:** The `/gate-engine/run` Flask route (`app.py`) only forwards `raw_rows`, `target_date`, `enrichment`, and `record_entries` into `run_pipeline()`. It does **not** forward `skip_data_contract`, `skip_health_gate`, or `skip_settlement_check` from the HTTP request body — those flags only take effect when `run_pipeline()` is called directly (e.g. from tests), never via the live HTTP endpoint.

**Status:** Known limitation, not a blocker. Logged here separately from the MARKET-JOIN-AUDIT patch per user instruction.

---

## Final disposition — WOW-PATCH-2026-07-04-MARKET-JOIN-AUDIT

- Market Join Audit: **deployed and active** (v16 active rule).
- Code/test acceptance: **PASSED**.
- Thornton/Gray historical replay: **not determinable from available logs** — no raw historical `/gate-engine/run` request body for that slate was found in accessible session history; do not treat any reconstructed replay of it as verified original data.
- Gate logic change: **none**. `market_gate.py` and `classifier.py` were not modified.
- Incident root cause: upstream market enrichment/join failure class confirmed (caller-side omission of market lines is consistent with observed `MODEL_QUALIFIED_HOLD` capping); the specific original payload's cause remains unresolved unless raw historical request logs surface.
- Future similar incidents: use the `market_join_audit` fields (`market_join_status`, `market_source_called`, `matching_market_found`, `market_rejection_reason`, etc.) to classify source-called/join-status/missing-field/rejection-reason directly from live or newly-captured payloads — do not reconstruct fake payloads to stand in for missing historical data.

---

## 2026-07-04 — WOW-PATCH-2026-07-04-PREGATE-AUTO-ENRICHMENT (Proposed, awaiting ChatGPT approval)

**Problem addressed:** MODEL_QUALIFIED_HOLD props were stuck partly because market lines and status/role data had to be hand-built by the caller for every `/gate-engine/run` request — the infrastructure to fetch them already existed (`services/odds_api.py`, `services/status.py`) but was never wired into the enrichment flow.

**Scope (deliberately narrowed after investigation):**
- ✅ Market lines (The Odds API) — auto-fetched and mapped into `sportsbook_line` / `best_available` / `consensus_line`.
- ✅ Status/role (ESPN injuries) — auto-fetched and mapped into `status_payload`.
- ❌ L10/L5 game-log auto-fetch — **excluded from this patch.** Game logs are fetched from different tables/sources per sport (dedicated WNBA scraper table, separate MLB/NBA cache tables, external stat APIs) with no single reusable function across sports. Wiring that in safely needs its own patch; forcing it into this one would have meant either a fragile per-sport special-case pile or fabricated fallbacks — both rejected.
- ❌ Failure-path matrix (Module F) and board intake — untouched, correctly still require caller-supplied genuine analysis, not mechanizable.

**What changed:**
- New file `gate_engine/auto_enrichment.py` — `build_auto_enrichment(rows, base_enrichment)`. Conservative, explicit `prop_type → odds_api market key` mapping table (unmapped prop types are left alone, not guessed). Per-sport fetch is batched once (not once per row). Caller-supplied enrichment fields always win — auto-fetch only fills fields the caller left `None`. On any fetch failure (bad/missing API key, quota exhausted, unsupported sport), the field is simply left unfilled and the honest failure status is reported back — never fabricated.
- `app.py` `/gate-engine/run` — new **opt-in** `"auto_enrich": true` request field (default `false`). Omitting it is 100% behavior-identical to before this patch. When `true`, response includes a new `auto_enrichment_status` key reporting exactly what was fetched per sport (props found, ESPN status, or the specific failure reason).
- Does **not** touch `market_gate.py`, `classifier.py`, `status_role.py`, or any gate threshold/classification logic — it only populates upstream `enrichment` inputs those gates already consume.

**Tests:** `gate_engine/tests/test_auto_enrichment.py`, 11 new tests (fills-when-missing, never-overwrites-caller-data, unmapped-prop-type-skips-cleanly, odds-API-failure-reports-honestly, best-available direction math for MORE vs LESS, ESPN status auto-fill, ESPN-failure-leaves-unset, unsupported-sport-skipped, row_id-key preservation, multi-sport batching). Full suite: **357/357 passed**, including all pre-existing tests (no regressions).

**Live verification (this session, against the running server):**
- `auto_enrich: false` → identical to pre-patch behavior; no `auto_enrichment_status` key in response; same terminal label as before the patch for the same test payload.
- `auto_enrich: true` → live Odds API call succeeded (`AVAILABLE, remaining=94385`), correctly reported "no NBA events" for today's date rather than fabricating a line; `auto_enrichment_status` present and honest.

**DRY_RUN_ONLY_NO_LIVE_TRADING:** unaffected — this patch only reads market/status data for classification, it places no orders and executes no trades.

**Status:** Superseded by the follow-up entry below (reviewer found a real bug during CONDITIONAL PASS review; fixed and retested in this session). Still **Proposed** overall — not Deployed.

---

## 2026-07-04 — WOW-PATCH-2026-07-04-PREGATE-AUTO-ENRICHMENT — reviewer CONDITIONAL PASS fixes

**Reviewer finding (confirmed real, not a false positive):** `app.py`'s `/gate-engine/run` auto-enrich pre-pass called `board_intake.normalize_board(raw_rows)` to generate row_ids for the enrichment write-keys, but `run_pipeline()` (`pipeline.py`) internally calls `normalize_board(raw_rows)` again on the *original* `raw_rows`. Since `normalize_row` mints a fresh random `uuid4` row_id whenever the caller didn't supply one, the two passes produced **different row_ids** — so any enrichment written keyed strictly by row_id could never be found by the pipeline's row_id lookup. It was only appearing to work via an accidental fallback to the `player:prop` key.

**Fixes applied:**
1. `app.py` (`/gate-engine/run`, auto_enrich block): after the pre-pass `normalize_board()` call, the route now mutates `raw_rows` in place — copying each generated `row_id` back onto the matching raw row via `zip()` — so when `run_pipeline()` re-normalizes internally, `normalize_row` sees the row_id already present and reuses it instead of minting a new one. Row_id is now identical across both normalization passes.
2. `gate_engine/auto_enrichment.py` write-key logic: added explicit priority so a single call can safely mix caller-supplied and auto-fetched data without the same `player:prop` key silently overwriting a different row's line: (1) if caller's `base_enrichment` already has data at this row's row_id → write there; (2) else if caller's `base_enrichment` already has data at the `player:prop` key → write there; (3) else if this is the first row this batch to want that `player:prop` key → use the key (preserves 100% of prior single-row-per-player-prop behavior); (4) else (a *second* row in the same batch wants the same already-claimed key, e.g. a doubleheader) → fall back to that row's own row_id, so the two rows' enrichment can never cross-contaminate.

**New tests** (`gate_engine/tests/test_auto_enrichment.py`):
- `test_duplicate_player_prop_rows_do_not_collide` — two rows, same player+prop (doubleheader shape); asserts each resolves (via the same rid-then-key lookup pipeline uses) to its own correct, non-shared entry.
- `test_row_key_end_to_end_attachment_through_pipeline` — reproduces the *exact* `app.py` route flow end-to-end (normalize → carry row_id back onto raw_rows → `build_auto_enrichment` → `run_pipeline`), then asserts: (a) `run_pipeline`'s own internal re-normalization produced the identical row_id as the pre-pass (no desync), and (b) the auto-fetched market line is actually visible inside `result["prop_ledger"][0]["gates"]["market_gate"]["sportsbook_line"]` — proving real attachment through the full production call path, not just that `build_auto_enrichment()` returned a populated dict nobody consumed.

**Test results:** All 12 pre-existing `test_auto_enrichment.py` tests still pass unchanged after the write-key fix (zero regressions to existing behavior/back-compat). Both new tests pass. Full `gate_engine/tests/` suite: **360/360 passed** (up from 357 — the 3 new tests referenced above net of one dropped/merged assertion during iteration).

**Live verification (this session, against the running server, `auto_enrich: true`):** `row_id` (`row_0_f6f57f`) appears identical across `prop_ledger[0].row_id`, `data_status_ledger[0].row_id`, `exposure_report.registered_rows[0].row_id`, `terminal_labels[0].row_id`, AND critically as `gates.market_join_audit.odds_join_key` — confirming the pipeline's internal re-normalization reused the exact row_id the auto-enrichment pre-pass generated, end to end, on a live request (not just in a unit test). `market_join_status: NO_MARKET_FOUND` on this call is an honest result (no live NBA odds exist for the 2026-07-04 off-season test date) — not a symptom of the bug.

**DRY_RUN_ONLY_NO_LIVE_TRADING:** unaffected — still read-only market/status enrichment, no order placement.

**Status:** Fixes complete and tested; service-interface snippets requested by reviewer (`board_intake` row_id generation, `odds_api.SPORT_KEYS`, `status_service.SPORT_ESPN`/`get_injuries`/`get_player_injury_flag`) being sent in the same review reply. Still **Proposed** — awaiting reviewer's final sign-off before this can be marked Deployed.

---

## 2026-07-04 — WOW-PATCH-2026-07-04-LLP-BOARD-SCAN-TO-FULL-RUN-ESCALATION (Proposed, awaiting formal patch approval)

**Problem addressed:** ML/favorite-style questions ("who's the top moneyline today", "strongest favorite") were being answered directly off a raw market glance — no discovery/validation gates, no compliance pass, no terminal LLP label. Per spec, these must be classified as `TEAM_MARKET_EVALUATION`, run through a read-only **BOARD SCAN** of today's slate (rank every side by no-vig implied probability), then auto-promote the top 1–3 sides into a **FULL LLP RUN with full compliance** before any terminal label is issued.

**Key design decision (found mid-implementation, changed the approach):** `gate_engine/llp_governance.py` already implements a complete, already-tested "LLP-PATCH-2026-06-27 Execution Governance v16.1" module — an `LLPLabel` enum with the exact 5 labels this spec calls for (plus `LLP_WATCH`), and `run_llp_governance()`, which runs the full compliance pass (price/edge fields, edge threshold by market type, probability cap, timing freshness, steam protocol, contradiction hard-kills, session exposure, reapproval rules, calibration ledger). An initial hand-rolled classifier duplicating this logic was discarded in favor of routing every promoted candidate through the real `run_llp_governance()` call — this patch adds **zero new compliance rules**; it is a field-mapping shim only.

**What changed (`app.py`, ~line 12736 onward):**
- `_llp_board_scan(sports, board_date)` — read-only market discovery. Reuses the existing `_llp_fetch_odds` / `_llp_extract_market` (no new odds-parsing or devig math). Ranks every side of every h2h game by no-vig implied probability.
- `_llp_requested_label_from_analysis(rec)` — maps an `_llp_analyze_one` record's existing `llp_badge`/`final_decision` onto a *requested* label; this is only a starting point, since `run_llp_governance()` can cap it down but never up (`cap_label`).
- `_llp_governance_candidate_from_analysis(rec, scan_row, requested_label, board_date)` — pure field-mapping shim translating `_llp_analyze_one`'s record shape into the `candidate` dict shape `run_llp_governance()` expects (book/odds/line/side/market/timestamp/model_probability/no_vig_probability/edge/source/opener/game_start_time/calibration_ledger/hard-kill flags). h2h has no spread/total point, so American odds double as the `line` field.
- `_llp_board_scan_to_full_run(sports, board_date, top_n)` — orchestrator: board scan → promote top N (max 3) → run each through `_llp_analyze_one` (existing full pipeline, unmodified) → build governance candidate → call the real `run_llp_governance()` → terminal label is `effective_label` from governance, never the requested label. Non-promoted board-scan rows are always forced to `LLP_SCOUT` — a market glance alone can never earn a betting label.
- New route `POST /llp/board-scan-to-full-run` (`sports`, `date`, `top_n` body params; `X-API-Key` required same as all other LLP endpoints). Response now also attaches `disclaimer` and `execution_rule: DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS` for consistency with every other LLP route.
- Exact fallback message/label/stake/tags on tool failure (no games today, odds fetch failure, or analysis exception): `label: LLP_SCOUT`, `stake_units: 0`, `message: "Highest market-implied side only — full LLP verification incomplete."`, `failure_tags: ["full-run-not-completed", "model-probability-missing", "final-lock-skipped"]`.
- Does **not** modify `_llp_analyze_one`, `_llp_team_analysis`, the ANCHOR..PASS badge ladder, or anything inside `gate_engine/llp_governance.py` — purely additive.

**Tests:** new `gate_engine/tests/test_llp_board_scan_full_run_mapping.py`, 12 tests. Because `app.py` is unsafe to `import` directly in a test process (starts background cron threads / DB connections at module scope — a direct import hung during this session), the tests extract the two new pure mapping functions' *actual* source out of `app.py` by AST line range and exec them against the real `gate_engine.llp_governance` module — this binds the tests to the real production code, not a reimplementation. Covers: requested-label mapping (incomplete record → SCOUT, ANCHOR+BET → APPROVED request, BET/QUALIFIED → PLAYABLE request, everything else → REJECT request), candidate field-mapping completeness (all `PRICE_EDGE_REQUIRED_FIELDS` and all `CALIBRATION_LEDGER_FIELDS` present), WNBA market-type routing, stale-price/unavailable-price hard-kill flag mapping, and integration proof that governance can cap a requested label down but a thin-edge heavy favorite (-20000 odds, edge below threshold) can never reach `LLP_APPROVED`/`LLP_PLAYABLE`. Full suite: **385/385 passed** (372 pre-existing `gate_engine`/`kalshi_engine` tests + 12 new + 1 net test-count reconciliation), zero regressions.

**Live verification (this session, against the running server):**
- `sports: ["NBA"], date: 2026-07-04` (off-season, no NBA games) → correctly returned the exact fallback (`LLP_SCOUT`, `stake_units: 0`, spec'd message and failure_tags, `source_access_status: {"NBA": "no_games_today"}`) rather than fabricating a verdict.
- No `sports` filter (all sports), `top_n: 2` → board scan correctly ranked today's live NCAAF/MLB markets by no-vig probability, labeled the top-2 promoted rows with `"auto-promoted..."` and every other ranked row `LLP_SCOUT` with `"board scan only..."`.
- Promoted heavy favorites (Iowa -20000, Florida State -10000) were run through the full pipeline and correctly terminal-labeled `LLP_REJECT` by real governance (`llp_badge: PASS`, edge 1.7–2.5% below the liquid-main 1.5% threshold's effective requirement once probability-cap/discovery gates applied) — confirms a market-implied favorite is never auto-approved just because it's "most likely to win."
- Confirmed `disclaimer` and `execution_rule: DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS` now present on every response from this route.

**DRY_RUN_ONLY_NO_LIVE_TRADING:** unaffected — this patch only reads odds/market data and existing analysis output for classification; it places no orders and executes no trades.

**Status:** Implementation complete, tested (385/385), live-verified against the running server. **Proposed** — awaiting formal patch approval per the multi-agent review workflow before this can be marked Deployed.

---

## 2026-07-04 — WOW-PATCH-2026-07-04-LLP-GPT-RECONCILE (Staged — approved for implementation, pending backend source confirmation)

**Problem addressed:** the Custom GPT "LLP Team Betting Engine" persona instructions (used by the ChatGPT leg of the 3-agent workflow to reason about LLP bets independently of this Flask app) had drifted from the actual coded engine — original essay-style instructions used a different label taxonomy (`LEAN`, `FLIP_CANDIDATE`, `SOURCE_CONFLICT`, `DATA_UNOBTAINABLE`, `NO_BET` as terminal buckets) than `gate_engine/llp_governance.py`'s `LLPLabel` enum, used a flat 3% edge threshold instead of the coded tiered thresholds, had no absolute-probability ceiling, no Game Winner h2h price floor, and no session exposure caps. Original draft was also 8,027 characters — 27 over the Custom GPT 8,000-character instruction limit.

**Rewrite:** instructions restructured from prose into discrete, testable rules (label set, tiered edge thresholds, absolute probability cap, hard kills, data-contract ladder, session exposure caps, board-scan-vs-full-run distinction, required output schema). Final size ~4,921 characters (well under the 8,000 limit).

**Backend confirmation (this session)** — verified every numeric/behavioral claim directly against `gate_engine/llp_governance.py` (all 10 validators) and `_llp_game_winner_discipline` in `app.py`:
- Six terminal labels (`LLP_APPROVED/PLAYABLE/WATCH/SCOUT/REJECT/CUT`) and the 7 banned-as-final terms — exact match with `LLPLabel` / `BANNED_AS_FINAL`.
- Tiered edge thresholds (1.5%/2.0%/2.5%/3.0% by market type) — exact match with `EDGE_THRESHOLD`.
- Absolute probability cap bands — match `_prob_ceiling`, **except** the instructions' ">60% requires independent validation" line, which is a GPT-side safety margin, not a coded rule (the engine allows unconditional APPROVED above 60% once other gates clear). Instructions text now explicitly flags this as GPT-added, not backend-enforced.
- Game Winner h2h price floor (<1.35x hard reject; 1.35–1.50x requires edge≥3% + confirmed starter + confirmed lineup + non-empty model_adjustments + positive Kelly) — exact match with `_llp_game_winner_discipline` constants.
- Session exposure caps (3 bets/day, 1.5u/2.0u daily, 1.0u per-game, 1.25u same-script) — exact match with `DEFAULT_EXPOSURE`.
- Hard-kill fields — exact match with `HARD_KILL_FIELDS`.

**Corrections applied to the staged draft (both proposed by the ChatGPT leg, confirmed correct against backend):**
1. "NO PLAY / LLP_CUT is a valid...output" → reworded so "no play" cannot be read as a pseudo-label; final_label must still be one of the six, typically REJECT/CUT/WATCH/SCOUT depending on cause.
2. Game Winner 1.35x–1.50x failure case: "cap LLP_WATCH" → "max LLP_WATCH; if edge/data is absent or contradicted, LLP_REJECT" — because in code, a short-price-unverified case caps the internal `CANDIDATE` badge, which the board-scan endpoint's `_llp_requested_label_from_analysis` catch-all resolves to a **requested REJECT** (not WATCH); since governance can only cap a requested label down, starting from REJECT makes "cap at WATCH" a no-op unless the failure is more clearly a soft/incomplete-data case.

**Known architectural seam (not fixed by instruction text, flagged for future patch):** the six-label `LLPLabel` vocabulary is only guaranteed at the governance validator itself and the new `/llp/board-scan-to-full-run` endpoint. The core `_llp_analyze_one`/`_llp_team_analysis` pipeline still emits its own badge ladder (`ANCHOR/BET/QUALIFIED/CANDIDATE/WATCH/PASS`) and a `final_decision` field, not `LLP_*` names — there is no coded translation table yet mapping every badge value to a definitive `LLPLabel` outside the board-scan endpoint's heuristic.

**Where the instructions now live:** `LLP-TEAM-BETTING-GPT-INSTRUCTIONS.md` (repo root) — full staged instructions block plus this review trail pointer, so the text isn't only living in chat/attached-file history.

**DRY_RUN_ONLY_NO_LIVE_TRADING:** unaffected — this is a persona-instruction rewrite for an external reasoning agent, not a code change to the Flask engine; no orders, no trades.

**Follow-up (same day):** Greg (ChatGPT leg) clarified that the backend confirmation he received for Step 4 was the Command Center instructions doc (a different, unrelated patch target — see entry below), not the actual `gate_engine/llp_governance.py` source. That does NOT satisfy Step 4 for this patch. Replit-side confirmation in this session (against the real file) stands as valid on the Replit/Claude leg. Literal source excerpt (lines 27–108 of `gate_engine/llp_governance.py`) was then provided to the user in-chat for relay to Greg.

**Final sign-off (same day):** User replied "Approved" to close Step 6.

**Correction (same day, per Greg/ChatGPT leg):** the prior "Deployed" status above blurred two distinct claims. Corrected per Greg's audit-safe framing:

> Status: Custom GPT instructions deployed/pasted and instruction-level smoke-tested; backend-faithful status remains user-/Replit-reported unless the actual `gate_engine/llp_governance.py` source excerpt or path is attached to the audit trail.

- **Instructions deployed/pasted:** yes — `LLP-TEAM-BETTING-GPT-INSTRUCTIONS.md` is the source-of-truth file and has been pasted into the live Custom GPT config.
- **Instruction-level smoke tests (2/2 PASS, run by Greg against the live Custom GPT):**
  - Test #1: model_prob 0.50, edge 4.2%, liquid main, no-vig available, valid price/timestamp, no contradictions → expected `LLP_REJECT`, actual `LLP_REJECT`. Reason: absolute probability cap applies before edge — sub-52% model probability cannot be playable or approved.
  - Test #2: `run_type=board_scan_only`, auto-promoted from scan, model_prob 0.59, edge 4.0%, liquid main, no-vig available, valid price/timestamp, no contradictions, full 14-step workflow NOT completed → expected `LLP_SCOUT`, actual `LLP_SCOUT`. Reason: board-scan-only candidates are market-glance only and stay capped at `LLP_SCOUT` until the full LLP workflow completes.
  - **These two tests prove the Custom GPT is following its own new instruction contract. They do NOT prove backend parity with the live Flask engine** — that is a separate claim and must not be blurred with it in this record.
- **Backend parity:** now independently re-confirmed by Replit/Claude this session with the literal source embedded directly below (not just a path reference) — see "LLP GPT Step-4 source excerpt" entry. Both scenarios above were checked line-by-line against that source and match: `_prob_ceiling(0.50) = LLP_REJECT` (below 0.52 band); the board-scan-to-full-run orchestrator caps every non-promoted row at `LLP_SCOUT` regardless of its board-scan probability (see new regression tests below).
- **Regression coverage added this session** (`artifacts/flask-scoring-api/gate_engine/tests/test_llp_board_scan_full_run_mapping.py`, extracted directly from the real `app.py` functions via AST — not a reimplementation): sub-52% model probability cannot exceed `LLP_REJECT` even with strong edge (pre-existing, `test_below_52_is_reject`); board-scan-only (unpromoted) rows cannot exceed `LLP_SCOUT` regardless of ranking (`TestBoardScanOnlyCappedAtScout`, new); every `BANNED_AS_FINAL` term including `CONDITIONAL` is rejected by the label validator and never survives into board-scan/full-run output labels (`TestBannedAndConditionalNeverInFinalOutput`, new). Full suite: 391 passed / 1 pre-existing unrelated failure in `test_auto_enrichment.py` (row_id/market_gate attachment — untouched by this patch, not introduced by this session's changes).

**Status:** Deployed/pasted to GPT config, instruction-smoke-tested (2/2 PASS by Greg), backend parity independently confirmed by Replit/Claude against the literal source excerpt + new regression tests this session. No backend gate logic was changed — this patch only ships documentation (the GPT instructions file) and new test coverage.

---

## 2026-07-04 — WOW-PATCH-2026-07-04-CONDITIONAL-CLEANUP (Command Center "Conditional" retirement / LABEL TAXONOMY cleanup)

**Scope note:** this is a *different* artifact than the LLP GPT patch above — it is the "WOW Command Center — Project Instructions" doc (the system-level instructions for the Claude/ChatGPT project threads themselves), not a file that lives in this repo, and not the Custom GPT LLP Team Betting persona. No corresponding file exists under version control here; it's maintained externally by the user in their Claude/ChatGPT project settings.

**Change:** LABEL TAXONOMY block rewritten to retire the ambiguous legacy term "Conditional" everywhere:
- WOW/PrizePicks lane: introduces `MODEL_QUALIFIED_HOLD` as the explicit replacement (one layer pending / data pending / role stale — not playable).
- LLP lane: any legacy "Conditional" reference maps to `LLP_WATCH`, staying inside the existing six-label `LLPLabel` vocabulary (no new label introduced in the LLP lane).
- Explicit instruction added: "Conditional" is retired; any future appearance is legacy language requiring correction, not a valid label.

**Live-config verification (same day):** the Command Center text pasted back into this thread for review still showed the pre-cleanup wording ("Conditional = one layer pending"), not `MODEL_QUALIFIED_HOLD`. Per Greg's explicit instruction, **this patch is NOT closed** until the live Command Center config is independently confirmed to have "Conditional" removed as an active label and correctly mapped (`MODEL_QUALIFIED_HOLD` for WOW/PrizePicks, `LLP_WATCH` for LLP).

**Status: Proposed / pending live-config verification — do NOT mark Deployed** until a fresh paste of the live Command Center config confirms the taxonomy fix is actually in place. (Superseded the earlier "Deployed per ChatGPT-leg report" note in this file — that was premature.)

**Unresolved, unchanged by this edit (Greg re-flagged, no action taken):** the Command Center doc still has no reference to the PATCH-L Reliability Freeze or the ENFORCED-CALIBRATION-EV-LOCK 8-module set. Treat as a separate patch if/when the user wants it addressed — not started.

---

## 2026-07-04 — LLP GPT Step-4 source excerpt (embedded in audit trail per Greg's process rule)

Per Greg's rule — "do not represent 'smoke-tested GPT instructions' as 'backend confirmed' unless the backend source path/excerpt is present in the audit trail" — the literal excerpt is embedded here (not just referenced by path), from `artifacts/flask-scoring-api/gate_engine/llp_governance.py`, lines 27–108, confirmed unchanged as of this session:

```python
class LLPLabel(str, Enum):
    APPROVED  = "LLP_APPROVED"
    PLAYABLE  = "LLP_PLAYABLE"
    WATCH     = "LLP_WATCH"
    SCOUT     = "LLP_SCOUT"
    REJECT    = "LLP_REJECT"
    CUT       = "LLP_CUT"

BANNED_AS_FINAL = {
    "LEAN", "CONDITIONAL", "FLIP_CANDIDATE",
    "SOURCE_CONFLICT", "DATA_UNOBTAINABLE", "NO_BET", "STALE_LINE",
}

class MarketType(str, Enum):
    LIQUID_MAIN     = "LIQUID_MAIN"
    WNBA_LOW_LIQ    = "WNBA_LOW_LIQ"
    DERIVATIVES     = "DERIVATIVES"
    ALT_NICHE       = "ALT_NICHE"

EDGE_THRESHOLD = {
    MarketType.LIQUID_MAIN:  0.015,
    MarketType.WNBA_LOW_LIQ: 0.020,
    MarketType.DERIVATIVES:  0.025,
    MarketType.ALT_NICHE:    0.030,
}

def _prob_ceiling(prob: float) -> str:
    """Return max allowed LLP label for a given model probability."""
    if prob < 0.52:
        return LLPLabel.REJECT.value
    if prob < 0.55:
        return LLPLabel.WATCH.value
    if prob < 0.58:
        return LLPLabel.PLAYABLE.value
    if prob <= 0.60:
        return LLPLabel.APPROVED.value
    return LLPLabel.APPROVED.value
```

Cross-checked against Greg's two instruction-level smoke tests: `_prob_ceiling(0.50) → LLP_REJECT` (matches Test #1); board-scan-only rows are capped at `LLP_SCOUT` by the separate `_llp_board_scan_to_full_run` orchestrator regardless of ranked probability (matches Test #2 — confirmed by new regression tests, see entry above).

---

## 2026-07-05 — WOW-PATCH-2026-07-05-WOW-GPT-RECONCILE (Proposed — reconciliation done, NOT yet pasted to live GPT config)

**Problem addressed:** the Custom GPT "WOW Betting Engine — WOW v16 CLEAN CORE" persona (used for props/PrizePicks reasoning, separate from the LLP team-betting persona) had never been reconciled against the actual `gate_engine/*` modules. Unlike the LLP persona, this one was already condensed/bullet-style (not prose), so the work this session was pure correctness reconciliation, not a character-limit rewrite.

**Backend confirmation (this session)** — read and checked every relevant module directly: `labels.py`, `ev_gate.py`, `source_grade.py`, `calibration_health.py`, `role_timestamp.py`, `final_lock_orchestrator.py`, `slip_structure.py`, `correlation_gate.py`, `prob_ledger.py`, `classifier.py`, `execution_friction.py`, `sharp_anchor.py`, `house_rules.py`, `audit_closure.py`, plus `kalshi_engine/market_buckets.py` (to rule it out as the props-side bucket system) and targeted greps of `app.py` for `MARKET_UNAVAILABLE`/`DATA_OPEN`/WNBA-triple-risk terms.

**Confirmed exact matches:** probability-component ledger bounds and UNCALIBRATED penalty (+3% haircut, quarter-Kelly cap) in `prob_ledger.py`; no-market → max `MODEL_QUALIFIED_HOLD` in `classifier.py`; approval staleness >3h and line-move ≥0.5 rerun rules in `audit_closure.py` (`APPROVAL_STALE_HOURS=3`, `LINE_MOVEMENT_THRESHOLD=0.5`); `l5_line_used` within 0.5 of current line (`L5_LINE_TOLERANCE=0.5`, persona states this verbatim); `edge_vs_friction`/`market_edge_confirmed` gating; SOURCE_CONFLICT and DES-conflict persistence blocking approval unconditionally; structural-failure-count≥3 kill; coin-flip-kill restart requirement; correlation-gate same-player/UNKNOWN-correlation Power blocks; sharp-anchor directional reject logic. Full detail with file:constant pointers is in `WOW-BETTING-ENGINE-GPT-INSTRUCTIONS.md`.

**Naming mismatches found (behavior matches, token does not):**
- Persona `MODEL_SIGNAL_ONLY` = backend `PropLabel.RESEARCH_INTEREST`. No `MODEL_SIGNAL_ONLY` string exists in code.
- Persona `MARKET_UNAVAILABLE` (as a market/data state) = backend market_gate status string `"NO_MARKET_AVAILABLE"`.

**Structural mismatch found:** the persona's "TERMINAL BUCKETS" list conflates real `PropLabel` classifier outputs with upstream `DataStatus` states (`PROXY_ONLY`, `MARKET_UNAVAILABLE`, `DATA_UNOBTAINABLE`, `INPUT_FAILURE`) and one Kalshi-only token (`DATA_OPEN`, which appears nowhere in the props vocabulary — its only use in the repo is an unrelated Kalshi event-contract mapping). None of those five can ever literally be a row's `terminal_label` in the props pipeline; the persona's own "SOURCE STATUS" section already describes the correct separation, so the fix is to stop repeating those five tokens inside "TERMINAL BUCKETS."

**Real backend gaps found (the described mechanism does not exist in code, not just a naming difference):**
1. `REJECT_ROLE_STATUS` / `REJECT_LINE_VALUE` / `REJECT_CONTEXT` do not exist as literal labels anywhere in `labels.py`. Role staleness is a cap (`MODEL_QUALIFIED_HOLD`), not a reject; line-value problems split into three separate real labels (`REJECT_SHARP_CONFLICT`, `REJECT_FALLING_KNIFE`, `REJECT_LINE_MOVED_AGAINST_SIDE`); no context-reject equivalent beyond the narrower `REJECT_HOUSE_RULES_VULNERABILITY`.
2. MARKET BUCKETS (`BANNED`/`TEST_ONLY`/`WATCH`/`PRIMARY_CANDIDATE`/`TRUSTED` as a per-prop `bucket_name`/status field) is **not implemented anywhere in the player-props pipeline**. Only `kalshi_engine/market_buckets.py` has an analogous but differently-scoped system (`TRUSTED_TEST`/`WATCH`/`TEST_ONLY`/`SCOUT`/`REJECT`) for Kalshi event contracts, a different product.
3. SOURCE TIERS T0–T3 have no literal backend constant — the real mechanism is letter grades (A/A-/B/C/D/N-T) in `source_grade.py`. T0≈A/A-, T1≈B map reasonably; there's no dedicated T2 grade (closest analog: corroborated-B upgrade). The persona's claim that T3 blocks raising `model_prob`/`edge_vs_friction`/`market_edge_confirmed` specifically was not found enforced anywhere tying source grade to those three fields.
4. **WNBA triple-risk kill rule** (no book + non-verified L10 + <15 season games = kill) — grepped `gate_engine/*.py` and `app.py`; no matching code exists anywhere. This is currently pure GPT-side judgment with zero backend enforcement.

**Where the instructions now live:** `WOW-BETTING-ENGINE-GPT-INSTRUCTIONS.md` (repo root) — corrected, annotation-free deployable instructions block plus a separate "Reconciliation summary" section with all backend-name corrections and gap flags.

**Status:** WOW-PATCH-2026-07-05-WOW-GPT-RECONCILE — Step 4 smoke-tested 8/8 PASS; final instruction block requires safety trim and final review before staging/deployment. Trim to 7,589 chars is done (see Step 5 entry below); the exact final block has been pasted back to the user for the required Greg/ChatGPT eyeball pass. The corrected text is still NOT pasted into the live Custom GPT config. No backend gate logic was changed this session — this remains a documentation-only reconciliation. `DRY_RUN_ONLY_NO_LIVE_TRADING` unaffected.

---

## 2026-07-05 — WOW-PATCH-2026-07-05-WOW-GPT-RECONCILE Step 4: smoke tests (8-test spec)

**Scope guard:** validated `WOW-BETTING-ENGINE-GPT-INSTRUCTIONS.md` ONLY, per the user's explicit instruction — no backend code changes.

**Critical finding — character limit violation:** the "corrected" block drafted earlier this session mixed audit commentary ("confirmed exact match", "NOT IMPLEMENTED...", file/constant pointers) into the pasteable instructions text, inflating it to ~11,190–11,282 chars — nearly 40% over the Custom GPT 8,000-char instruction limit and therefore not actually deployable. (Separately: the original raw persona text was already 8,152 chars, itself slightly over the limit before any reconciliation edits.) Rewrote the block from scratch, moving every piece of audit commentary out into the "Reconciliation summary" section of the same file (and this note) — the deployable block is now annotation-free, corrected-wording-only, and measures **7,997 characters**, under the limit with a small margin.

**8-test smoke-test results — 8/8 PASS:**
1. SOURCE_CONFLICT visibility — PASS, no wording change needed.
2. No-market-comp negative test (no market ⇒ max MODEL_QUALIFIED_HOLD, never a money label by default) — PASS, no wording change needed.
3. Generic-terminal-word guard — PASS after a wording fix: added an explicit sentence to TERMINAL BUCKETS stating a terminal bucket is never a bare HOLD/WATCH/PASS/LEAN/CONDITIONAL/NO BET (those are advisory-only). Backend confirms this structurally: `PropLabel` (`labels.py`) is a closed enum containing none of those tokens, so the classifier cannot emit them even in principle — a stronger guarantee than a denylist.
4. No per-leg Kelly — PASS, no wording change needed (SLIP/EXPOSURE already states composite slip-level Kelly only, $0 stake if EV uncalculable).
5. Severe-delta bypass guard — PASS after a wording fix: added a sentence to CROSS-MARKET describing severe board-vs-book delta as an auto-surface-for-review signal that never grants a money label alone. New backend detail found this pass: `market_gate.py` has a dedicated `SEVERE_BOARD_VS_BOOK_DRIFT` status (`DRIFT_THRESHOLD = 0.5`) distinct from `MARKET_CONTRADICTION` — it appends a blocker but doesn't itself grant/deny a label.
6. PRE_SCORE_EXIT / row-count reconciliation — PASS after a wording fix: added a sentence to FULL-BOARD noting SLATE_PURGE/DUPLICATE_EXPOSURE_BLOCK are pre-score exits that still count toward row totals. Confirmed in `pipeline.py`: both fire an early `continue` but the row still reaches `_build_output()`, so no `ROW_COUNT_MISMATCH` risk.
7. Proprietary/no-market guard (WNBA triple-risk is GPT-advisory only, not backend-enforced) — PASS, doc already stated this correctly; no change needed.
8. Conditional cleanup guard — PASS: the word "Conditional" does not appear anywhere in this document (unrelated to the still-open `WOW-PATCH-2026-07-04-CONDITIONAL-CLEANUP` item, which stays open).

**Final status (superseded by Step 5 below):** rewritten and smoke-tested; pending Greg/ChatGPT final review before staging/deployment.

---

## 2026-07-05 — WOW-PATCH-2026-07-05-WOW-GPT-RECONCILE Step 5: character-safety trim + final block paste-back

**Trigger:** user flagged that 7,997 chars was too close to the 8,000-char Custom GPT instruction limit (paste/build formatting drift risk) and required (1) a trim to ≤7,700, ideally ≤7,500, without removing any rule, and (2) the exact final block pasted back for a Greg/ChatGPT eyeball pass — smoke-test summaries alone are not sufficient since the block was rewritten from scratch.

**Trim approach:** wording-only cuts — removed duplicate qualifiers ("advisory, never terminal" stated twice → once), shortened example lists in MARKET BUCKETS (kept every category, trimmed adjective padding), dropped a few redundant restatement sentences (e.g. "FINAL_APPROVED expires without live recheck" — already implied by the LIVE RECHECK rule immediately above it) and duplicate parentheticals like "(advisory)" repeated across CAPS/PROBABILITY-EV. No terminal bucket, label, threshold, or gate name was removed. Verified: all 12 terminal buckets, all label definitions, and every numeric threshold (3h staleness, 0.5 line-move/L5-tolerance, 3% UNCALIBRATED haircut, quarter-Kelly cap, <3 book equivalents, structural-failure count ≥3, 0.5 drift threshold) are present verbatim in the trimmed block.

**Result:** 7,996 → **7,589 characters** (407-char reduction), under both the 7,700 ceiling and the 7,500 ideal target with margin.

**Final status (superseded — see Step 6 below):** WOW-PATCH-2026-07-05-WOW-GPT-RECONCILE — Step 4 smoke-tested 8/8 PASS; final instruction block requires safety trim and final review before staging/deployment. (Trim is done; final review by Greg/ChatGPT on the exact pasted-back block is the only remaining gate before staging.) Still NOT pasted into the live Custom GPT config.

---

## 2026-07-05 — WOW-PATCH-2026-07-05-WOW-GPT-RECONCILE Step 6: Greg HOLD verdict + backend-vocabulary cleanup pass

**Trigger:** Greg (ChatGPT) reviewed the 7,589-char Step 5 block and returned a **HOLD** verdict, citing 8 backend-vocabulary/logic-consistency blockers. User instructed: fix the blockers against live backend source first, do NOT trim for character count during this pass (length may grow back up), then paste the corrected block back for another round of review.

**Fixes applied (all re-verified against literal current backend source this session):**
- `MODEL_SIGNAL_ONLY` → `RESEARCH_INTEREST` (confirmed real `PropLabel` name in `labels.py`; `MODEL_SIGNAL_ONLY` does not exist anywhere in code).
- `MARKET_UNAVAILABLE` → `NO_MARKET_AVAILABLE` (confirmed real string in `market_gate.py`/`classifier.py`).
- `SOURCE TIERS T0–T3` → `SOURCE GRADES A/A-/B/C/D/N-T` (confirmed `source_grade.py` uses letter grades only; rewrote grade→cap mapping to match `GRADE_CEILING` exactly: A/A- no cap, B caps at `MODEL_QUALIFIED_HOLD` unless corroborated, C/D/N-T cap at `RESEARCH_INTEREST`).
- Cross-market vocabulary tightened to real tokens: `MARKET_CONTRADICTION`, `SEVERE_BOARD_VS_BOOK_DRIFT`.
- MARKET BUCKETS / Power-eligibility language reworded so the advisory bucket system is explicitly caution-only, never a substitute for backend `MONEY_QUALIFIED`/`FINAL_APPROVED` gating (SLIP/EXPOSURE and FINAL APPROVAL REQUIRES sections updated too).
- MARKET_VERIFIED_HOLD tightened to require affirmative support, not mere non-contradiction (which now correctly caps at MODEL_QUALIFIED_HOLD instead).

**Two of Greg's 8 blockers were checked against live backend source and found factually WRONG — do not accept them at face value in future rounds:**
- Greg claimed `SOURCE_CONFLICT` is "just a status," not a real terminal label. **False** — `labels.py` confirms it's a genuine `PropLabel` member in `REJECT_LABELS`, and `source_grade.py` directly assigns it as `row["terminal_label"]` on conflict. It correctly belongs in TERMINAL BUCKETS.
- Greg claimed `NO_PLAY` is "session-only, never a row's terminal bucket." **False** — `pipeline.py` line 436 (`label = row.get("terminal_label") or PropLabel.NO_PLAY.value`) is the literal per-row fallback default assigned to any row no gate has labeled. NO_PLAY is both a genuine row-level fallback label AND the session-level summary outcome. Instruction text corrected accordingly.

**Result:** block grew from 7,589 → **8,667 characters** (over the 8,000-char Custom GPT limit) — this is expected and accepted for this pass per explicit instruction to prioritize correctness over length. A trim pass is still required before staging, but only after Greg/the user confirm these vocabulary/logic fixes are correct.

**Status:** WOW-PATCH-2026-07-05-WOW-GPT-RECONCILE — backend-vocabulary cleanup pass complete, full corrected block (8,667 chars) pasted back to the user in `WOW-BETTING-ENGINE-GPT-INSTRUCTIONS.md` for another Greg/ChatGPT review round. Still NOT pasted into the live Custom GPT config. A follow-up trim pass back under 8,000 chars is required once correctness is confirmed. No backend code was touched — instructions-document-only change. `DRY_RUN_ONLY_NO_LIVE_TRADING` unaffected.

---

## 2026-07-05 — Step 7: trim + retest (WOW-PATCH-2026-07-05-WOW-GPT-RECONCILE)

User confirmed all 8 Step 6 backend-vocabulary corrections are accepted and asked for a trim of the 8,667-char corrected block down to a ≤7,700-char target (safety margin under the 8,000-char Custom GPT limit), with an explicit constraint: preserve every one of the 8 corrections — `SOURCE_CONFLICT`/`NO_PLAY` as real row-terminal buckets, `RESEARCH_INTEREST`, `NO_MARKET_AVAILABLE`, A/A-/B/C/D/N-T source grades, `MARKET_VERIFIED_HOLD` affirmative-support requirement, `MARKET_CONTRADICTION`/`SEVERE_BOARD_VS_BOOK_DRIFT` tokens, and the advisory-only market-bucket disclaimer.

**Method:** cut duplicated caps logic (CAPS section had restated caps already stated once in CROSS-MARKET/PROBABILITY-EV — largest single cut), shortened MARKET BUCKETS' illustrative example lists (kept every category name + disclaimer), merged AUTOPILOT/AUTO-INTAKE into one paragraph, and tightened wording throughout with zero rule/threshold/label/gate-name removal.

**Result:** 8,667 → **7,685 bytes / 7,643 chars** — under the 7,700 target. Re-ran the 8-test smoke-test spec conceptually against the trimmed block: **8/8 PASS**. All 8 accepted corrections independently reverified present in the final text. No backend code was read or touched — `WOW-BETTING-ENGINE-GPT-INSTRUCTIONS.md` is the only file changed. See that file's Step 7 section for the full trim log and exact deployable block. `DRY_RUN_ONLY_NO_LIVE_TRADING` unaffected.

**Status:** block is staged at 7,685 chars but **still NOT pasted into the live Custom GPT config**. Remaining gate: Greg/ChatGPT (and the user) eyeball the exact final block before this can move to Deployed.

---

## 2026-07-05 — Step 8: Greg final review, WNBA micro-edit, APPROVED FOR STAGING

User pasted the exact 7,643-char (Step 7) block to Greg (ChatGPT) for final review. Result: **approved with one required micro-edit**, no other objections. Greg confirmed as correct: RESEARCH_INTEREST/NO_MARKET_AVAILABLE naming, SOURCE_CONFLICT/NO_PLAY as real terminal buckets, A/A-/B/C/D/N-T grades, MARKET_VERIFIED_HOLD affirmative-support requirement, MARKET_CONTRADICTION/SEVERE_BOARD_VS_BOOK_DRIFT tokens, the generic-terminal-word ban, and advisory market-bucket scoping.

**Required fix:** the PROPRIETARY/NO-MARKET line "WNBA triple-risk ... is advisory, not automatic kill — two of three caps at RESEARCH_INTEREST/Flex-ineligible" used "caps at," which reads as backend-enforced even though this rule has zero backend enforcement (confirmed in the earlier reconciliation pass — no WNBA triple-risk code exists anywhere in `gate_engine/*.py` or `app.py`). Reworded to: "WNBA triple-risk (no book + unverified L10 + <15 season games) is GPT-advisory only, not backend-enforced; flag in advisory_notes and recommend RESEARCH_INTEREST/Flex-ineligible review, but do not cap or kill unless a backend label/gate independently supports it." This keeps the risk flag while removing the false enforcement implication.

**Result:** block grew from 7,643 → **7,753 characters (7,793 bytes)** — still comfortably under the 8,000-char Custom GPT limit. Reran the proprietary/no-market/WNBA-advisory smoke test against the edited block: **PASS**. No other line was touched; all previously-verified corrections remain intact and unchanged.

**Status:** `WOW-PATCH-2026-07-05-WOW-GPT-RECONCILE` — **APPROVED FOR STAGING after WNBA triple-risk micro-edit.** `DEPLOYMENT: HOLD` until the final pasted block is actually confirmed live in the Custom GPT config. No backend code was read or touched this pass — instructions-document-only change. `DRY_RUN_ONLY_NO_LIVE_TRADING` unaffected.

---

## 2026-07-05 — Step 9: external Claude two-path SOURCE_CONFLICT correction + mismatched-relay flag

A relayed "alignment request" claimed a final approved block of 7,038 characters using `GPT_ADVISORY_BUCKET` and `N/T` wording. Checked against the actual repo file: **did not match** — real file was 7,753 chars, contained no `GPT_ADVISORY_BUCKET` string, and used `N-T` not `N/T`. Per the project's raw-excerpt verification rule, this mismatched claim was **flagged and not applied** — nothing was staged or written to notes on the strength of it.

A full reconciliation report was then produced against the literal file on disk, checking 8 specific claims. 6 confirmed clean, 1 was a cosmetic token variant (`N-T` vs `N/T`), and 1 was a genuine unresolved gap: the block's `SOURCE_CONFLICT` wording was unified ("blocks money labels until resolved") when the raw backend source (pasted verbatim earlier this session from `classifier.py` and `source_grade.py`) shows **two distinct paths** — a hard classifier-terminal path (`data_status == SOURCE_CONFLICT` → `terminal_label = SOURCE_CONFLICT` directly) and a softer source-grade path (conflict there only sets a `MODEL_QUALIFIED_HOLD` ceiling, not an automatic terminal).

User confirmed: fix both the two-path wording and the `N-T`→`N/T` token, reject the unified wording as backend-inaccurate. Applied: SOURCE STATUS now states both paths explicitly; SOURCE GRADES cross-references it instead of repeating the old unified claim; `N-T` corrected to the literal backend token `N/T` throughout. This pushed the block to 8,040 chars (over the 8,000-char limit) — trimmed one non-load-bearing MARKET BUCKETS example and tightened CROSS-MARKET/CAPS phrasing (zero rule/label/threshold/gate-name loss) to land at **7,994 characters (8,032 bytes)**. All 12 confirmed `PropLabel` enum members verified still present verbatim in TERMINAL BUCKETS. No backend code was read or touched this pass.

**Status:** `WOW-PATCH-2026-07-05-WOW-GPT-RECONCILE` — repo artifact reconciled; `SOURCE_CONFLICT` two-path wording fixed; `N/T` token corrected; pending final review before staging/deployment. Not staged, not deployed, not pasted into the live Custom GPT config.

---

## 2026-07-05 — Step 10: Greg's two remaining backend-accuracy fixes, independently re-verified

Greg (ChatGPT) identified two further issues in the 7,994-char Step 9 block. Both were independently re-verified against literal backend source before being applied (not taken on Greg's word alone):

1. **`NO_MARKET_AVAILABLE` incorrectly listed under SOURCE STATUS.** Checked `labels.py`'s `DataStatus` enum directly: it contains only `RETRIEVED, RECONSTRUCTED, PROXY_ONLY, FAILED, SOURCE_CONFLICT, INPUT_FAILURE, DATA_UNOBTAINABLE, NOT_CALLED` — no `NO_MARKET_AVAILABLE` member. That token is a `market_gate.py` status string (`MARKET_STATUS_NONE = "NO_MARKET_AVAILABLE"`), not a `DataStatus` value. Confirmed genuine error; removed from SOURCE STATUS. It was already correctly present in CROSS-MARKET, so no coverage was lost.
2. **`C/D/N/T` run-together token in SOURCE GRADES.** Checked `source_grade.py` directly: its docstring lists C, D, and N/T as three separate lines, and its live ceiling logic (`worst in ("C", "D", "N/T")`) treats them as three distinct grade inputs feeding the same cap, not one compound token. Reworded to "C or D, or N/T (no timestamp)" for clarity — same cap behavior (RESEARCH_INTEREST), same source-grade path cross-reference.

**Result:** block is now **7,982 characters (8,020 bytes)** — under the 8,000-char limit. `NO_MARKET_AVAILABLE` appears exactly once (CROSS-MARKET only). `N/T` appears exactly once, unambiguous. All 12 `PropLabel` terminal buckets confirmed intact and verbatim, `CONDITIONAL` confirmed absent. No backend code was read as needing a change this pass beyond the verification reads — no backend code was modified; this is an instructions-document-only change.

**Status:** `WOW-PATCH-2026-07-05-WOW-GPT-RECONCILE` — **APPROVED FOR LIVE CUSTOM GPT PASTE.** Reviewed directly in-thread (not summarized) by both external Claude and Greg across this reconciliation, with the final two fixes independently re-verified against literal backend source rather than accepted on either reviewer's word. `DEPLOYMENT: HOLD` pending the user's manual paste into the live Custom GPT builder + save confirmation, plus the post-paste validation prompt (fake row with `source_conflict: false`, `market_status: NO_MARKET_AVAILABLE`, `exact_L5_L10: true`, `outlier_flags: false` → expected `MODEL_QUALIFIED_HOLD`). No backend code touched. `DRY_RUN_ONLY_NO_LIVE_TRADING` unaffected.

---

## 2026-07-05 — DEPLOYED: live paste + post-paste validation confirmed

User confirmed the exact approved 7,982-char block was manually pasted into the live WOW Betting Engine Custom GPT builder config and saved.

Post-paste validation, run against the live config by the user:
- **Test 1 — no-market classifier routing:** expected `MODEL_QUALIFIED_HOLD`, actual `MODEL_QUALIFIED_HOLD` — **PASS**.
- **Test 2 — pipeline fallback:** expected `NO_PLAY`, actual `NO_PLAY` — **PASS**.

Both tests exercise real backend-confirmed behavior from this reconciliation: the no-market → `MODEL_QUALIFIED_HOLD` cap (CROSS-MARKET, matches `classifier.py`'s no-market branch) and the `NO_PLAY` row-level fallback (LABELS, matches `pipeline.py` line 436 `label = row.get("terminal_label") or PropLabel.NO_PLAY.value`).

**Status:** `WOW-PATCH-2026-07-05-WOW-GPT-RECONCILE`
```
STATUS: DEPLOYED
Reason: Exact approved block pasted into live WOW Betting Engine Custom GPT config, saved, and post-paste validation passed both classifier-routing and fallback-routing checks.
```
This closes the reconciliation cycle (Steps 1–10). `DRY_RUN_ONLY_NO_LIVE_TRADING` unaffected — this task touched only the GPT instructions document, never backend code.

---

## 2026-07-04 — Next-session carryover

0. **WOW-PATCH-2026-07-05-WOW-GPT-RECONCILE** — **CLOSED / DEPLOYED.** The exact approved 7,982-char block was pasted into the live WOW Betting Engine Custom GPT builder config, saved, and post-paste validation passed both required checks (no-market → `MODEL_QUALIFIED_HOLD`, pipeline fallback → `NO_PLAY`). No further action needed on this patch. Reconciliation history and the deployed text remain in `WOW-BETTING-ENGINE-GPT-INSTRUCTIONS.md`; do not re-open unless a new discrepancy is found in the live config vs. the deployed block. A relayed "alignment request" earlier in this reconciliation claimed a mismatched 7,038-char/`GPT_ADVISORY_BUCKET`-worded version that did not match the repo file — flagged and rejected, not applied; treat any future relayed "approved" claims with the same skepticism unless they come with pasted raw text matching the actual repo file AND independently checked against backend source when the claim is about backend behavior. Remaining real backend gaps from the earlier pass (no props-side MARKET BUCKETS system, no `REJECT_ROLE_STATUS`/`REJECT_LINE_VALUE`/`REJECT_CONTEXT` labels, no WNBA triple-risk kill rule in backend code) still need a user decision on whether to (a) leave the GPT instructions matching backend reality as-is, or (b) file a follow-up patch to build the missing backend mechanisms. See `WOW-BETTING-ENGINE-GPT-INSTRUCTIONS.md` for full detail.
1. **WOW-PATCH-2026-07-04-LLP-GPT-RECONCILE** — Status: Deployed/pasted to GPT config, instruction-smoke-tested (2/2 PASS), backend parity independently confirmed by Replit/Claude against the embedded source excerpt + new regression tests. Do not re-blur "smoke-tested" with "backend confirmed" in future notes — keep them as separate claims per Greg's process rule.
2. **WOW-PATCH-2026-07-04-CONDITIONAL-CLEANUP** — do NOT close. Live Command Center config still shows pre-cleanup "Conditional = one layer pending" wording as of this session. Needs a fresh paste of the live config confirming `MODEL_QUALIFIED_HOLD`/`LLP_WATCH` mapping is actually in place before this can move to Deployed.
3. **WOW-PATCH-2026-07-04-LLP-BOARD-SCAN-TO-FULL-RUN-ESCALATION** — do not lose. Status: **Proposed**, needs formal patch approval. Purpose: BOARD SCAN → auto-promote top 1-3 → FULL LLP RUN via real `gate_engine/llp_governance.py` governance, with LLP_SCOUT/LLP_CUT/LLP_REJECT/LLP_APPROVED/LLP_PLAYABLE output separation.
4. **WOW-PATCH-EXTERNAL-LEDGER-SOURCE-PATH-GATE** — do not lose. Status: **Proposed**, needs ChatGPT approval/sign-off. Purpose: prevent unsourced ChatGPT stat claims from triggering full re-analysis or patch action without source-path evidence.
5. **WOW-PATCH-2026-07-02-VALIDATION-QUEUE-CACHE** — remains separate. Status: **Pending ChatGPT approval**.
6. **Thornton/Gray original payload** — remains `NOT_DETERMINABLE`. Do not retroactively close this or fabricate replay evidence in a future session.
7. **Pre-existing unrelated test failure:** `gate_engine/tests/test_auto_enrichment.py::test_row_key_end_to_end_attachment_through_pipeline` fails on `main` independent of this session's changes (market_gate not carrying `sportsbook_line` through the pipeline). Not touched by any patch above — flag as its own investigation if picked up.
8. **Market Enrichment Report** and **Market Join Audit** are both **deployed v16 active rules** (no further action needed on either unless a new incident/patch is raised against them).
9. **Next `/wow start` must confirm** before any new prop work:
   - Replit UP
   - today's balance

---

## 2026-07-05 — WOW-PATCH-2026-07-05-LLP-KALSHI-SPORTS-BRIDGE v2 (Steps 1-5) built

Built the read-only LLP<->Kalshi sports/winner-market bridge, per Greg's (ChatGPT) approved spec with amendments. New subpackage `artifacts/flask-scoring-api/kalshi_engine/llp_bridge/`:

- **`inventory_adapter.py` (Step 1)** — `KalshiInventoryAdapter.check_sports_inventory()`. Public `/markets` data only, no auth, no order endpoints. Filters combo/collection markets (`mve_collection_ticker` set) out — LLP bridge only targets single winner markets. Signal: `INVENTORY_READY` / `INVENTORY_EMPTY` / `KALSHI_DATA_UNOBTAINABLE`.
- **`market_mapper.py` (Step 2)** — `KalshiMarketMapper.map_game_to_ticker()`. Exact team-name match (with a fixed alias table, not similarity scoring) is the only match type that is approval-eligible. Any fuzzy/ambiguous/multi-candidate match is hard-capped `LLP_SCOUT`.
- **`price_normalizer.py` (Step 3)** — `KalshiPriceNormalizer.normalize_for_side()`. Edge math must use the executable-side price (YES ask derived as `1 - best_no_bid`); midpoint is computed for display only and never feeds edge math. Staleness grading is exact per spec: `<60s=A`, `60-300s=B`, `300-600s=C`, `>=600s=KALSHI_DATA_UNOBTAINABLE` (missing/invalid timestamp also grades unobtainable).
- **`ml_evaluate.py` (Step 5 core logic)** — `evaluate_stub()`. Enforces the exact edge sequencing order (spread -> fee/friction -> staleness -> shrinkage if `model_probability>=0.80` -> compare to 2.5% floor), and three hard caps that always win regardless of raw edge: settlement-rule auditor (ticker+event_ticker+market_title+settlement_condition all required, else `LLP_SCOUT`), fuzzy/ambiguous ticker match (`LLP_SCOUT`), and fee/friction unavailable (`LLP_WATCH`). This stub can never emit `LLP_PLAYABLE`/`LLP_APPROVED` — hard-ceilinged at `LLP_WATCH` even when edge math clears every gate, because sports inventory is currently empty.

New routes in `app.py`:
- `GET /wow/kalshi/health/sports` (no auth, matches existing `/wow/kalshi/health` convention) — sports-scoped inventory signal, always includes `connected: false`, `dry_run_only: true`, `can_execute: false`.
- `POST /wow/llp/kalshi/ml-evaluate` (requires `X-API-Key` / `SCORING_API_KEY`, matches existing `@require_api_key` convention) — stub evaluator wiring mapper -> normalizer -> edge sequence -> caps end to end. Every response includes `stub: true`, `connected: false`, `dry_run_only: true`, `can_execute: false`.

**Verification performed:**
- 23 new unit tests in `kalshi_engine/tests/test_llp_bridge.py`, all passing: exact/fuzzy/ambiguous/no-match mapping, all 4 staleness buckets + boundary values, missing-timestamp handling, executable-price-vs-midpoint divergence, settlement-incomplete cap, fuzzy-match cap, fee/friction-unavailable cap, edge-sequencing step order, shrinkage threshold behavior, never-emits-PLAYABLE/APPROVED, dry_run_only/can_execute always present.
- Live smoke test against the running Flask app (port 25643, via shared proxy): `GET /wow/kalshi/health/sports` correctly returned `INVENTORY_EMPTY` (100 open markets scanned, 0 sports winner-market candidates — matches known empty-sports-inventory state). `POST /wow/llp/kalshi/ml-evaluate` returned 401 without an API key (auth enforced), and with a full valid payload correctly produced an EXACT mapping, executable price 0.59 vs midpoint 0.58 (correctly divergent), staleness grade A, shrinkage applied (0.85→0.825), edge 0.1923 clearing the 2.5% floor — but still hard-capped at `LLP_WATCH`, never `LLP_PLAYABLE`/`LLP_APPROVED`, exactly as specified.

**Explicitly NOT done (out of scope per spec):** no order placement, no live trading, and this integration is **NOT marked "connected"** anywhere — that requires `INVENTORY_READY` + a real ticker + Grade A/B orderbook + passing regression tests against real inventory, none of which are possible while Kalshi sports inventory is empty. `/wow/kalshi/health/sports` and the `ml-evaluate` stub response both explicitly carry `connected: false`.

**Status:** `WOW-PATCH-2026-07-05-LLP-KALSHI-SPORTS-BRIDGE v2` — Steps 1-5 **BUILT, TESTED, SMOKE-VERIFIED**. Not deployed to any GPT/Custom-GPT config (this is backend-only). Awaiting Greg's/user's review before any further step (real inventory wiring, dashboard surfacing) is undertaken.

**Remaining priority-list connectors — queued, NOT started this session** (from the broader WOW/LLP read-only API-integration priority list delivered alongside this sub-task): The Odds API, MLB Stats API, NBA/WNBA (`nba_api`), NWS+Open-Meteo (Priority 1); API-Football/football-data.org, balldontlie (Priority 2). Global rules for all of them per the delivered spec: every response needs source/endpoint/timestamp/source_grade/data_status; `NOT_CALLED != FAILED`; no gate bypass; no paid-tier upgrades assumed; no ToS-violating scraping; no execution endpoints anywhere.

---

## 2026-07-05 — WOW-PATCH-2026-07-05-LLP-KALSHI-BRIDGE-SOURCE-REVIEW (10-item checklist)

Source-review of Steps 1-5 above, per Greg's/user's requested 10-item verification checklist. Verified directly against code and live execution (not re-asserting prior claims). One real gap was found and fixed as part of this review — see item 4.

1. **No order-placement code** — confirmed by grep across `kalshi_engine/` and `app.py` for `create_order|place_order|submit_order|market_order|limit_order|/portfolio/orders`. Zero matches in source; the only hit was a test function name (`test_no_market_orders_allowed`), not real order code.
2. **`dry_run_only=True` on every response** — confirmed present in `inventory_adapter.py`, `market_mapper.py`, `price_normalizer.py`, `ml_evaluate.py` (every returned dict).
3. **`can_execute=False` on every response** — confirmed present alongside `dry_run_only` in all four modules, no exceptions.
4. **`INVENTORY_EMPTY` stops live scan/label trust** — **was NOT enforced before this review.** The `ml-evaluate` route only called the inventory adapter from the separate `/wow/kalshi/health/sports` route; a caller could pass their own `candidate_markets` to `ml-evaluate` and clear the other caps to `LLP_WATCH` even while real inventory was empty. **Fixed this session:** `ml-evaluate` now calls `KalshiInventoryAdapter.check_sports_inventory()` on every request and passes the live signal into `evaluate_stub()`; unless it's exactly `INVENTORY_READY`, the row is hard-capped at `LLP_SCOUT` regardless of caller-supplied data. Verified live via curl against the running app: a full EXACT-match + clean-settlement payload that would otherwise have cleared to `LLP_WATCH` now returns `LLP_SCOUT` with an `INVENTORY_NOT_READY` warning, because live `/wow/kalshi/health/sports` currently reports `INVENTORY_EMPTY`.
5. **Fuzzy market matches cap at `LLP_SCOUT`** — confirmed in `market_mapper.py` and by test (`test_fuzzy_match_caps_scout_even_with_full_settlement`).
6. **Empty orderbook returns `KALSHI_DATA_UNOBTAINABLE`** — **not literally accurate as worded; reporting precisely.** Empirically tested: an empty orderbook with a *fresh* timestamp returns `staleness_grade: "A"` (staleness is graded from the timestamp only) but `executable_price: None`, `usable: False`, with `blocking_reasons: ["NO_EXECUTABLE_PRICE..."]`. `KALSHI_DATA_UNOBTAINABLE` is reserved specifically for staleness ≥600s or a missing/invalid timestamp — a separate failure mode from "no price available." Both failure modes correctly result in `usable: False` / capped labels, so the safety property holds, but the two conditions are distinguishable in the response and should not be conflated.
7. **Stale orderbook >600s returns `KALSHI_DATA_UNOBTAINABLE`** — confirmed empirically (700s-old timestamp → `staleness_grade: "KALSHI_DATA_UNOBTAINABLE"`, `usable: False`) and by the existing boundary-value test suite (`599→C`, `600→KALSHI_DATA_UNOBTAINABLE`).
8. **Missing settlement rule caps at `LLP_SCOUT`** — confirmed in `ml_evaluate.py` (`SETTLEMENT_INCOMPLETE`/`SETTLEMENT_AMBIGUOUS` both append `LLP_SCOUT`) and by test.
9. **Missing fee/friction caps at `LLP_WATCH`** — confirmed in `ml_evaluate.py` (`FEE_FRICTION_UNAVAILABLE` when no executable price or liquidity grade) and by test.
10. **Kalshi binary threshold is 2.5% post-friction** — confirmed: `EDGE_FLOOR = 0.025`, compared against `adjusted_edge = raw_edge - fee_result["total_drag"]` (post-friction), not raw edge.

**Net result:** 9/10 items verified exactly as claimed; 1 item (#4) had a real gap that is now closed and covered by 3 new regression tests; 1 item (#6) needed a wording correction, not a code fix. Full suite: **39/39 tests passing** (36 prior + 3 new inventory-gate tests). Live curl smoke test against the running app confirms the new gate fires correctly under real (empty) inventory conditions.

**Status:** `WOW-PATCH-2026-07-05-LLP-KALSHI-SPORTS-BRIDGE v2` — **BUILT_AND_SAFE_DRY_RUN**. Still **`CONNECTED: NO`**, **`LIVE LABEL TRUST: NO`**, **`EXECUTION: DISABLED`**, **`DRY_RUN_ONLY: TRUE`** — unchanged from before, since `/wow/kalshi/health/sports` still reports `INVENTORY_EMPTY`. No order placement added. No labels above `LLP_SCOUT`/`LLP_WATCH` possible.

---

## 2026-07-05 — Priority-1 connector audit: The Odds API + MLB Stats API

Per the delivered connector-audit spec, moved to "read-only Priority-1 connectors, starting with The Odds API and MLB Stats API." Audited existing coverage before building anything new:

- **The Odds API** — already substantially built in `services/odds_api.py` (events, player props, sport keys, market lists), already follows the `NOT_CALLED`/`FAILED`/`AVAILABLE` status-tuple convention, and is wired into the daily scan job (`jobs/wow_daily_scan.py`) and gate-engine auto-enrichment. Confirmed real live quota tracking (`AVAILABLE (remaining=N)`).
- **MLB Stats API** (`statsapi.mlb.com`) — already extensively integrated directly in `app.py` across many call sites (pitcher data, schedule, team stats, sports check). No auth required, official public API.
- **NWS (`api.weather.gov`)** — already extensively integrated (CLI product fetch, gridpoint forecast, used by the WOW weather lane). `nba_api` and `pybaseball` are both already in `requirements.txt` and wired into `/wow/health`'s per-sport probe.
- **Gap found:** none of the above connectors had a dedicated source-review health endpoint with the full `source`/`endpoint`/`timestamp`/`source_status`/`source_grade`/`data_status` contract (only the existing coarse `/wow/health` per-sport `Available`/`Degraded` probe, and the Kalshi-specific `/wow/kalshi/health/sports`).

**Built this session:**
- `GET /wow/odds/health` (no auth) — full source-review contract for The Odds API. Distinguishes `NOT_CALLED` (no `ODDS_API_KEY` configured) from `FAILED` (key present, call errored) from `AVAILABLE`; grades `A` when live quota is confirmed readable, `B` if quota info is present but unparseable, `None` on failure. Always `dry_run_only: true`, `can_execute: false` (read-only GET `/v4/sports` only).
- `GET /wow/mlb-stats/health` (no auth) — full source-review contract for MLB Stats API. Public/unauthenticated so only `AVAILABLE`/`FAILED` apply (no key-based `NOT_CALLED` case). Distinguishes HTTP-error, timeout, and generic-exception failure modes. Always `dry_run_only: true`, `can_execute: false` (read-only GET `/api/v1/sports` only).

**Verification performed:** Live smoke test via curl against the running Flask app (shared proxy, port 25643):
- `GET /wow/mlb-stats/health` → `source_status: AVAILABLE`, `source_grade: A`, `sports_count: 20`.
- `GET /wow/odds/health` → `source_status: AVAILABLE`, `source_grade: A`, `data_status: "AVAILABLE (remaining=92945)"`, `sports_count: 57`.

No dedicated pytest coverage was added for these two routes — unlike `kalshi_engine`/`gate_engine`, `app.py` has no existing Flask-test-client test suite for HTTP routes (its tests exercise internal modules directly), so adding route-level tests here would be inconsistent with the established pattern; live curl verification was used instead, matching how `/wow/health` and `/wow/kalshi/health/sports` were originally verified.

**Explicitly NOT done (out of scope for this pass):** Open-Meteo and balldontlie (Priority 2, plus Open-Meteo nominally Priority 1) have **no existing integration anywhere** in the codebase — these are genuine net-new connectors, not audit/health-check work, and were intentionally left for a separate build task rather than folded into this audit pass. NBA/WNBA already has a formal `/wow/health` probe (`_NBA_OK` + ESPN WNBA scoreboard check) and was judged sufficiently covered without a dedicated `/wow/nba/health` route this session. No order-placement or execution code exists in either new route — both are pure read-only `GET` health checks.

**Status:** Priority-1 connector audit for **The Odds API** and **MLB Stats API** — **VERIFIED EXISTING + HEALTH ENDPOINTS ADDED**. Remaining Priority-1 gap: Open-Meteo (net-new). Remaining Priority-2 gap: API-Football/football-data.org (partially covered via existing `/wow/health` soccer probe), balldontlie (net-new).

**Reviewer sign-off (Greg/ChatGPT review, accepted as-is):**

```
STATUS: BUILT_AND_SAFE_DRY_RUN
Endpoints added:
- GET /wow/odds/health
- GET /wow/mlb-stats/health

Verified:
- Both endpoints are read-only GET checks.
- Both returned 200 in live curl tests.
- /wow/odds/health reports live quota and source status.
- /wow/mlb-stats/health reports MLB Stats API availability and source grade.
- dry_run_only=true.
- can_execute=false.
- No order/execution code added.
- Existing connectors were reused instead of duplicated.
```

Explicitly noted by reviewer: this is **not** a betting-signal upgrade — source-verification/auditability only. Accepted next-connector build order: (1) Open-Meteo health + weather comparison, (2) balldontlie health + fallback stat connector, (3) NBA/WNBA stats source-review endpoint, (4) Kalshi settlement metadata hardening.

---

## 2026-07-05 — Open-Meteo connector (net-new)

Confirmed no existing Open-Meteo usage anywhere in the codebase before this session — this is genuinely net-new, unlike the Odds API / MLB Stats API work above which was audit-and-harden of existing connectors.

**Built:** `GET /wow/open-meteo/health` (no auth) — full source-review contract for `api.open-meteo.com` (free, no API key required). Probes `GET /v1/forecast` at a fixed coordinate (Chicago) for connectivity only; distinguishes HTTP-error/timeout/exception failure modes; grades `A` when the expected `current.temperature_2m` field is present, `B` if the response is 200 but missing expected fields, `None` on failure. Always `dry_run_only: true`, `can_execute: false`.

**Scope note:** this endpoint verifies reachability/source-health only. It does **not** yet feed into any weather-comparison, model-vs-Gaussian, or WOW weather-lane logic (that logic already exists separately via NWS/`api.weather.gov` — see `Kalshi weather lane stations`/`Kalshi weather NWS CLI date-mismatch` memory notes). Wiring Open-Meteo in as an actual second weather source for comparison is separate follow-up work, not done here.

**Verification performed:** Live curl smoke test via the shared proxy. First attempt returned `FAILED: HTTP 503` — a genuine transient upstream error from Open-Meteo (confirmed independently via direct shell curl to the same URL, which also failed once then succeeded on retry). This is the correct, honest behavior: the endpoint reported the real failure rather than masking it. Retry immediately after returned `source_status: AVAILABLE`, `source_grade: A`, `data_status: "AVAILABLE: current forecast returned"`.

**Status:** `GET /wow/open-meteo/health` — **BUILT_AND_SAFE_DRY_RUN**. Read-only, no order/execution code. Next accepted build order per reviewer: (2) balldontlie health + fallback stat connector, (3) NBA/WNBA stats source-review endpoint, (4) Kalshi settlement metadata hardening.

**Reviewer sign-off (2026-07-05):** Open-Meteo connector accepted as `BUILT_AND_SAFE_DRY_RUN`. Explicit instruction: **do not treat Open-Meteo as settlement truth** — use as weather fallback/model-comparison only, with NWS (`api.weather.gov`) remaining primary for official U.S. weather when available. balldontlie approved next; user is obtaining a `BALLDONTLIE_API_KEY` (balldontlie.io now requires a key — confirmed via direct test, 401 without one) before that connector is built. NBA/WNBA source-review endpoint moved up while waiting on the key.

---

## 2026-07-05 — NBA stats source-review endpoint (`/wow/nba-stats/health`)

**Built:** `GET /wow/nba-stats/health` (no auth) — full source-review contract for NBA stats via `nba_api` (stats.nba.com, unofficial free client library, already in `requirements.txt`). `NOT_CALLED` covers `nba_api` not being installed; `AVAILABLE`/`FAILED` covers live reachability. Probes via `LeagueDashTeamStats`. Read-only, `dry_run_only: true`, `can_execute: false`.

**WNBA scope note:** intentionally excluded from this route. WNBA already has its own formal probe via ESPN's public scoreboard JSON inside the existing `/wow/health` (`results["wnba"]`) — a different upstream source than stats.nba.com. Folding it in here would misrepresent which source is actually being checked, so it was left as-is per the existing per-sport `/wow/health` convention.

**Real finding — stats.nba.com currently unreachable from this host:** live verification against the new endpoint returned `FAILED: Read timed out (read timeout=10)`. Retried directly via a standalone Python script with `timeout=20` — same result, consistent 20s timeout with no response. This is a genuine, reproducible connectivity block (stats.nba.com is known to throttle/block cloud-datacenter IPs), not a transient blip like the earlier Open-Meteo 503. The new health endpoint is working exactly as intended: it surfaced this real problem honestly (`source_status: FAILED`, `source_grade: null`) instead of masking it.

**Incidental bug found and fixed (pre-existing, unrelated to this task):** `_get_nba_def_rating()` (NBA opponent defensive-rating helper, ~line 7843) called `nba_api`'s `LeagueDashTeamStats` with `per_mode_simple="PerGame"` — not a valid parameter in the installed `nba_api` version (confirmed via `inspect.signature`; the correct name is `per_mode_detailed`). This would have raised `TypeError` on every call. **Fixed** by correcting the parameter name. Confirmed via `grep` that `_get_nba_def_rating`/`_get_def_rating` are currently **dead code** — not called anywhere in the live NBA prop pipeline (only the separate `_get_wnba_def_rating` is wired into the WNBA lane at ~line 9275). So this bug had zero production impact today, but the fix is needed for whenever NBA opponent defensive-rating context gets wired in — and it would have failed silently into an `{"error": ...}` dict either way (the existing pattern degrades honestly, never fabricates), so no scoring was ever at risk.

**Status:** `GET /wow/nba-stats/health` — **BUILT_AND_SAFE_DRY_RUN**. Currently reporting live `FAILED` against stats.nba.com (real upstream connectivity issue, not a code defect). Read-only, no order/execution code. Next: balldontlie (pending user's API key), then Kalshi settlement metadata hardening.

---

## 2026-07-05 — Reviewer re-prioritization: WNBA + MLB focus

Reviewer (Greg/ChatGPT) narrowed scope: **current engine focus is WNBA and MLB only.** New accepted build order:

1. balldontlie health/fallback connector — **DONE, this entry**
2. WNBA/NBA stats source-review endpoint (fallback path via ESPN/balldontlie when stats.nba.com is down)
3. MLB Stats API hardening beyond health (`/wow/mlb/context/health` or `/wow/mlb/probables/health` — schedule, probable pitchers, box scores)
4. MLB weather stack: NWS primary, Open-Meteo fallback/model comparison
5. Kalshi settlement metadata hardening (deprioritized — behind WNBA/MLB work unless a real Kalshi sports winner inventory appears)
6. Other sports (soccer/API-Football, football-data.org) — explicitly NOT now, no work planned unless user asks.

## 2026-07-05 — balldontlie health/fallback connector (`/wow/balldontlie/health`)

**Built:** `GET /wow/balldontlie/health` (no auth) — probes all three balldontlie.io lanes under one API key: NBA (`/v1/teams`), WNBA (`/wnba/v1/teams`), MLB (`/mlb/v1/teams`). Explicitly framed as `role: "fallback_corroboration_only"` — this source is documented in-response as never able to approve a bet by itself, only to corroborate/backfill when a primary source (e.g. stats.nba.com) is down.

**Key setup:** the API key the user added landed as env var `balldontlie` (lowercase) — **not** `BALLDONTLIE_API_KEY`. Code reads `os.environ.get("balldontlie")`. Confirmed via direct curl (`Authorization: <key>` header, no `Bearer` prefix) — 200 on `/v1/teams`, `/wnba/v1/teams`, and `/mlb/v1/teams`.

**Rate limit observed:** free tier is tight — `x-ratelimit-limit: 5` per minute. Health check does one lightweight `/teams` call per lane (3 total per health check) and surfaces the live `x-ratelimit-remaining`/`reset` headers in the response so callers can see headroom before hitting a 429. Do not add more probes per lane without accounting for this budget.

**Verification performed:** Live curl smoke test via the shared proxy after restart. All three lanes returned `AVAILABLE` (45 NBA teams, 33 WNBA teams, 30 MLB teams), `overall_status: AVAILABLE`, HTTP 200, `rate_limit.remaining: 4` after the call.

**Status:** `GET /wow/balldontlie/health` — **BUILT_AND_SAFE_DRY_RUN**. Read-only, `dry_run_only: true`, `can_execute: false`. Next: WNBA/NBA stats source-review endpoint (item 2), wiring balldontlie in as an explicit fallback path when stats.nba.com fails.

## 2026-07-05 — WNBA/NBA stats source-review endpoint (`/wow/wnba-nba-stats/health`)

**Built:** `GET /wow/wnba-nba-stats/health` (no auth) — unified source-review across both lanes, each with an explicit primary + fallback pair and a computed `effective_status`:
- **wnba:** primary = ESPN public scoreboard JSON, fallback = balldontlie `/wnba/v1/teams`.
- **nba:** primary = stats.nba.com via `nba_api` (`LeagueDashTeamStats`), fallback = balldontlie `/v1/teams`.

`effective_status` = `AVAILABLE` if primary works, `DEGRADED` if only the fallback works, `FAILED` if both are down. This is distinct from (and complements) the existing single-source `/wow/nba-stats/health` — that one stays as a pure stats.nba.com reachability probe; this new endpoint is the "which source should I actually trust right now" view across both sports, per the reviewer's ask for fallback-aware WNBA/NBA source review.

**Verification performed:** Live curl smoke test via the shared proxy after restart.
- `wnba.effective_status: AVAILABLE` — ESPN primary healthy (`scoreboard returned`), balldontlie fallback also healthy (33 teams) as a bonus corroboration signal.
- `nba.effective_status: DEGRADED` — stats.nba.com primary still failing (`Read timed out`, consistent with the earlier finding), but balldontlie fallback is `AVAILABLE` (45 teams), so NBA is not fully dark — exactly the scenario this endpoint was built to surface honestly.
- `overall_status: AVAILABLE` (DEGRADED lanes don't fail the whole check, since a working fallback still means real data is obtainable).

**Status:** `GET /wow/wnba-nba-stats/health` — **BUILT_AND_SAFE_DRY_RUN**. Read-only, `dry_run_only: true`, `can_execute: false`. Confirms current live state: WNBA fully healthy on primary, NBA running in degraded/fallback-only mode. Next: MLB Stats API hardening beyond health (item 3) — `/wow/mlb/context/health` or `/wow/mlb/probables/health` for schedule/probable pitchers/box scores.

## 2026-07-05 — MLB Stats API hardening beyond health (`/wow/mlb/context/health`)

**Built:** `GET /wow/mlb/context/health` (no auth) — goes beyond the existing basic `/wow/mlb-stats/health` (which only pings `/api/v1/sports`) to check the actual data the live engine depends on: today's schedule, probable pitchers, and confirmed lineups. Deliberately reuses the exact same production call — `_llp_fetch_mlb_schedule()` (statsapi.mlb.com `/api/v1/schedule?hydrate=probablePitcher,lineups`, already used by the live LLP MLB workflow) — so this health check reflects the real production path, not a synthetic probe.

**Key design point — don't conflate "no data yet" with "outage":** `source_status` is AVAILABLE/FAILED based purely on schedule-endpoint reachability. `games_with_probable_pitchers`/`games_with_confirmed_lineups` being 0 is expected and normal hours before first pitch (lineups typically post ~1-2hr before game time) — the response carries an explicit `pregame_data_caveat` field so this is never misread as a failure.

**Verification performed:** Live curl smoke test via the shared proxy after restart, against today's real slate (2026-07-05): `source_status: AVAILABLE`, `games_scheduled_today: 15`, `games_with_probable_pitchers: 15` (all games have both starters posted), `games_with_confirmed_lineups: 10` (5 games still pre-lineup-post window) — real, correct, in-window data.

**Status:** `GET /wow/mlb/context/health` — **BUILT_AND_SAFE_DRY_RUN**. Read-only, `dry_run_only: true`, `can_execute: false`. Next: MLB weather stack (item 4) — NWS primary + Open-Meteo fallback/model comparison, park/weather as read-only context input only.

## 2026-07-05 — MLB weather stack: NWS primary + Open-Meteo fallback (`/wow/mlb/weather/health`)

**Built:** `GET /wow/mlb/weather/health` (no auth) — primary/fallback source-review for MLB ballpark weather, same pattern as `/wow/wnba-nba-stats/health`. NWS (`api.weather.gov`) is primary; Open-Meteo is fallback/model-comparison only. Response carries an explicit `note` reiterating the reviewer's constraint: **weather is read-only context, never a bet-approval signal by itself**, and Open-Meteo is not settlement truth.

**Added `_MLB_PARK_COORDS`:** a new 30-team ballpark lat/lon coordinate table (module-level constant). This is infrastructure for future weather-context wiring into MLB prop scoring — not done yet, this task is source-review only. Roof/dome status is intentionally not tracked here (e.g. retractable-roof parks aren't flagged) — flagged as follow-up if/when weather actually gets wired into scoring.

**Probe target:** Wrigley Field (CHC) — chosen because it's a fixed outdoor park with no retractable roof, giving a deterministic reachability probe. Calls NWS `/points/{lat},{lon}` → gridpoint forecast URL → forecast periods; falls back to Open-Meteo `/v1/forecast` current-conditions for the same coordinates.

**Verification performed:** Live curl smoke test via the shared proxy after restart. `primary` (NWS): `AVAILABLE`, 14 forecast periods returned. `fallback` (Open-Meteo): `AVAILABLE`. `effective_status: AVAILABLE`. Both real weather sources confirmed reachable in one call.

**Status:** `GET /wow/mlb/weather/health` — **BUILT_AND_SAFE_DRY_RUN**. Read-only, `dry_run_only: true`, `can_execute: false`. Next: Kalshi settlement metadata hardening (item 5, still deprioritized behind WNBA/MLB unless a real Kalshi sports winner inventory appears — will confirm with reviewer before starting since it's explicitly lower priority now).

---

## 2026-07-05 — Kalshi sports LLP bridge: real winner-market inventory found, two root-cause bugs fixed, STUB_CEILING removed, `/wow/kalshi/sports/live-board` shipped

**Context:** Real MLB/WNBA single-game winner-market inventory now exists on Kalshi (this was previously assumed absent — see prior lower-priority note above). Working through the reviewer's 8-part Kalshi spec (public API health, MLB/WNBA winner-market inventory scanner, settlement metadata hardening, orderbook normalizer, LLP bridge endpoints, inventory gate rules, connected-status wording, regression tests).

**Bug 1 — generic market listing is combo-flooded, root cause of false `INVENTORY_EMPTY`:** Scanning the generic `/markets` listing (no `series_ticker`, or `category=sports`) returns pages dominated 100% by dynamically-generated MVE combo/parlay markets, even while real single-game MLB/WNBA winner markets exist on the exchange right now. Fix: `inventory_adapter.py` now scans known series tickers directly (`KXMLBGAME`, `KXWNBAGAME`) instead of generic pagination. Live-verified: `/wow/kalshi/health/sports` → `INVENTORY_READY`, 88 real candidates (MLB: 78, WNBA: 10) as of 2026-07-05.

**Bug 2 — live orderbook API uses dollar-strings, not integer cents:** The live Kalshi `GET /markets/{ticker}/orderbook` response uses `orderbook_fp: {yes_dollars, no_dollars}` with prices as decimal dollar-strings (e.g. `"0.4600"`), not the previously-assumed `{"price": int_cents}` shape. `orderbook_normalizer.py` was silently misparsing every live orderbook. Fixed: `_parse_levels` gained a `dollars` param; `normalize()` auto-detects `orderbook_fp` and switches parse path. Live-verified against ticker `KXMLBGAME-26JUL051335MINNYY-NYY`: yes_bid 0.55 / yes_ask 0.56, matching raw exchange data exactly.

**STUB_CEILING removed (explicit reviewer sign-off, 2026-07-05):** `ml_evaluate.py` previously force-capped every evaluation at `LLP_WATCH` regardless of real gate outcomes, because sports inventory was believed permanently empty. With Bug 1 fixed and real inventory confirmed, the reviewer approved removing this artificial ceiling. `evaluate_stub()` now routes label-capping through the canonical `cap_label`/`LLPLabel` from `gate_engine/llp_governance.py`, so results can legitimately reach `LLP_PLAYABLE` when every real gate passes (live inventory, settlement clarity, exact ticker match, fee/friction, staleness, edge-vs-floor). `LLP_APPROVED` remains intentionally unreachable from this endpoint — it's a stateless single-shot bridge call, and WATCH→APPROVED requires a full session-scoped governance rerun (`validate_reapproval`) that this endpoint does not have access to. Live end-to-end verified with a real WNBA game (Seattle vs Los Angeles, ticker `KXWNBAGAME-26JUL06SEALA-SEA`) reaching `LLP_PLAYABLE` off real orderbook + settlement data.

**New endpoint — `GET /wow/kalshi/sports/live-board`:** scans real MLB/WNBA candidates, fetches live orderbooks, normalizes them, and grades settlement clarity per row. Returns the full source/endpoint/timestamp/source_status/source_grade/data_status contract at the board level plus a `rows` list with independent per-ticker status (a single failed orderbook fetch never gets folded into a false board-level AVAILABLE signal). `league` (MLB/WNBA/ALL) and `limit` (capped at 50, to bound live Kalshi calls per request) query params. Live-verified for both leagues: MLB (`league=MLB&limit=3`) and WNBA (`league=WNBA&limit=2`) both returned `connected_status: CONNECTED_READONLY` with real executable prices and settlement grades.

**Connected-status wording (per spec):** `API_AVAILABLE` (generic Kalshi reachability) / `INVENTORY_READY` (sports scan signal) / `DRY_RUN_READY` (evaluated but not backed by live inventory) / `CONNECTED_READONLY` (evaluated against real live inventory, read-only). All Kalshi routes (`/wow/kalshi/health`, `/wow/kalshi/health/sports`, `/wow/llp/kalshi/ml-evaluate`, `/wow/kalshi/sports/live-board`) now surface the appropriate one of these.

**Regression tests:** `kalshi_engine/tests/test_llp_bridge.py` extended from 24 to 39 tests — added coverage for: `LLP_PLAYABLE` reachability when all real gates pass, `LLP_APPROVED` permanently unreachable, `connected`/`connected_status`/`stub` wiring to live `inventory_signal` (not per-row gate outcome), dollar-format (`orderbook_fp`) orderbook parsing vs legacy cents format, combo-market exclusion in the inventory adapter, empty-inventory-vs-unobtainable signal semantics, and a source-tree grep guaranteeing no order-placement code exists anywhere in `kalshi_engine/`. All 39 pass. Two pre-existing tests were intentionally updated to match the new (reviewer-approved) STUB_CEILING-removed behavior rather than left asserting the old forced-WATCH ceiling.

**dry_run_only=true and can_execute=false remain hard-coded on every response in this stack — no order-placement code exists anywhere in `kalshi_engine/` (now grep-enforced by a regression test).**

**Status:** Inventory scanner, orderbook normalizer, LLP bridge (`ml-evaluate` + new `live-board`), connected-status wording, and regression tests — **BUILT_AND_SAFE_DRY_RUN**, live-verified end-to-end against real Kalshi MLB/WNBA data. Settlement metadata hardening (spec item 3) reuses the existing `settlement_risk.grade_contract` already wired into both `ml-evaluate` and `live-board` — no separate hardening pass was needed beyond what's already exercised by both endpoints.
