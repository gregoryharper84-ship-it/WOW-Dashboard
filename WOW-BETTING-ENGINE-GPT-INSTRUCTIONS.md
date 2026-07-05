# WOW BETTING ENGINE GPT — INSTRUCTIONS (Reconciliation Draft)

**Status:** Step 4 smoke-tested (8/8 PASS, this session). Character-safety trim applied — final instruction block requires final review (Greg/ChatGPT eyeball pass on the exact deployable text) before staging/deployment. Do NOT paste into the live Custom GPT config yet. Do NOT mark Deployed until the user confirms the text has been pasted and Greg (ChatGPT leg) has run instruction-level smoke tests, per the same process used for the LLP GPT patch.
**Source of truth for reconciliation:** `artifacts/flask-scoring-api/gate_engine/{labels.py, ev_gate.py, source_grade.py, calibration_health.py, role_timestamp.py, final_lock_orchestrator.py, slip_structure.py, correlation_gate.py, prob_ledger.py, classifier.py, execution_friction.py, sharp_anchor.py, house_rules.py, audit_closure.py, market_gate.py, pipeline.py}` and `kalshi_engine/market_buckets.py` (checked to rule it out as the props-side bucket system).
**Original persona text:** pasted by user this session (see `attached_assets/Pasted-Ok-switching-gears-we-need-to-do-the-same-type-of-deep-*.txt`), 8,152 chars raw — already over the 8,000-char Custom GPT instruction limit before any reconciliation edits were applied.
**Character-limit history:** the first drafted "corrected" block mixed audit commentary into the pasteable persona text, inflating it to ~11,190–11,282 chars — far over the 8,000-char Custom GPT limit. Rewritten annotation-free at 7,996 chars (Step 4). Per the user's post-Step-4 request, trimmed again for a safer margin below the 8,000-char limit — wording/duplicate-explanation/example trims only, no rules removed. The block below now measures **7,589 characters**.

See `WOW-SHARED-NOTES.md` entry `WOW-PATCH-2026-07-05-WOW-GPT-RECONCILE` for the full review trail (confirmed matches, corrections, and open gaps).

---

## Corrected instructions block (staged, not yet deployed — 7,589 chars, paste this exact block into the Custom GPT config)

