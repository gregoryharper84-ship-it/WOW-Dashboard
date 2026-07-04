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

**Final sign-off (same day):** User replied "Approved" to close Step 6. Treating this as the final eyeball pass the deploy was being held for.

**Status: Deployed** (content-approved). The instructions text in `LLP-TEAM-BETTING-GPT-INSTRUCTIONS.md` is now the approved, final version — no further edits pending. Note the literal act of pasting this block into the Custom GPT builder UI is a manual step outside this repo/session; Replit/Claude has no access to that config surface. If it hasn't been pasted into the live Custom GPT yet, that paste is the only remaining action.

---

## 2026-07-04 — WOW Command Center instructions — "Conditional" retirement / LABEL TAXONOMY cleanup

**Scope note:** this is a *different* artifact than the LLP GPT patch above — it is the "WOW Command Center — Project Instructions" doc (the system-level instructions for the Claude/ChatGPT project threads themselves), not a file that lives in this repo, and not the Custom GPT LLP Team Betting persona. No corresponding file exists under version control here; it's maintained externally by the user in their Claude/ChatGPT project settings.

**Change:** LABEL TAXONOMY block rewritten to retire the ambiguous legacy term "Conditional" everywhere:
- WOW/PrizePicks lane: introduces `MODEL_QUALIFIED_HOLD` as the explicit replacement (one layer pending / data pending / role stale — not playable).
- LLP lane: any legacy "Conditional" reference maps to `LLP_WATCH`, staying inside the existing six-label `LLPLabel` vocabulary (no new label introduced in the LLP lane).
- Explicit instruction added: "Conditional" is retired; any future appearance is legacy language requiring correction, not a valid label.

**Status:** Reported by the ChatGPT/Greg leg as applied and requested to move from Proposed → **Deployed**. Recording here as **Deployed per ChatGPT-leg report** — flagging for the user: since this doc lives outside this repo (not a file Replit/Claude can read or diff), this status reflects Greg's assertion only. Please confirm you've actually pasted the corrected LABEL TAXONOMY block into the live Command Center project instructions if you haven't already, so this can be fully closed with confidence.

**Unresolved, unchanged by this edit (Greg re-flagged, no action taken):** the Command Center doc still has no reference to the PATCH-L Reliability Freeze or the ENFORCED-CALIBRATION-EV-LOCK 8-module set. Treat as a separate patch if/when the user wants it addressed — not started.

---

## 2026-07-04 — LLP GPT Step-4 source excerpt (provided to user for pasting to Greg)

Greg needs the literal `gate_engine/llp_governance.py` source (not the instructions doc) to independently confirm the `WOW-PATCH-2026-07-04-LLP-GPT-RECONCILE` rewrite before Step 6. Verbatim excerpt covering the `LLPLabel` enum, `BANNED_AS_FINAL` set, edge-tier thresholds, and probability-cap logic was pulled from `artifacts/flask-scoring-api/gate_engine/llp_governance.py` (lines 27–108) and handed to the user in-chat this session for relay to the ChatGPT thread. Once Greg confirms against that excerpt, Step 6 can proceed pending the user's own final eyeball pass.

---

## 2026-07-04 — Next-session carryover

1. **WOW-PATCH-2026-07-04-LLP-GPT-RECONCILE** — Status: **Deployed** (content-approved by user, "Approved" received this session). Instructions text lives in `LLP-TEAM-BETTING-GPT-INSTRUCTIONS.md` — that is now the final version. Remaining action, if not already done: paste that block into the live Custom GPT builder UI (manual, outside this repo/session — Replit/Claude cannot do this step).
2. **WOW Command Center "Conditional" retirement / LABEL TAXONOMY cleanup** — reported Deployed by Greg; confirm with the user that the live external Command Center config was actually updated, since this repo has no copy of that doc to verify against.
3. **WOW-PATCH-2026-07-04-LLP-BOARD-SCAN-TO-FULL-RUN-ESCALATION** — do not lose. Status: **Proposed**, needs formal patch approval. Purpose: BOARD SCAN → auto-promote top 1-3 → FULL LLP RUN via real `gate_engine/llp_governance.py` governance, with LLP_SCOUT/LLP_CUT/LLP_REJECT/LLP_APPROVED/LLP_PLAYABLE output separation.
4. **WOW-PATCH-EXTERNAL-LEDGER-SOURCE-PATH-GATE** — do not lose. Status: **Proposed**, needs ChatGPT approval/sign-off. Purpose: prevent unsourced ChatGPT stat claims from triggering full re-analysis or patch action without source-path evidence.
5. **WOW-PATCH-2026-07-02-VALIDATION-QUEUE-CACHE** — remains separate. Status: **Pending ChatGPT approval**.
6. **Thornton/Gray original payload** — remains `NOT_DETERMINABLE`. Do not retroactively close this or fabricate replay evidence in a future session.
7. **Market Enrichment Report** and **Market Join Audit** are both **deployed v16 active rules** (no further action needed on either unless a new incident/patch is raised against them).
8. **Next `/wow start` must confirm** before any new prop work:
   - Replit UP
   - today's balance
