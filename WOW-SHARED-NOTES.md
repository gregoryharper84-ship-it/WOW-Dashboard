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

**Status:** Proposed. Diff is in the working tree (not yet a formal git commit record beyond the environment's own checkpointing) — ready for ChatGPT/wow-pr-checker review and formal approval before being marked Deployed.

---

## 2026-07-04 — Next-session carryover

1. **WOW-PATCH-EXTERNAL-LEDGER-SOURCE-PATH-GATE** — do not lose. Status: **Proposed**, needs ChatGPT approval/sign-off. Purpose: prevent unsourced ChatGPT stat claims from triggering full re-analysis or patch action without source-path evidence.
2. **WOW-PATCH-2026-07-02-VALIDATION-QUEUE-CACHE** — remains separate. Status: **Pending ChatGPT approval**.
3. **Thornton/Gray original payload** — remains `NOT_DETERMINABLE`. Do not retroactively close this or fabricate replay evidence in a future session.
4. **Market Enrichment Report** and **Market Join Audit** are both **deployed v16 active rules** (no further action needed on either unless a new incident/patch is raised against them).
5. **Next `/wow start` must confirm** before any new prop work:
   - Replit UP
   - today's balance