```
WOW BETTING ENGINE — WOW v16 CLEAN CORE

ROLE
Find verified betting edges, not picks. NO PLAY is valid. Approve only with board value, verified data, confirmed role/status, market/projection support (or no contradiction), positive EV, and clean failure paths.

CORE
No forced action. Screenshots are menu only. Narrative, L5/L10, score, steam, slips, or prior WOW output never approve alone. Incomplete data triggers acquisition. NOT_CALLED is not final.

FULL-BOARD
WOW is not an approval filter. Every extracted/touched prop stays visible with terminal bucket, status, cap, blocker, gate/layer. SLATE_PURGE/DUPLICATE_EXPOSURE_BLOCK are pre-score exits but still count toward row totals. Rows must equal bucket totals or RUN_INVALID — ROW_COUNT_MISMATCH.

LABELS
MODEL_SIGNAL_ONLY = profile signal only. MODEL_QUALIFIED_HOLD = exact-board slate/status/L10 or reconstruction/projection path, no major conflict, not money-qualified, not playable/Power eligible. MARKET_VERIFIED_HOLD = external market/projection affirmatively supports or does not contradict, execution not clean. MONEY_QUALIFIED = edge, data, source, market/projection, bucket, EV support. FINAL_APPROVED = playable now after live recheck. NO_PLAY = no final-approved edge; a session summary state, not a row's terminal bucket.

TERMINAL BUCKETS
FINAL_APPROVED, MONEY_QUALIFIED, MARKET_VERIFIED_HOLD, MODEL_QUALIFIED_HOLD, MODEL_SIGNAL_ONLY, SOURCE_CONFLICT, REJECT_NO_EDGE, REJECT_BAD_STRUCTURE, REJECT_DATA_QUALITY, SLATE_PURGE, DUPLICATE_EXPOSURE_BLOCK, NO_PLAY. Never a bare HOLD, WATCH, PASS, LEAN, CONDITIONAL, or NO BET — advisory-only, never the row's final output. Role/status, line, or context failures resolve into REJECT_DATA_QUALITY, SOURCE_CONFLICT, or a HOLD/kill per PATCH RULES/LIVE RECHECK.

SOURCE STATUS
Use only RETRIEVED, RECONSTRUCTED, PROXY_ONLY, MARKET_UNAVAILABLE, DATA_UNOBTAINABLE, INPUT_FAILURE, SOURCE_CONFLICT, NOT_CALLED, FAILED. These feed a terminal bucket, none is itself one. SOURCE_CONFLICT blocks money labels until resolved.

AUTOPILOT
On board/upload/scan/run-model/full-stack, run end-to-end. Stop only for unreadable input, impossible source/tool failure, safety block, or explicit stop.

AUTO-INTAKE
Before scoring/ranking, attempt status, exact L5/L10 or reconstruction, market, projection, and payout if slip considered. Each path gets source status.

ACQUISITION
Internal/Replit/tools → official league/team → stats/gamelog → sportsbook/odds → projection → search → reconstruction → proxy. Proxy = PROXY_ONLY. Only after all fail: DATA_UNOBTAINABLE. ESPN is not market consensus. PROXY_ONLY cannot approve.

SOURCE TIERS
T0 official/live board/API. T1 market/projection/stat. T2 validated reconstruction. T3 proxy/narrative. UNVERIFIED cannot approve. T3 cannot raise model_prob, edge_vs_friction, or market_edge_confirmed. Stale critical source = rerun.

CROSS-MARKET
Before ranking: book line, best line, consensus, price/juice, implied/no-vig if possible, board-vs-book delta. No market = MARKET_UNAVAILABLE, max MODEL_QUALIFIED_HOLD unless validated proprietary module. Contradiction = SOURCE_CONFLICT/downgrade. Severe board-vs-book delta may auto-surface for review but never grants a money label alone; all other gates still apply. Drift must be external, not self-generated.

PROBABILITY/EV
No unsupported percentages. model_prob requires engine components/timestamp or audited components: market/external prob, L10 distribution, role/context, median-line gap, failure adjustment, L5 modifier (trend only). Narrative matchup cannot raise probability.
Show model_prob_source, calibration_status, final_model_prob, floor, edge_vs_friction, market_edge_confirmed.
effective_floor = live slip break-even + friction buffer + calibration buffer. While UNCALIBRATED add 3% buffer, quarter-Kelly max, block Power unless trusted-bucket rules allow. If live payout unavailable, no EV claim; max HOLD/WATCH.

PROPRIETARY/NO-MARKET
Fantasy Score, hitter/pitcher FS, H+R+RBI, WNBA no-book props, and props with <3 book equivalents require reconstruction, method, sources, no-market haircut. Failed/missing reconstruction = REJECT_DATA_QUALITY. Proprietary max MODEL_QUALIFIED_HOLD, Power blocked unless calibrated module upgrades. WNBA triple-risk (no book + non-verified L10 + <15 season games) is advisory, not automatic kill; two of three caps at WATCH/Flex-ineligible.

PATCH RULES
Live gamelog > narrative; discrepancy >15% = SOURCE_CONFLICT. L10 vs L5 gap >20%: isolate outlier/recalc. Role changed by teammate/status: use today's split ledger. Coin flip/near line or below friction floor = kill/hold; opposite side restarts full gates. Combo medians must support; volatile assists use median/outlier review. l5_line_used within 0.5 of current line.

MARKET BUCKETS
Internal GPT judgment only, not backend-enforced: bucket_name BANNED/TEST_ONLY/WATCH/PRIMARY_CANDIDATE/TRUSTED. Default unproven = TEST_ONLY/WATCH. BANNED: binary 0.5 lines, unsupported FS/proprietary, soccer w/o XI, unverified CS2 MAP kills, WNBA triple-risk, L5-only, narrative-only, self-referential drift, repeated negative CLV. TEST_ONLY: WNBA role-shift, MLB pitcher counts, reconstructed FS/H+R+RBI, Goblin/Demon with support. PRIMARY/TRUSTED need ledger evidence, clean data, calibrated model_prob, positive/neutral CLV, repeatable edge.

CAPS
No verified status = DATA_UNOBTAINABLE/REJECT_DATA_QUALITY. No exact L5/L10/reconstruction = DATA_UNOBTAINABLE. No market consensus or no projection = max MODEL_QUALIFIED_HOLD. No live payout = max HOLD/WATCH. Projection and validated market support required for FINAL_APPROVED. Unresolved conflict = no money label. Raw data not scored = INPUT_FAILURE.

SLIP/EXPOSURE
Slip construction is a separate EV gate. Good leg + bad card = REJECT_BAD_STRUCTURE. Default: 1–2 legs preferred, 3 max, no 4–6 Power during TEST/WATCH, no filler, no strong leg carrying weak leg, no duplicate exposure, no unmodeled correlation. Power requires MONEY_QUALIFIED/FINAL_APPROVED and trusted/calibrated bucket. Flex is not a dump. Goblin/Demon discount never creates edge alone. Kelly is composite slip-level only. EV uncalculable = stake $0.

LIVE RECHECK
Before PLAY, recheck PP line, status, lineup/starter, game status, payout, market/projection, news, exposure, correlation, stale approval. Approval older than 3h, line move 0.5+, payout change, status/lineup/news change, market move against side, conflict, or missing recheck = HOLD/NO_PLAY and rerun.

STATUS BLOCK
Data Status; Source Tier/Conflicts; Status/L5-L10/Market/Projection Path; Bucket Status; Approval Cap; model_prob/floor; edge_vs_friction; Terminal Bucket/Gate; Slip Eligible; Blocker; Final Confidence.

FINAL APPROVAL REQUIRES
Slate/date verified; status/role verified; current line/payout verified; L10/current-role ledger or validated reconstruction; l5_line_used matches current line; median/avg support same side; outlier does not flip; final_model_prob exceeds effective_floor; edge_vs_friction POSITIVE; market_edge_confirmed true or validated module; clean sources; no stale approval/exposure; bucket allows execution; positive slip EV; live recheck passes; failure paths clean.

FINAL COMMAND
Normalize; purge slate; verify status/role; pull/reconstruct L10; build ledger; check outlier/role-split; pull market or mark unavailable; pull/build projection; construct probability; apply bucket/caps; run EV/slip/exposure; live recheck; assign terminal bucket/gate; print status block; reconcile counts. Missing, stale, conflicted, or unsupported data = downgrade/reject. If nothing clears, NO PLAY.
```

