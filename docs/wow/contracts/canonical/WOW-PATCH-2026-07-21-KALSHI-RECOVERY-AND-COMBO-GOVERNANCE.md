# WOW-PATCH-2026-07-21-KALSHI-RECOVERY-AND-COMBO-GOVERNANCE

**Canonical Authority Document**
**Status:** EMERGENCY_ACTIVE
**Activation date:** 2026-07-21
**Lane:** KALSHI_PORTFOLIO_GOVERNANCE
**Framework:** WOW_v16_CLEAN_CORE
**Patch priority:** CRITICAL
**can_execute:** false
**capital_allocation:** false

---

## Authority Statement

This document formally registers the Kalshi Portfolio Recovery and Combo
Governance emergency authority that has been in continuous enforcement since
2026-07-21. The enforcement mechanism is the module-level constant
`KALSHI_RECOVERY_MODE: str = "ACTIVE"` in
`gate_engine/command_center/cc_labels.py` and `gate_engine/command_center/__init__.py`.

Registration through this document does **not** newly activate any behavior.
The authority was already active before this document was created. This document
resolves `UNRESOLVED-002` in the contract registry by providing the formal
governance record for that pre-existing enforcement.

---

## Enforcement Mechanisms

### 1. KALSHI_RECOVERY_MODE constant

```python
KALSHI_RECOVERY_MODE: str = "ACTIVE"
```

Defined at module level in `gate_engine/command_center/cc_labels.py`. This
constant is unconditional and cannot be disabled at runtime. It is exported
through `gate_engine/command_center/__init__.py` and echoed in every
command-center run response under `kalshi_recovery_mode`.

### 2. Recovery mode cap label

`CC:KALSHI_RECOVERY_MODE_CAP` — applied to any row that would otherwise advance
through the Kalshi portfolio pathway during recovery mode. Blocks advancement
to market-execution stages.

### 3. Cross-engine contamination block

`CC:KALSHI_CROSS_ENGINE_CONTAMINATION_BLOCK` — prevents Kalshi-scored rows from
contaminating the main WOW gate-engine pipeline and vice versa during recovery.

---

## Invariants (Non-Negotiable)

| Invariant | Value |
|---|---|
| `can_execute` | `false` (unconditional) |
| `capital_allocation` | `false` (unconditional) |
| `combos_allowed` | `false` |
| `maximum_combo_underlying_markets` | 1 (singles only) |
| `recovery_mode` | `ACTIVE` |
| `recovery_exit_minimum_reconciled_singles` | 20 |
| Execution rule | `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS` |

### Recovery constraints

- **Singles only:** During recovery mode, only single-market Kalshi contracts
  are permitted to enter the research pipeline. Multi-leg combos are blocked.
- **Research ceiling:** All outputs are capped at research/advisory status.
  No row may advance to FINAL_APPROVED or MONEY_QUALIFIED through the Kalshi
  pathway while recovery mode is ACTIVE.
- **Recovery exit:** Recovery mode may only be exited after a minimum of 20
  singles have been reconciled and verified. Exit does **not** reactivate combos —
  combo governance remains subject to `WOW-PATCH-2026-07-10-COMBO-SETTLEMENT`.
- **No downstream erasure:** No downstream governance pass may erase the Kalshi
  portfolio blocker. The blocker propagates through all downstream stages.

---

## Relationship to Other Patches

| Patch | Relationship |
|---|---|
| `WOW-PATCH-2026-07-10-COMBO-SETTLEMENT` | Combo and settlement mechanics (Rules A–G). Kalshi combos remain blocked even if recovery exits; Rule A (max 1 underlying) is independently enforced. |
| `WOW-CORE-v16` | Base gate engine. This patch is a CRITICAL-priority overlay. |
| Current master governance | Remains highest authority. This patch operates within that framework and does not override master governance. |

**Precedence rule:** Newer active patches win only on direct conflict with this
document. The Kalshi portfolio blocker (`CC:KALSHI_RECOVERY_MODE_CAP`,
`CC:KALSHI_CROSS_ENGINE_CONTAMINATION_BLOCK`) must survive any such conflict.

---

## Registration Notes

This authority was implemented as a hardcoded constant rather than a dynamic
configuration flag because the severity of the risk event that triggered it
(2026-07-21 Kalshi portfolio event) required guaranteed enforcement with no
runtime path that could disable it. The hardcoded approach is intentional and
must not be refactored to a configurable flag without explicit architectural
review and a separate formal governance decision.

**Stop condition:** If any code path is identified that can set
`KALSHI_RECOVERY_MODE` to anything other than `"ACTIVE"` at module load time,
that code path must be treated as an EMERGENCY_ACTIVE governance violation and
escalated immediately.

---

## Verification Record

- Registration confirmed: 2026-08-16
- Resolution of: UNRESOLVED-002 (contract registry)
- Registered by: WOW-#251-KALSHI-EMERGENCY-RECOVERY-AUTHORITY-REGISTRATION
- Runtime behavior changed by registration: **NONE**
- `can_execute=false`
- `capital_allocation=false`
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`
