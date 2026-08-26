---
name: role_timestamp null-safe staleness format
description: role_timestamp.py crashes when status_age is None and the grade is STALE/RECHECK — latent bug fixed; WNBA test fixture pattern to avoid wall-clock sensitivity.
---

## The Rule

`role_timestamp.py` STALE and RECHECK blocker f-strings must never format `role_age` or `status_age` directly with `:.0f` — either can be `None` when the corresponding timestamp field is absent from enrichment.

**How to apply:** Use a guard before each format:
```python
_ra_str = f"{role_age:.0f}" if role_age is not None else "unknown"
_sa_str = f"{status_age:.0f}" if status_age is not None else "unknown"
```

**Why:** `status_age` is `None` whenever no `status_timestamp` is supplied in enrichment (common in tests). The worst_role_status can still be STALE if `role_grade` alone is STALE. Entering the STALE branch with `None` status_age crashes with `TypeError: unsupported format string passed to NoneType.__format__`.

## WNBA Test Fixture Pattern

WNBA pipeline tests that go through `role_timestamp.py` must supply:

```python
"role_confirmation_age_minutes": 5,  # forces FRESH regardless of wall clock
```

Without this override, a hardcoded `role_timestamp` string will go stale as time passes and cause unpredictable STALE/RECHECK branches (and blocker injection that breaks ceiling assertions).

Also always supply all three required role fields so PACKET_INCOMPLETE_REJECTED doesn't fire:

```python
"role_status": {
    "active_status":     "ACTIVE",
    "role_timestamp":    "<some iso string>",
    "projected_minutes": 34.0,
},
"event_status": "SCHEDULED",
"box_score_log": [
    {"date": "...", "PTS": ..., "REB": ..., "AST": ..., "MIN": ..., "FGA": ...},
    ...  # at least 5 rows
],
```
