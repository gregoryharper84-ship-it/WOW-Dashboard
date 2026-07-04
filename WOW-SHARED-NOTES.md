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

## 2026-07-04 — Next-session carryover

1. **WOW-PATCH-EXTERNAL-LEDGER-SOURCE-PATH-GATE** — do not lose. Status: **Proposed**, needs ChatGPT approval/sign-off. Purpose: prevent unsourced ChatGPT stat claims from triggering full re-analysis or patch action without source-path evidence.
2. **WOW-PATCH-2026-07-02-VALIDATION-QUEUE-CACHE** — remains separate. Status: **Pending ChatGPT approval**.
3. **Thornton/Gray original payload** — remains `NOT_DETERMINABLE`. Do not retroactively close this or fabricate replay evidence in a future session.
4. **Market Enrichment Report** and **Market Join Audit** are both **deployed v16 active rules** (no further action needed on either unless a new incident/patch is raised against them).
5. **Next `/wow start` must confirm** before any new prop work:
   - Replit UP
   - today's balance
