# WOW-PATCH-2026-08-04-OUTS-MORE-MISSING-SURVIVAL-DATA

## Status

```text
PROPOSED
patch_priority=HIGH
framework=WOW_v16_CLEAN_CORE
label=v16 test candidate
```

This is a proposal, not an active rule. It has not been applied to the live
legacy platform deployment — code, tests, and verification below are ready to hand
to legacy platform Agent, but activation is a decision for the human operator per
project governance (Claude is not decision authority).

## Purpose

Postmortem on a settled 5-pick PrizePicks Flex, 2026-08-04:

```text
Sean Manaea      Ks MORE 3.5   → 7    HIT
Grant Holmes     Outs MORE 14.5 → 18  HIT
Logan Henderson  Outs MORE 14.5 → 18  HIT
Jared Jones      Outs MORE 14.5 → 12  MISS (pulled after 4.0 IP, one out short)
```

The postmortem could not determine, from chat alone, whether Jones's loss
was legitimate variance (a correctly-computed survival probability that
simply missed) or a model error (the workload-survival gate never ran).
Auditing `gate_engine/mlb_directional_firewall.py` — the module that is
supposed to enforce exactly this check under PATCH-015
(`WOW-PATCH-2026-07-30-WNBA-COMPOSITE-MLB-DIRECTIONAL-AND-CROSS-TICKET-GOVERNANCE`)
— found a real gap that makes the question unanswerable from the ledger too,
for any row affected by it.

## The gap

`_apply_outs_more_gate()` reads `required_out_survival_lower_bound` and
runs three rules against it (below-floor, conditional-as-unconditional,
workload-unresolved). All three are conditioned on the value being present.
When the field is `None` — i.e. never computed or never passed through —
**none of the three rules fire**, and the row falls through to the same
`MLB_OUTS_MORE_HOLD` → `MODEL_QUALIFIED_HOLD` ceiling as a row whose
survival probability was actually computed and cleared the 0.65 floor.

Reproduced directly against the module:

```text
row = {sport: MLB, stat_type: "pitching outs", direction: MORE}   # no survival field at all
run(row)
→ terminal_label = MODEL_QUALIFIED_HOLD
→ blockers = []
```

