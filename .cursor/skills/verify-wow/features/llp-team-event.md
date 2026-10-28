# LLP team/event probabilities

## Sub-features

Official pregame event identity, exact outright settlement, one controlling fitted sport/event specialist, raw and calibrated numeric probability, calibrated lower bound, final refresh, and terminal receipt.

## How to get to it (user POV)

User asks WOW for winner probabilities, favorites or underdog upsets. This routes to LLP_TEAM_BETTING_ENGINE rather than the prop lane.

## Driving it with existing LLP model and receipt harness

Use an actual eligible pregame event with verified identity and settlement. From the existing authorized runtime interface, obtain its controlling fitted-specialist result and link to the immutable event/prediction receipt. Check exact model artifact, timestamp, fresh inputs, numeric calibration and lower bound, and V17 terminal decision. Adjacent source regression in the configured isolated test environment:

~~~bash
python3 -m pytest -q artifacts/wow-engine/v17/test_game_winner_cash_single_gate.py
~~~

This regression does not itself prove production scoring.

## Gotchas

Market odds, projections or Scout findings cannot become model probability. Distinguish missing model, missing candidate inputs, scorer error, invalid output and market-only unavailable prices. Never force a ranking or downgrade a completed sporting probability because downstream market price is missing. Without a real fitted package, outcome is UNVERIFIED. can_execute=false.
