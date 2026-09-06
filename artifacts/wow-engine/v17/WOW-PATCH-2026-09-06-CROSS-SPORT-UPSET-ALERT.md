# WOW V17 — Cross-Sport Favorite Upset Alert

Status: `INSTALLED_PENDING_MERGE`

## Purpose

Surface a first-class alert when the market classifies a participant/team as the favorite but the governed sporting model says that favorite is vulnerable. The same signal supports both sides of the workflow:

1. warn against blindly treating a market favorite as a strong winner candidate; and
2. surface the strongest non-favorite outcome as an upset-discovery candidate.

## Separation of contracts

The market contributes **classification only**: which outcome is currently the favorite. The alert is then computed from the already-governed sporting probability package.

The alert must never:
- substitute market implied probability for governed sporting probability;
- change raw/calibrated/lower-bound sporting probability;
- create a pick, promotion, admission gate, NO_PICK threshold, cash gate, or terminal result;
- apply a universal sport-agnostic probability cutoff;
- execute, route, approve, modify, or cancel a wager.

`can_execute=false` remains absolute.

## Cross-sport alert semantics

For any supported team/event sport with:
- a verified current market-favorite classification; and
- a valid governed probability package for the complete mutually exclusive outcome space,

evaluate the favorite against the strongest modeled alternative.

### HIGH — `UPSET_ALERT_MODEL_FLIP`

Another governed outcome has a higher calibrated probability than the market favorite.

Interpretation: the market favorite is **not the model favorite**. The strongest alternative becomes the primary upset-discovery candidate.

### ELEVATED — `UPSET_ALERT_UNCERTAINTY_OVERLAP`

The market favorite remains the highest calibrated point estimate, but its calibrated lower bound does not clear the strongest alternative's calibrated upper bound.

Interpretation: the favorite is still the model leader, but the governed uncertainty interval does not separate it from the strongest alternative.

### NONE — `FAVORITE_MODEL_CLEAR`

The favorite is the highest calibrated point estimate and its calibrated lower bound clears every alternative's calibrated upper bound.

### UNAVAILABLE — `UPSET_ALERT_UNAVAILABLE`

The market favorite classification or required governed probability package is missing/unverified. Do not manufacture an alert from odds, narrative reasoning, recent hit rate, external projections, or generic research.

## Multi-outcome sports

Do not hard-code 50% as an alert threshold. For soccer and other multi-outcome markets, compare the market favorite against the complete mutually exclusive model outcome space, including draw/tie where applicable. A participant may be a valid market/model favorite with probability below 50%.

## Failure-path evidence

Modeled favorite failure paths, largest favorite loss path, and underdog upset paths may be attached as explanatory evidence. They must not be double-counted as an extra probability penalty after the fitted model has already incorporated them.

## Output contract

Winner/upset leaderboards should expose, when available:
- `upset_alert_status`
- `upset_alert_severity`
- `market_favorite`
- `market_favorite_model_probability`
- `market_favorite_lower_bound`
- `upset_candidate`
- `upset_candidate_model_probability`
- `upset_candidate_lower_bound`
- `probability_gap`
- `reason_codes`
- modeled favorite failure/upset path evidence if present

The flag is informational and discovery-oriented only. Ranking remains governed by the controlling lane's calibrated lower-bound contract.
