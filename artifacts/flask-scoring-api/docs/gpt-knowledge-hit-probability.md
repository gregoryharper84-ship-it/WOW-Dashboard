# GPT Knowledge: `hit_probability` Field Spec

## Overview

Every leg returned by `/analyze-and-score` includes three probability fields:

| Field | Type | Notes |
|-------|------|-------|
| `hit_probability` | `float \| null` | Calibrated win probability 0.0–1.0; `null` when data is insufficient |
| `model_used` | `string` | Which model computed the probability (see below) |
| `calibration_note` | `string` | One-sentence human-readable explanation of inputs and any caveats |

---

## `model_used` Values and MLB Dispatch Rules

### MLB props

| Stat | Line | `model_used` | Method |
|------|------|-------------|--------|
| H, HITS | ≤ 1.5 | `mlb_formula_v2` | Binomial `P(≥1 hit) = 1−(1−p_per_PA)^n_PA`. `p_per_PA` sourced from enrichment batting average or derived from game-log mean ÷ league-avg PA (3.65). |
| HR, RBI, SB, BB, R, TB, 1B, 2B, 3B | ≤ 1.5 | `bernoulli_hit_rate` | Game-log fraction where value clears the line threshold. Uses actual stat values so the line is honored correctly. |
| SO, K, TB, SO/IP | any (counting) | `poisson_l10` | Poisson CDF on game-log mean as λ. |
| Any MLB prop above the binary range | > 1.5 | `poisson_l10` or `claude_estimate` | Routes to counting or Claude depending on stat type. |

**Important**: HR/RBI/SB/BB/R props at ≤1.5 line use `bernoulli_hit_rate`, not `mlb_formula_v2`, because home-run and stolen-base event rates are not interchangeable with batting average.

### NBA / WNBA props

| Stat | `model_used` | Method |
|------|-------------|--------|
| PTS, REB, AST, PRA, STL, BLK, TOV, 3PM, FTM, FG3M, combo (+) | `poisson_l10` | Poisson CDF `P(X≥line) = 1−CDF(floor(line)−1, λ)` where `λ = game_log mean`. |

### All other sports (NFL, NHL, etc.)

| `model_used` | Trigger |
|-------------|---------|
| `claude_estimate` | No Python formula exists yet; Claude is prompted with game_log + market no_vig_prob + injury_status and must show its working. |
| `no_data` | No game log available; `hit_probability` is always `null`. |
| `error` | Exception during computation; `hit_probability` is `null`. |

---

## `calibration_note` Examples

```
"MLB formula v2: MORE (P(≥1 hit))=0.6842, BA=0.289, n_PA=3.90, dq=FULL"
"MLB formula v2: MORE (P(≥1 hit))=0.7124, BA=0.274, n_PA=3.65, dq=PARTIAL; L6 only — L10 unavailable; BA derived from game log (6g mean=0.83), not season avg"
"Bernoulli MORE: 7/10 games ≥ 0.5"
"Bernoulli MORE: 3/5 games ≥ 1.5"
"Poisson MORE: λ=24.30, P(X≥25)=0.4821, n=10"
"Poisson MORE: λ=18.20, P(X≥22)=0.1904, n=7; Poisson λ from 7 games, below 10-game ideal"
"No game log available — cannot compute probability"
"insufficient data"  ← UNRESOLVABLE legs
```

---

## UNRESOLVABLE Legs

When `terminal_label` is `UNRESOLVABLE` (player could not be identified after Claude gap-fill):
- `hit_probability`: `null`
- `model_used`: `"no_data"`
- `calibration_note`: `"insufficient data"`

The GPT must **not** attempt to estimate probability for UNRESOLVABLE legs.

---

## Sample-Size Warnings

When a Poisson model has fewer than 10 games in the log, `calibration_note` includes:
```
Poisson λ from N games, below 10-game ideal
```

When the MLB formula uses a game-log–derived batting average instead of a season BA:
```
BA derived from game log (Ng mean=X.XX), not season avg
```

---

## GPT Interpretation Rules

1. **Do not recompute or override `hit_probability`**. The backend is authoritative.
2. `hit_probability` and `model_used` together let you explain the basis to the user ("the Poisson model using the last 10 games gives a 62% chance of clearing").
3. When `hit_probability` is `null`, the probability is genuinely unknown — do not substitute `no_vig_prob` or a guess.
4. `calibration_note` is for display only; extract λ or n from it only if clearly labelled — never parse it for decision logic.
