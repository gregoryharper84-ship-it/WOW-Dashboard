# WOW Results Log

Formal postmortem record for settled slips. Each row links a real outcome to the engine
state at the time — whether the model was correctly calibrated, whether a patch was
triggered, and what the future rule is. Append-only; newest entries at the bottom.

---

## Entry format

| Field | Description |
|---|---|
| Date | Slip settlement date |
| Player | Player name |
| Market | Stat type + direction + line |
| Result | Actual outcome — HIT or MISS |
| Scored outcome | What the engine returned (terminal_label, blockers) at scoring time |
| Variance vs. model error | Whether the miss was within the model's stated probability or a gate failure |
| Patch Needed | Yes / No / Already applied |
| Future Rule | What rule or gate change prevents the same ambiguity next time |

---

## 2026-08-04 — PrizePicks Flex slip (settled)

**Slip:** 5-pick Flex, 2026-08-04

| Player | Market | Result | Notes |
|---|---|---|---|
| Sean Manaea | Pitcher Strikeouts MORE 3.5 | HIT (7 Ks) | Clean |
| Grant Holmes | Pitching Outs MORE 14.5 | HIT (18 outs) | Clean |
| Logan Henderson | Pitching Outs MORE 14.5 | HIT (18 outs) | Clean |
| Jared Jones | Pitching Outs MORE 14.5 | **MISS (12 outs, pulled at 4.0 IP)** | See postmortem below |

**Jared Jones postmortem:**

- **Scored outcome:** `MODEL_QUALIFIED_HOLD` (the standard Outs-MORE ceiling under PATCH-015)
- **Variance vs. model error:** Ambiguous at scoring time — `MODEL_QUALIFIED_HOLD` was the
  output regardless of whether `required_out_survival_lower_bound` was computed and cleared
  the 0.65 floor, or was simply `None` and fell through silently. The ledger row was
  identical in both cases.
- **Root cause found:** `_apply_outs_more_gate()` in `gate_engine/mlb_directional_firewall.py`
  had no Rule 0 guard. A `None` survival lower bound skipped all three rules (low-prob,
  conditional-as-unconditional, clean-pass) and landed on the same `MODEL_QUALIFIED_HOLD`
  outcome as a row whose probability was computed and cleared. Missing data and passing
  data were indistinguishable in the output.
- **Patch Needed:** Yes — applied
- **Patch:** `WOW-PATCH-2026-08-04-OUTS-MORE-MISSING-SURVIVAL-DATA` (deployed commit
  `a768274`, 2026-08-05)
- **Future Rule (Rule 0):** If `required_out_survival_lower_bound` is `None` or
  non-numeric at gate entry, the row routes to `REJECT_DATA_QUALITY` +
  `MLB_OUTS_MORE_SURVIVAL_DATA_MISSING` blocker. `MODEL_QUALIFIED_HOLD` is now only
  reachable by rows where the survival probability was actually computed.
- **Whether this miss would recur:** The gate correctly rejects on missing data now.
  Whether Jones's actual survival probability (had it been computed) would have cleared
  0.65 cannot be determined retroactively; that is the remaining open uncertainty.

---
