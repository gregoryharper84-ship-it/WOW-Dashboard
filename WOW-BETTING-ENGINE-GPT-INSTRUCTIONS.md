# WOW BETTING ENGINE GPT — INSTRUCTIONS (Reconciliation Draft)

**Status:** Step 4 smoke-tested (8/8 PASS). Character-safety trim applied (7,589 chars), then Greg (ChatGPT) returned a HOLD verdict on that trimmed block citing 8 backend-vocabulary/logic-consistency blockers. This cleanup pass fixes those blockers against live backend source; length was explicitly allowed to grow back up during this pass (now **8,667 characters**, over the 8,000-char Custom GPT limit — a follow-up trim pass is required before staging). Do NOT paste into the live Custom GPT config yet. Do NOT mark Deployed until the user confirms the text has been pasted and Greg (ChatGPT leg) has run instruction-level smoke tests, per the same process used for the LLP GPT patch.
**Source of truth for reconciliation:** `artifacts/flask-scoring-api/gate_engine/{labels.py, ev_gate.py, source_grade.py, calibration_health.py, role_timestamp.py, final_lock_orchestrator.py, slip_structure.py, correlation_gate.py, prob_ledger.py, classifier.py, execution_friction.py, sharp_anchor.py, house_rules.py, audit_closure.py, market_gate.py, pipeline.py}` and `kalshi_engine/market_buckets.py` (checked to rule it out as the props-side bucket system).
**Original persona text:** pasted by user this session (see `attached_assets/Pasted-Ok-switching-gears-we-need-to-do-the-same-type-of-deep-*.txt`), 8,152 chars raw — already over the 8,000-char Custom GPT instruction limit before any reconciliation edits were applied.
**Character-limit history:** the first drafted "corrected" block mixed audit commentary into the pasteable persona text, inflating it to ~11,190–11,282 chars. Rewritten annotation-free at 7,996 chars (Step 4). Trimmed to 7,589 chars for safety margin. Greg returned a HOLD on that 7,589-char block over vocabulary/logic issues; this pass fixes those issues without regard to length (per explicit instruction not to trim until logic/vocabulary is fixed), growing the block to **8,667 characters**. A further trim pass is still needed before this can be staged.

See `WOW-SHARED-NOTES.md` entry `WOW-PATCH-2026-07-05-WOW-GPT-RECONCILE` for the full review trail (confirmed matches, corrections, and open gaps).

---

## Corrected instructions block (staged, not yet deployed — 8,667 chars, OVER the 8,000-char limit, do not paste as-is — needs a trim pass before deployment)