Missing data and passing data currently produce an identical, silent
outcome. This violates the project's own stated data-integrity rule
(`"NOT_CALLED" ≠ "NOT_AVAILABLE"`; "No approval without exact-line
support") and the pattern already used elsewhere in the codebase —
`failure_path.py`'s `DATA_CONTRACT_FAIL` treats a missing/abstract
required field as a hard stop, not a silent pass-through. This module
never got the equivalent treatment for its own required field.

No test file exists for `mlb_directional_firewall.py` at all, which is
why this has never been caught.

## Non-Negotiable Governance

```text
WOW_VERSION=WOW_v16_CLEAN_CORE
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
can_execute=false
```

`can_execute` is not referenced by this patch. Confirmed by inspection of
the diff: no line touches `can_execute` in `mlb_directional_firewall.py`,
`labels.py`, or the new test file.

---

# PATCH — Rule 0: Missing Survival Data Fails Closed

**Status:** PROPOSED
**Owner:** `gate_engine.mlb_directional_firewall`
**Target:** `_apply_outs_more_gate()`

## Problem

```text
p_survival_lb = None
→ Rule 1 (below-floor): skipped, condition requires p_survival_lb is not None
→ Rule 2 (conditional-as-unconditional): False by default, skipped
→ Rule 3 (workload-unresolved): False by default, skipped
→ falls to else branch: _apply_ceiling(row, MODEL_QUALIFIED_HOLD)
→ SAME outcome as a row that actually cleared the 0.65 floor
```

## Fix

Add Rule 0, checked before Rule 1: if `required_out_survival_lower_bound`
is `None` or non-numeric, do not fall through. Set:

```text
terminal_label = REJECT_DATA_QUALITY   (existing label, already in REJECT_LABELS)
blockers += ["MLB_OUTS_MORE_SURVIVAL_DATA_MISSING"]
directional_forward_test_status = "MLB_OUTS_MORE_SURVIVAL_DATA_MISSING"
```

`REJECT_DATA_QUALITY` is reused rather than inventing a new `PropLabel`
because it's the existing general-purpose data-quality reject label,
already terminal (protected from downgrade by `_apply_ceiling`'s existing
`current.startswith("REJECT")` guard), and semantically exact.

## Behavior Unchanged

Rules 1, 2, 3 and the clean-passing path are untouched. Verified by 13
regression tests (new file, module previously had zero test coverage):

```text
test_missing_survival_data_fails_closed              — the core fix
test_missing_survival_data_distinct_from_passing_case — the actual regression
test_non_numeric_survival_value_treated_as_missing
test_below_floor_survival_probability_blocks          — unchanged
test_at_floor_survival_probability_passes              — unchanged
test_custom_floor_respected                            — unchanged
test_conditional_as_unconditional_blocks               — unchanged
test_workload_restriction_unresolved_caps_at_hold       — unchanged
test_clean_passing_row_capped_at_hold_not_rejected      — unchanged
test_can_execute_always_false                           — unchanged
test_detects_outs_more_lane / test_detects_k_less_lane /
  test_non_mlb_row_is_not_pitcher_prop                  — lane detection, unaffected
```

All 13 pass against the patched module. `git apply --check` and
`git apply` succeed cleanly against a reconstructed copy of the current
`mlb_directional_firewall.py`, verified byte-identical to the tested
version.

## What this patch does NOT do

It does not determine whether Jared Jones's specific miss was variance or
model error — that requires pulling his actual ledger row and checking
whether `required_out_survival_lower_bound` was populated at pick time.
If it was populated and Jones's value legitimately cleared 0.65, this
patch changes nothing about that outcome (Rule 0 only fires on `None`).
If it was `None`, this patch means the *next* occurrence of that gap
gets caught immediately instead of silently qualifying.

It also does not retroactively re-grade any already-settled row — this is
forward-looking gate enforcement only.

## Priority Justification: HIGH not MEDIUM

Upgraded from the postmortem's original MEDIUM (taxonomy-only) estimate
once the code audit found this is a live, exploitable gap in the actual
gate that governs live promotion of Outs-MORE props — not just a missing
label in a postmortem classification table. Every Outs-MORE row currently
missing this field is being silently promoted to `MODEL_QUALIFIED_HOLD`.

## Acceptance Tests (already passing, see above)

1. A row with no `required_out_survival_lower_bound` → `REJECT_DATA_QUALITY`,
   not `MODEL_QUALIFIED_HOLD`.
2. The same row structure with a valid survival value → unchanged
   `MODEL_QUALIFIED_HOLD` behavior (proves the fix is additive, not
   destructive).
3. A non-numeric survival value (e.g. a string) → treated as missing, not
   silently coerced or ignored.
4. Rules 1–3 (below-floor, conditional-as-unconditional,
   workload-unresolved) behave identically to before the patch.
5. `can_execute` remains `False` in every branch, missing-data or not.

## Activation Prompt

> Apply WOW-PATCH-2026-08-04-OUTS-MORE-MISSING-SURVIVAL-DATA to
> gate_engine/mlb_directional_firewall.py. Add Rule 0 to
> _apply_outs_more_gate(): a missing or non-numeric
> required_out_survival_lower_bound must REJECT_DATA_QUALITY with blocker
> MLB_OUTS_MORE_SURVIVAL_DATA_MISSING, not fall through to the standard
> MLB_OUTS_MORE_HOLD ceiling. Add the accompanying test file. Run
> python -m pytest gate_engine/tests/ -x -q before merge. can_execute
> remains unconditionally False throughout.

---

## One-Line Definition

**This patch closes a silent fail-open gap in the MLB Outs-MORE
workload-survival gate: a prop is no longer promoted to
MODEL_QUALIFIED_HOLD when its required survival probability was never
computed — it now fails closed to REJECT_DATA_QUALITY instead.**
