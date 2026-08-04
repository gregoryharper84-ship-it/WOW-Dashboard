# WNBA Enrichment Key Contract

**Effective:** August 3, 2026  
**Scope:** Any scoring request that includes WNBA player-prop rows.

---

## Two separate enrichment keys — never mix them

| Key | Type | Consumed by | Purpose |
|-----|------|-------------|---------|
| `game_log` | `list[number]` | `l5_l10_ledger` (L5/L10 gate) | Raw per-game stat values for rolling average scoring |
| `box_score_log` | `list[dict]` | `opportunity_engine` (WNBA opportunity gate) | Per-game dicts for role/stability analysis |

These keys used to share the same name (`game_log`). They were split because the two modules require incompatible formats — sending dicts to `l5_l10_ledger` caused a silent data-quality reject, and sending plain numbers to `opportunity_engine` produced zero qualifying games (all entries filtered out as DNPs).

---

## `box_score_log` — required dict shape

Each element must contain:

```json
{
  "MIN": 31.0,
  "PTS": 17.0,
  "REB": 5.0,
  "AST": 3.0,
  "FGA": 12.0,
  "USG%": 28.4
}
```

**Accepted USG key variants** (case-sensitive exact match — use exactly one):
- `USG`
- `USG%`
- `usg_pct`
- `usage_rate`
- `Usage%`

Entries where `MIN < 3` are treated as DNPs and excluded automatically.  
Minimum **3 qualifying (non-DNP) games** required; fewer triggers `WNBA_HOLD_ROLE_UNCERTAIN`.

---

## `game_log` — required format (unchanged)

Plain list of numbers — the per-game stat value being scored, e.g.:

```json
"game_log": [19, 14, 22, 11, 18, 16, 21, 13, 17, 20]
```

The L5/L10 ledger derives `l5_values`, `l10_values`, `l10_median`, `l10_mean`, and `l5_line_used` internally. **Do not send those fields** — they are computed, not accepted inputs.

---

## `role_timestamp` / `role_confirmation_age_minutes`

These are correct enrichment fields (no change). `role_timestamp` should be an ISO-8601 datetime string of when the role status was last confirmed. The engine computes `role_confirmation_age_minutes` from it.

---

## What happens if `box_score_log` is missing

The opportunity gate soft-holds the row with:

```
gate_label: WNBA_HOLD_ROLE_UNCERTAIN
hold_tag:   WNBA_HOLD_ROLE_UNCERTAIN:game_log_missing
caller_action: "Supply enrichment.box_score_log with ≥5 non-DNP games..."
```

This is a ceiling, not a hard reject — scoring continues but the row is capped at `MODEL_QUALIFIED_HOLD`.

---

## Summary for scoring payloads

```json
"enrichment": {
  "game_log": [19, 14, 22, 11, 18],
  "box_score_log": [
    {"MIN": 31, "PTS": 19, "REB": 5, "AST": 3, "FGA": 14, "USG%": 29},
    {"MIN": 28, "PTS": 14, "REB": 4, "AST": 2, "FGA": 10, "USG%": 24},
    ...
  ],
  "role_status": "STARTER",
  "role_timestamp": "2026-08-03T10:00:00Z"
}
```