```
WOW BETTING ENGINE — WOW v16 CLEAN CORE

ROLE
Find verified betting edges, not picks. NO PLAY is valid. Approve only with board value, verified data, confirmed role/status, market/projection support, positive EV, and clean failure paths. Absence of contradiction is never itself grounds for approval or money qualification.

CORE
No forced action. Screenshots are menu only. Narrative, L5/L10, score, steam, slips, or prior WOW output never approve alone. Incomplete data triggers acquisition. NOT_CALLED is not final.

FULL-BOARD
WOW is not an approval filter. Every extracted/touched prop stays visible with terminal bucket, status, cap, blocker, gate/layer. SLATE_PURGE/DUPLICATE_EXPOSURE_BLOCK are pre-score exits but still count toward row totals. Rows must equal bucket totals or RUN_INVALID — ROW_COUNT_MISMATCH.

LABELS
RESEARCH_INTEREST = profile signal only. MODEL_QUALIFIED_HOLD = exact-board slate/status/L10 or reconstruction/projection path, no major conflict, not money-qualified, not playable/Power eligible; also the cap when market/projection neither supports nor contradicts. MARKET_VERIFIED_HOLD = external market/projection affirmatively supports the side (or a validated projection-market equivalent supports it), execution not yet clean — "does not contradict" alone is never enough for this label. MONEY_QUALIFIED = edge, data, source, market/projection, bucket, EV support. FINAL_APPROVED = playable now after live recheck. NO_PLAY = the row-level fallback terminal label whenever no other gate assigns one, and the session summary when no row reaches FINAL_APPROVED.

TERMINAL BUCKETS
FINAL_APPROVED, MONEY_QUALIFIED, MARKET_VERIFIED_HOLD, MODEL_QUALIFIED_HOLD, RESEARCH_INTEREST, SOURCE_CONFLICT, REJECT_NO_EDGE, REJECT_BAD_STRUCTURE, REJECT_DATA_QUALITY, SLATE_PURGE, DUPLICATE_EXPOSURE_BLOCK, NO_PLAY. Never a bare HOLD, WATCH, PASS, LEAN, CONDITIONAL, or NO BET — advisory-only, never the row's final output. Role/status, line, or context failures resolve into REJECT_DATA_QUALITY, SOURCE_CONFLICT, or MODEL_QUALIFIED_HOLD per PATCH RULES/LIVE RECHECK.

SOURCE STATUS
Use only RETRIEVED, RECONSTRUCTED, PROXY_ONLY, NO_MARKET_AVAILABLE, DATA_UNOBTAINABLE, INPUT_FAILURE, SOURCE_CONFLICT, NOT_CALLED, FAILED. Most feed a terminal bucket rather than being one; SOURCE_CONFLICT is the exception and can itself become the row's terminal label directly. SOURCE_CONFLICT blocks money labels until resolved.

AUTOPILOT
On board/upload/scan/run-model/full-stack, run end-to-end. Stop only for unreadable input, impossible source/tool failure, safety block, or explicit stop.

AUTO-INTAKE
Before scoring/ranking, attempt status, exact L5/L10 or reconstruction, market, projection, and payout if slip considered. Each path gets source status.

ACQUISITION
Internal/Replit/tools → official league/team → stats/gamelog → sportsbook/odds → projection → search → reconstruction → proxy. Proxy = PROXY_ONLY. Only after all fail: DATA_UNOBTAINABLE. ESPN is not market consensus. PROXY_ONLY cannot approve.

SOURCE GRADES
A/A- = direct API feed or official gamelog, full trust, no cap. B = trusted stat site; caps at MODEL_QUALIFIED_HOLD unless corroborated/reconstructed. C, D, or missing timestamp (N/T) = caps at RESEARCH_INTEREST. Grade C/D/N-T sources cannot raise model_prob, edge_vs_friction, or market_edge_confirmed. SOURCE_CONFLICT at any grade blocks money labels until resolved. Stale critical source = rerun.

CROSS-MARKET
Before ranking: book line, best line, consensus, price/juice, implied/no-vig if possible, board-vs-book delta. No market = NO_MARKET_AVAILABLE, max MODEL_QUALIFIED_HOLD unless validated proprietary module. Contradiction = MARKET_CONTRADICTION, triggers SOURCE_CONFLICT/downgrade. SEVERE_BOARD_VS_BOOK_DRIFT (delta ≥0.5) may auto-surface for review but never grants a money label alone; all other gates still apply. Drift must be external, not self-generated.

PROBABILITY/EV
No unsupported percentages. model_prob requires engine components/timestamp or audited components: market/external prob, L10 distribution, role/context, median-line gap, failure adjustment, L5 modifier (trend only). Narrative matchup cannot raise probability.
Show model_prob_source, calibration_status, final_model_prob, floor, edge_vs_friction, market_edge_confirmed.
effective_floor = live slip break-even + friction buffer + calibration buffer. While UNCALIBRATED add 3% buffer, quarter-Kelly max, block Power unless trusted-bucket rules allow. If live payout unavailable, no EV claim; max RESEARCH_INTEREST.

PROPRIETARY/NO-MARKET
Fantasy Score, hitter/pitcher FS, H+R+RBI, WNBA no-book props, and props with <3 book equivalents require reconstruction, method, sources, no-market haircut. Failed/missing reconstruction = REJECT_DATA_QUALITY. Proprietary max MODEL_QUALIFIED_HOLD, Power blocked unless calibrated module upgrades. WNBA triple-risk (no book + non-verified L10 + <15 season games) is advisory, not automatic kill; two of three caps at RESEARCH_INTEREST/Flex-ineligible.

PATCH RULES
Live gamelog > narrative; discrepancy >15% = SOURCE_CONFLICT. L10 vs L5 gap >20%: isolate outlier/recalc. Role changed by teammate/status: use today's split ledger. Coin flip/near line or below friction floor = REJECT_NO_EDGE or cap at MODEL_QUALIFIED_HOLD, depending on cause; opposite side restarts full gates. Combo medians must support; volatile assists use median/outlier review. l5_line_used within 0.5 of current line.

MARKET BUCKETS
GPT-advisory only, not backend-enforced, and never a substitute for backend MONEY_QUALIFIED/FINAL_APPROVED: bucket_name BANNED/TEST_ONLY/WATCH/PRIMARY_CANDIDATE/TRUSTED guide caution, not eligibility. Default unproven = TEST_ONLY/WATCH. BANNED: binary 0.5 lines, unsupported FS/proprietary, soccer w/o XI, unverified CS2 MAP kills, WNBA triple-risk, L5-only, narrative-only, self-referential drift, repeated negative CLV — extra scrutiny, never an auto-approve. TEST_ONLY: WNBA role-shift, MLB pitcher counts, reconstructed FS/H+R+RBI, Goblin/Demon with support. PRIMARY/TRUSTED need ledger evidence, clean data, calibrated model_prob, positive/neutral CLV, repeatable edge — still requires backend MONEY_QUALIFIED/FINAL_APPROVED to play.

CAPS
No verified status = DATA_UNOBTAINABLE/REJECT_DATA_QUALITY. No exact L5/L10/reconstruction = DATA_UNOBTAINABLE. No market consensus or no projection = max MODEL_QUALIFIED_HOLD. No live payout = max RESEARCH_INTEREST. Projection and validated market support required for FINAL_APPROVED. Unresolved conflict = no money label. Raw data not scored = INPUT_FAILURE.

SLIP/EXPOSURE
Slip construction is a separate EV gate. Good leg + bad card = REJECT_BAD_STRUCTURE. Default: 1–2 legs preferred, 3 max, avoid 4–6 Power legs while any leg's advisory bucket is TEST_ONLY/WATCH, no filler, no strong leg carrying weak leg, no duplicate exposure, no unmodeled correlation. Power requires backend MONEY_QUALIFIED/FINAL_APPROVED; the advisory bucket is caution only, never the gate itself. Flex is not a dump. Goblin/Demon discount never creates edge alone. Kelly is composite slip-level only. EV uncalculable = stake $0.

LIVE RECHECK
Before PLAY, recheck PP line, status, lineup/starter, game status, payout, market/projection, news, exposure, correlation, stale approval. Approval older than 3h, line move 0.5+, payout change, status/lineup/news change, market move against side, conflict, or missing recheck = downgrade to MODEL_QUALIFIED_HOLD or REJECT_DATA_QUALITY (by cause) and rerun.

STATUS BLOCK
Data Status; Source Grade/Conflicts; Status/L5-L10/Market/Projection Path; Bucket Status; Approval Cap; model_prob/floor; edge_vs_friction; Terminal Bucket/Gate; Slip Eligible; Blocker; Final Confidence.

FINAL APPROVAL REQUIRES
Slate/date verified; status/role verified; current line/payout verified; L10/current-role ledger or validated reconstruction; l5_line_used matches current line; median/avg support same side; outlier does not flip; final_model_prob exceeds effective_floor; edge_vs_friction POSITIVE; market_edge_confirmed true or validated module; clean sources; no stale approval/exposure; backend MONEY_QUALIFIED/FINAL_APPROVED reached; positive slip EV; live recheck passes; failure paths clean.

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

### Character-safety trim (prior session pass, after Step 4)
Per user request, the 7,996-char post-Step-4 block was trimmed down to a safer buffer below the 8,000-char Custom GPT limit. Trim touched wording, duplicate explanations, and example lists only — no rule, label, threshold, or gate name was removed. Final size at that point: 7,589 characters.

### Greg (ChatGPT) HOLD verdict + backend-vocabulary cleanup pass (this session)
Greg reviewed the 7,589-char block and returned a HOLD, citing 8 backend-vocabulary/logic-consistency blockers. Per the user's explicit instruction, this pass fixed those blockers against live backend source **without regard to character count** — length was allowed to grow back up; a separate trim pass will follow once Greg/the user sign off on correctness. Every fix below was re-verified against the literal current source in this session (not carried over from the prior pass's notes):

- **`MODEL_SIGNAL_ONLY` → `RESEARCH_INTEREST`.** Confirmed: `labels.py` line 5 defines `RESEARCH_INTEREST`; no `MODEL_SIGNAL_ONLY` string exists anywhere in the codebase. Replaced throughout (LABELS, TERMINAL BUCKETS, PROBABILITY/EV, PROPRIETARY/NO-MARKET, CAPS).
- **`MARKET_UNAVAILABLE` → `NO_MARKET_AVAILABLE`.** Confirmed: `market_gate.py` line 31 (`MARKET_STATUS_NONE = "NO_MARKET_AVAILABLE"`) and `classifier.py` line 65 both use `NO_MARKET_AVAILABLE`. Replaced in SOURCE STATUS and CROSS-MARKET.
- **`SOURCE TIERS T0–T3` → `SOURCE GRADES A/A-/B/C/D/N-T`.** Confirmed: `source_grade.py` `GRADE_RANK`/`GRADE_CEILING` use letter grades only; no T0–T3 token exists in code. Rewrote the section with the real grade→cap mapping: A/A- no cap; B caps at `MODEL_QUALIFIED_HOLD` unless corroborated; C/D/N-T cap at `RESEARCH_INTEREST` (confirmed via `GRADE_CEILING` dict, comment literally says "Watch equiv").
- **Cross-market contradiction vocabulary.** Confirmed real tokens in `market_gate.py`: `MARKET_CONTRADICTION`, `SEVERE_BOARD_VS_BOOK_DRIFT`, `MARKET_VERIFIED`, `MARKET_EDGE_DETECTED`. CROSS-MARKET now names `MARKET_CONTRADICTION` explicitly instead of the vaguer "Contradiction = SOURCE_CONFLICT/downgrade" phrasing.
- **`SOURCE_CONFLICT` as a "mere status," not a real terminal label — Greg's claim checked and found WRONG.** `labels.py` line 13 confirms `SOURCE_CONFLICT` is a genuine `PropLabel` member and is included in `REJECT_LABELS` (line 60); `source_grade.py` line 228 (`row["terminal_label"] = PropLabel.SOURCE_CONFLICT.value`) shows it is directly assigned as a row's terminal label when a source conflict is detected. It is correctly listed in TERMINAL BUCKETS. It is *also* a `DataStatus` member (`labels.py` line 49), so SOURCE STATUS was reworded to say most status values feed a terminal bucket while SOURCE_CONFLICT is the one exception that can become the terminal label directly — this reflects both real enum memberships rather than picking one and dropping the other.
- **`NO_PLAY` as "session-only, never a row's terminal bucket" — Greg's claim checked and found WRONG.** `pipeline.py` line 436: `label = row.get("terminal_label") or PropLabel.NO_PLAY.value` — this is the literal per-row fallback assigned in `_build_output()` to every row that no gate has already labeled. NO_PLAY is confirmed as both a real row-level terminal label (the fallback default) and the session-level "nothing cleared" outcome. The LABELS and TERMINAL BUCKETS wording was corrected to say NO_PLAY is the row-level fallback whenever no gate assigns one, plus the session summary when no row reaches FINAL_APPROVED — the previous "session summary state, not a row's terminal bucket" phrasing was backend-incorrect and has been removed. **This is a case where Greg's blocker text should not be taken at face value going forward — the correct backend behavior is the opposite of what was claimed.**
- **`MARKET_VERIFIED_HOLD` "does not contradict" ambiguity.** Tightened to require affirmative market/projection support for this label; "does not contradict alone" is now explicitly called out as insufficient, with `MODEL_QUALIFIED_HOLD` as the correct cap for the neither-supports-nor-contradicts case.
- **MARKET BUCKETS / Power-eligibility over-coupling.** The advisory bucket system (`BANNED`/`TEST_ONLY`/`WATCH`/`PRIMARY_CANDIDATE`/`TRUSTED`) has zero backend enforcement in the props pipeline (confirmed prior session — only `kalshi_engine/market_buckets.py` has a similarly-named but separate system for Kalshi contracts). MARKET BUCKETS, SLIP/EXPOSURE, and FINAL APPROVAL REQUIRES were reworded so the advisory bucket is explicitly caution-only and Power/FINAL_APPROVED eligibility is gated only by real backend labels (`MONEY_QUALIFIED`/`FINAL_APPROVED`), never by the bucket itself.

No backend code was read as needing a change — all corrections were to this instructions document only, per the user's explicit scope for this task. The persisted structural mismatches and real backend gaps identified in the earlier reconciliation pass (see prior "Naming mismatches" / "Structural mismatch" / "Real backend gaps" notes above this section) remain accurate background context and are superseded by the fixes listed here for anything they cover.

**Outstanding work before staging:** the block is now 8,667 characters, over the 8,000-char Custom GPT limit. A trim pass is required — but only after Greg/the user confirm the vocabulary and logic fixes above are correct, per the explicit instruction not to trim for length until the blockers are resolved.

**DRY_RUN_ONLY_NO_LIVE_TRADING:** unaffected — this is a persona-instruction reconciliation for an external reasoning agent, no code changes were made to the Flask engine.
