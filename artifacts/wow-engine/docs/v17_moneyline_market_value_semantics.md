# V17 moneyline market/value semantics

## Scope

This contract applies to sportsbook-board evidence for team/event moneyline evaluation across supported sports (for example MLB, ATP/WTA match winner, and other governed team/event winner lanes). It is market evidence only and never replaces the controlling fitted sporting specialist.

## Board interpretation

- Select the `Moneyline` market when evaluating outright team/player winners.
- Individual sportsbook columns are book-specific executable/reference prices.
- `BEST` is the most favorable currently observed price for the selected side across available books. It is a price candidate, not a sporting probability.
- `OPEN` is the opening-market baseline when available. It is used to measure movement into `CURRENT` and, when captured, `CLOSE`.

## American odds

- Negative odds `-X`: raw breakeven probability is `X / (X + 100)`.
- Positive odds `+X`: raw breakeven probability is `100 / (X + 100)`.
- These are price-derived breakeven thresholds, not fitted win probabilities.

For the same side, the numerically largest American price is the better price for the bettor. Thus `-145` is better than `-175`, and `+148` is better than `+130`.

## Vig / overround

For a two-sided moneyline at one bookmaker, convert both sides to raw implied probability. Their sum above 1.0 is the bookmaker overround. V17 stores same-book overround separately from no-vig consensus. Cross-book prices must not be paired to manufacture a no-vig probability.

## Line shopping

For each side, V17 records the best and worst observed executable prices and derives:

- breakeven-probability improvement from using BEST instead of the worst observed price;
- decimal-return improvement per unit staked.

Line shopping affects value/executable-price analysis only. It does not change sporting probability or probability rank.

## Value evaluation

Value analysis compares the already-completed governed probability package against the BEST executable raw breakeven threshold:

- `point_price_edge = calibrated_probability - best_executable_breakeven_probability`
- `conservative_price_edge = calibrated_lower_bound - best_executable_breakeven_probability`

Consensus no-vig probability, Pinnacle/reference-book prices, book dispersion, and movement are supporting market evidence. They do not become the sporting probability.

## Movement

When `OPEN`, `CURRENT`, and/or `CLOSE` snapshots exist, V17 measures movement in no-vig probability points. A large move is evidence that the market changed; it is not, by itself, proof of sharp money, injury news, lineup news, or a correct direction of travel.

## Governance invariants

- exactly one controlling fitted sport specialist owns each sporting probability row;
- sportsbook prices, BEST, consensus, Pinnacle, OPEN/CURRENT/CLOSE movement, and vig are evidence/value features only;
- probability ranking is not mutated by market evidence;
- `V17_TERMINAL_REDUCER` remains terminal authority;
- `can_execute=false` and dry-run-only governance remain binding.
