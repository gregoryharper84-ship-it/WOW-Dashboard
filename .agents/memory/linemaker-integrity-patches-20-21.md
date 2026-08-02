---
name: Linemaker integrity patches #20 and #21
description: Patch #20 (LLP matchup/EV/pipeline integrity) + Patch #21 (slip construction integrity) from Linemaker round 2–3 audit findings. Rules, new labels, and wiring status.
---

# Linemaker Integrity Patches #20 and #21

**Patch count after these:** 21
**Governance hash:** changed — must update Custom GPT gate-engine action schema expected_governance_hash.

---

## Patch #20 — WOW-PATCH-2026-08-02-LLP-MATCHUP-EV-INTEGRITY (precedence 99)

Module: `gate_engine/llp_matchup_ev_integrity.py`

Five rules (analytical only — no pipeline wiring yet, see task #61):
1. **SMALL-SAMPLE MATCHUP FLOOR** — BvP/H2H sample <25 PA cannot be primary driver; violation → WATCH cap + `l5-l10-overtrusted`
2. **ABSENCE-OF-DATA NEUTRALITY** — "zero career matchups" = DATA_UNAVAILABLE, never a negative signal; violation → `reasoned-not-modeled`
3. **EV-CLAIM AUDIT GATE** — EV % must carry model_prob + fair_odds + book + timestamp; missing any → REJECTED from output + `missing-projection-support`
4. **VARIANCE-VS-SAFETY SEPARATION** — substitution pitched as "safer" while lowering hit-prob LB → VARIANCE_INCREASE label
5. **UPSTREAM DEPENDENCY LOCK** — dependent step cannot report results if upstream is incomplete/running/timed-out → PIPELINE_INTEGRITY_FAILURE + `dropped=True`

New labels: `PIPELINE_INTEGRITY_FAILURE`, `VARIANCE_INCREASE`

---

## Patch #21 — WOW-PATCH-2026-08-02-LLP-SLIP-CONSTRUCTION-INTEGRITY (precedence 100)

Module: `gate_engine/llp_slip_construction.py`

Three rules (analytical only — no pipeline wiring yet, see task #61):
1. **CROSS-BOOK PARLAY DETECTION** — legs spanning multiple books/exchanges + parlay language → CROSS_BOOK_PARLAY_ILLUSION; these are structurally independent single bets
2. **SAME-GAME CORRELATED STACK** — ML + player prop from same team/game where prop rationale depends on ML outcome → SAME_GAME_CORRELATED_STACK (extends Section 27.1 / wow-correlation-guard)
3. **SELECTIVE RECENCY CONSISTENCY** — recency override without citing WOW rule + stale_data_reason → SELECTIVE_RECENCY_APPLIED

New labels: `CROSS_BOOK_PARLAY_ILLUSION`, `SAME_GAME_CORRELATED_STACK`, `SELECTIVE_RECENCY_APPLIED`

---

## Wiring status

Both modules exist as pure functions but are NOT yet called from any scoring route or pipeline step. Task #61 covers this. Until wired, rules are enforced only when callers explicitly invoke the modules.

## New Section 32 failure tags (5 total)

All five are in `gate_engine/labels.py`. External Claude session needs to formally add them to the LLP spec Section 32 tag library.

## Governance hash reminder

21 active patches means the MANIFEST_GOVERNANCE_HASH changed at startup. Custom GPT gate-engine action schema YAML needs `expected_governance_hash` updated (task #62). GET /wow/engine/health returns the current value.