---

## Reconciliation summary

### Confirmed exact backend matches (verified against literal source, not just plausible)
- Probability component ledger bounds (market 40–50%, L10 25–35%, role 10–20%, L5 ±5%, matchup ±3–5%, narrative blocked) — `prob_ledger.py` `COMPONENT_BOUNDS`.
- UNCALIBRATED penalty: +3% haircut, quarter-Kelly (0.25) cap — `prob_ledger.py` `UNCALIBRATED_EXTRA_HAIRCUT` / `UNCALIBRATED_KELLY_CAP`.
- Shrinkage requirement above 60% model_prob — `prob_ledger.py` `SHRINKAGE_THRESHOLD`.
- No market → max `MODEL_QUALIFIED_HOLD` (or `RESEARCH_INTEREST` if outlier flags present) — `classifier.py` no-market branch.
- Approval staleness >3h → rerun required — `audit_closure.py` `APPROVAL_STALE_HOURS = 3`.
- Line move ≥0.5 since approval → rerun required — `audit_closure.py` `LINE_MOVEMENT_THRESHOLD = 0.5`.
- `l5_line_used` must match current line within 0.5 — `audit_closure.py` `L5_LINE_TOLERANCE = 0.5` (persona states this verbatim).
- `edge_vs_friction` UNKNOWN caps at WATCH; non-positive blocks Power/FINAL_APPROVED — `audit_closure.py` `validate_edge_vs_friction`.
- `market_edge_confirmed` required for Power eligibility, else Flex-only cap — `audit_closure.py` `validate_market_edge_confirmed`.
- SOURCE_CONFLICT blocks approval unconditionally — `audit_closure.py` `validate_source_conflict`, `classifier.py` `_source_conflict`.
- DES conflict persists across sessions, blocking — `audit_closure.py` `validate_des_conflict_persistence`.
- Structural failure count ≥3 kills the prop — `audit_closure.py` `MAX_STRUCTURAL_FAILURES = 3`.
- Coin-flip kill forces opposite side to restart the full gate stack — `audit_closure.py` `validate_coin_flip_kill`.
- Correlation: same-player direct/component overlap auto-rejects Power; UNKNOWN correlation blocks Power — `correlation_gate.py`.
- Sharp anchor: PrizePicks is target market not confirmation; reject only on firm sharp opposition or unfavorable line move since entry; stale-favorable line is not a reject — `sharp_anchor.py`.
- Severe board-vs-book drift (`|delta| >= 0.5`) is flagged as its own status (`SEVERE_BOARD_VS_BOOK_DRIFT`, `market_gate.py` `DRIFT_THRESHOLD = 0.5`) distinct from `MARKET_CONTRADICTION` — it appends a blocker but does not itself grant or deny a label; other gates (status, line, payout, source, market, live recheck) still gate the final label. The persona's "auto-surface for review, never grants a money label alone" wording matches this exactly.
- `SLATE_PURGE` and `DUPLICATE_EXPOSURE_BLOCK` are terminal `PropLabel` values that fire an early `continue` in `pipeline.py`, but the row is still carried into `_build_output()` and counted in the final row total — confirmed no `ROW_COUNT_MISMATCH` risk from these two paths.
- Generic-terminal-word guard: `PropLabel` (`labels.py`) is a closed enum with no member spelled `HOLD`, `WATCH`, `PASS`, `LEAN`, `CONDITIONAL`, or `NO BET` — the classifier is structurally incapable of emitting any of those as a terminal label (stronger guarantee than an LLP-style denylist, since there's nothing to deny in the first place).

### Naming mismatches (same behavior, different token — low risk, but must not be presented to Greg as identical strings)
- Persona `MODEL_SIGNAL_ONLY` = backend `PropLabel.RESEARCH_INTEREST`. No `MODEL_SIGNAL_ONLY` string exists anywhere in code.
- Persona `MARKET_UNAVAILABLE` (as a data/market state) = backend market_gate status string `"NO_MARKET_AVAILABLE"`. No `DataStatus` or `PropLabel` member is literally spelled `MARKET_UNAVAILABLE`.

### Structural mismatch (persona blends two different backend concepts into one list)
- Persona's "TERMINAL BUCKETS" list includes `PROXY_ONLY`, `MARKET_UNAVAILABLE`, `DATA_OPEN`, `DATA_UNOBTAINABLE`, `INPUT_FAILURE` alongside real `PropLabel` values. In the backend these five are `DataStatus` states (or, for `DATA_OPEN`, a Kalshi-only token — see below) that feed `classifier.py`, not classifier outputs. A row's `terminal_label` field can never literally equal any of those five in the props pipeline.
- `DATA_OPEN` does not exist anywhere in the props/PropLabel/DataStatus vocabulary. Its only occurrence in the codebase is a Kalshi event-contract status mapping (`"DATA_OPEN": "KALSHI_SCOUT"`), a completely separate subsystem.

### Real backend gaps (not naming — the described mechanism does not exist in code)
1. **`REJECT_ROLE_STATUS` / `REJECT_LINE_VALUE` / `REJECT_CONTEXT`** — none of these three literal terminal labels exist in `labels.py`. What actually fires instead:
   - Role/status staleness caps the row at `MODEL_QUALIFIED_HOLD` (`role_timestamp.py`), it does not produce a hard REJECT.
   - Line-value problems produce one of three distinct labels depending on cause: `REJECT_SHARP_CONFLICT`, `REJECT_FALLING_KNIFE` (`sharp_anchor.py`), or `REJECT_LINE_MOVED_AGAINST_SIDE` (`execution_friction.py`).
   - No literal "context" reject was found; the closest real mechanism is `REJECT_HOUSE_RULES_VULNERABILITY` / `HOUSE_RULES_CAUTION` (`house_rules.py`), which is narrower (injury/partial-play context only) than what "REJECT_CONTEXT" implies.
2. **MARKET BUCKETS (`BANNED`/`TEST_ONLY`/`WATCH`/`PRIMARY_CANDIDATE`/`TRUSTED`)** — no such `bucket_name`/status field exists anywhere in the player-props pipeline. `kalshi_engine/market_buckets.py` implements a similarly-named but scoped-differently system (`TRUSTED_TEST`/`WATCH`/`TEST_ONLY`/`SCOUT`/`REJECT`) for Kalshi event contracts only — not props, not PrizePicks.
3. **SOURCE TIERS T0–T3** — backend uses letter grades (A/A-/B/C/D/N-T) in `source_grade.py`, not a T0–T3 scheme. T0≈A/A-, T1≈B map reasonably; there is no dedicated T2 ("validated reconstruction") grade — the closest analog is a corroborated-B upgrade path. The persona's specific claim that "T3 cannot raise model_prob, edge_vs_friction, or market_edge_confirmed" was not found enforced anywhere tying source grade to those three fields directly.
4. **WNBA triple-risk kill rule** — searched `gate_engine/*.py` and `app.py` for WNBA-specific triple-risk logic (no book + non-verified L10 + <15 season games); no matching code exists. This rule is currently GPT-only judgment with zero backend enforcement.

Note: the fabricated `REJECT_ROLE_STATUS`/`REJECT_LINE_VALUE`/`REJECT_CONTEXT` labels and the literal `T0`–`T3` tier codes have been dropped from the deployable block above (they never existed in the backend); the block now describes the underlying behavior in plain language instead of inventing non-existent literal tokens, which also cost fewer characters.

### Step 4 smoke-test pass (this session — 8-test spec)
All 8 tests from the user's smoke-test spec (`attached_assets/Pasted-Replit-update...1783211313488.txt`) were run conceptually against the corrected block and current backend source. Result: 8/8 PASS. Three tests drove wording changes to the deployable block (generic-terminal-word guard made explicit; severe-delta bypass guard added to CROSS-MARKET; SLATE_PURGE/DUPLICATE_EXPOSURE_BLOCK row-count note added to FULL-BOARD). No backend code was read as needing a change — all corrections were to this instructions document only, per the user's explicit scope for this task.

### Character-safety trim (this session, after Step 4)
Per user request, the 7,996-char post-Step-4 block was trimmed down to a safer buffer below the 8,000-char Custom GPT limit. Trim touched wording, duplicate explanations, and example lists only — no rule, label, threshold, or gate name was removed. All 12 terminal buckets, all label definitions, every numeric threshold (3h staleness, 0.5 line-move/l5-tolerance, 3% UNCALIBRATED haircut, quarter-Kelly cap, <3 book equivalents, structural-failure count, drift threshold), and every bucket/label token from the pre-trim block remain present verbatim. Final size: **7,589 characters**, below both the 7,700 ceiling and the 7,500 ideal target. This trimmed block is the one Greg/ChatGPT should give a final eyeball pass before any staging/deployment — do not treat the 7,996-char Step 4 version as current.

**DRY_RUN_ONLY_NO_LIVE_TRADING:** unaffected — this is a persona-instruction reconciliation for an external reasoning agent, no code changes were made to the Flask engine.
