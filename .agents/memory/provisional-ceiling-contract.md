---
name: PROVISIONAL ceiling + probability contract
description: analyze_and_score probability sub-object fields; PROVISIONAL ceiling enforcement; data_gaps structured list
---

## Rules

**PROVISIONAL ceiling on model_registry entries**
Every `_prov()` entry now includes:
```python
"provisional_ceiling": {
    "maximum_label":       "MODEL_QUALIFIED_HOLD",
    "power_eligibility":   False,
    "money_grade_allowed": False,
}
```
ACTIVE entries and NO_REGISTERED_MODEL entries do NOT have this key.

**probability sub-object contract**
```json
{
  "raw_probability":           0.74,
  "calibrated_probability":    null,       // non-null only for ACTIVE models
  "calibrated_lower_bound":    0.66,
  "upper_bound":               0.82,
  "push_probability":          0.0,
  "calibration_status":        "PROVISIONAL",   // CALIBRATED | PROVISIONAL | UNAVAILABLE
  "probability_publishable":   false,
  "high_probability_qualified": false
}
```
- `calibration_status` is CALIBRATED only for ACTIVE models with a non-null probability.
- Never set `calibrated_probability` non-null for PROVISIONAL (no real cohort calibration exists).
- `probability_publishable` = ACTIVE + non-null probability.
- `high_probability_qualified` = publishable + probability ≥ 0.65.

**backend sub-object additions**
```json
{
  "money_grade_allowed": false,          // false for PROVISIONAL models
  "model_ceiling": "MODEL_QUALIFIED_HOLD" // present for PROVISIONAL, null otherwise
}
```

**data_gaps structured list**
Each leg now includes a `data_gaps` list of structured acquisition requests:
```json
{
  "field":             "box_score_log",
  "required_for":      "WNBA opportunity gate",
  "preferred_sources": [...],
  "minimum_records":   5,
  "accepted_format":   "list[dict] — ...",
  "resubmission_key":  "enrichment.box_score_log"
}
```
Built from: `row.get("data_gaps")` + auto-detected missing game_log/sportsbook_line + unresolvable player flags. Deduplicated with `dict.fromkeys()`. `_GAP_CONTRACT` and `_build_gap_entry()` are module-level in app.py, above the `/analyze-and-score` route.

**Why:**
ChatGPT review: PROVISIONAL numeric results must not override ceilings. Calling a provisional output "calibrated" without a real cohort is misleading. Structured data_gaps gives the GPT precise instructions and prevents broad, uncontrolled research.
