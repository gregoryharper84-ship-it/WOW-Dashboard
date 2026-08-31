# gate_engine enforcement check — Outs-MORE workload-survival gate

## What I was checking

You asked whether `mlb_directional_firewall.py` actually enforces
`required_out_survival_lower_bound`, prompted by the Jared Jones postmortem
where it was unclear if his loss was variance or a gate that never ran.

## What I found

It's enforced — mostly. `_apply_outs_more_gate()` has three real rules
(below-floor block, conditional-treated-as-unconditional block,
workload-restriction-unresolved cap). But all three are gated on
`p_survival_lb is not None`. When the field is `None`, nothing fires, and
the row falls through to the same `MODEL_QUALIFIED_HOLD` ceiling as a row
that actually cleared the 0.65 floor. Missing data and passing data were
indistinguishable in the output. Reproduced this directly, not inferred.

There was also zero test coverage on this module before today — nothing
would have caught it.

## The fix

Added a Rule 0 ahead of the existing rules: `p_survival_lb is None` now
routes to `REJECT_DATA_QUALITY` with a new blocker tag
(`MLB_OUTS_MORE_SURVIVAL_DATA_MISSING`) instead of falling through. 13 new
tests, including one that specifically proves missing-data and
passing-data now produce different terminal labels.

## Verified

- `git apply --check` / `git apply` succeed cleanly against a
  reconstructed copy of the live module.
- `pytest gate_engine/tests/test_mlb_directional_firewall.py -q` → 13/13
  pass.
- Rules 1–3 and the clean-passing path are unchanged (regression tests
  cover each explicitly).
- `can_execute` isn't touched anywhere in the diff.

## What this doesn't tell you

Whether Jones's row specifically had a missing or a computed-but-wrong
survival value — that's a ledger question, not a code question. This
patch just means the next time the field is missing, it gets rejected
instead of silently promoted.

## Files

- `task_outs_more_missing_data.patch` — the diff (firewall fix + new
  test file).
- `WOW-PATCH-2026-08-04-OUTS-MORE-MISSING-SURVIVAL-DATA.md` — the formal
  patch doc, `v16 test candidate` label, ready for legacy platform Agent.
