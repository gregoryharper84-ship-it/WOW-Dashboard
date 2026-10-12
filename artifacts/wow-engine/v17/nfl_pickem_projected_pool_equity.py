"""Research-only pool equity under documented, pre-lock ownership assumptions.

No actual opponent cards are inferred. Stochastic opponent scenarios are
illustrative; their ownership fractions require independent historical
validation. The NFL sporting probabilities are immutable V17 model inputs.
"""
from __future__ import annotations

from math import isclose, isfinite, sqrt
from random import Random
from typing import Any, Mapping, Sequence

from nfl_event_model_contract import CONTROLLING_SPECIALIST
from v17.nfl_pickem_full_pool_equity import (
    PoolEquityInputError, SOURCE_TERMINALS, MAX_GAMES, _prob, _bound, _required, _time,
)
from v17.nfl_pickem_ownership_forecast import validate_prelock_ownership_forecast

CAN_EXECUTE = False
SERVING_MODE = "OWNERSHIP_ASSUMPTION_RESEARCH_ONLY"
AUTOMATIC_PROMOTION = False
PRODUCTION_PICK_MUTATION_ALLOWED = False
PRODUCTION_PROBABILITY_MUTATION_ALLOWED = False


def evaluate_projected_pool_shadow(
    governed_picks: Sequence[Mapping[str, Any]],
    ownership_forecast: Mapping[str, Any],
    *,
    lock_at: str,
    pool_size: int,
    draws: int = 4096,
    seed: int = 17,
    max_flips: int = 3,
    max_expected_correct_sacrifice: float = 0.25,
) -> dict[str, Any]:
    """Compare immutable baseline with a conservative research challenger.

    Opponent cards are stochastic, independently drawn per event and participant
    from explicitly documented *assumed* ownership fractions. This is neither
    an ownership-fitted model nor a production/real-payout forecast.
    """
    lock = _time(lock_at, "PICKEM_EQUITY_LOCK_INVALID")
    if not isinstance(governed_picks, (list, tuple)) or not 1 <= len(governed_picks) <= MAX_GAMES:
        raise PoolEquityInputError("PICKEM_EQUITY_GAME_COUNT_INVALID")
    if isinstance(pool_size, bool) or not isinstance(pool_size, int) or not 2 <= pool_size <= 1000:
        raise PoolEquityInputError("PICKEM_PROJECTED_POOL_SIZE_INVALID")
    if isinstance(draws, bool) or not isinstance(draws, int) or not 1000 <= draws <= 20000:
        raise PoolEquityInputError("PICKEM_PROJECTED_DRAW_COUNT_INVALID")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32:
        raise PoolEquityInputError("PICKEM_PROJECTED_SEED_INVALID")
    # Cap random draws across all competitors/events to avoid runaway CPU cost.
    if draws * (pool_size - 1) * len(governed_picks) > 8_000_000:
        raise PoolEquityInputError("PICKEM_PROJECTED_RESOURCE_BUDGET_EXCEEDED")
    if isinstance(max_flips, bool) or not isinstance(max_flips, int) or not 0 <= max_flips <= MAX_GAMES:
        raise PoolEquityInputError("PICKEM_EQUITY_MAX_FLIPS_INVALID")
    if (isinstance(max_expected_correct_sacrifice, bool)
            or not isinstance(max_expected_correct_sacrifice, (float, int))
            or not isfinite(max_expected_correct_sacrifice)
            or max_expected_correct_sacrifice < 0):
        raise PoolEquityInputError("PICKEM_EQUITY_SACRIFICE_LIMIT_INVALID")

    ids: list[str] = []
    homes: list[str] = []
    aways: list[str] = []
    probs: list[float] = []
    baseline_mask = 0
    receipts: set[str] = set()
    for i, row in enumerate(governed_picks):
        if not isinstance(row, Mapping) or row.get("status") != "PICKEM_READY" or row.get("can_execute") is not False:
            raise PoolEquityInputError("PICKEM_EQUITY_SOURCE_NOT_READY")
        if row.get("source_terminal_label") not in SOURCE_TERMINALS:
            raise PoolEquityInputError("PICKEM_EQUITY_SOURCE_TERMINAL_BLOCKED")
        if row.get("controlling_specialist") != CONTROLLING_SPECIALIST:
            raise PoolEquityInputError("PICKEM_EQUITY_SPECIALIST_MISMATCH")
        eid = _required(row.get("official_event_id"), "PICKEM_EQUITY_EVENT_ID_REQUIRED")
        home = _required(row.get("home_team"), "PICKEM_EQUITY_HOME_REQUIRED")
        away = _required(row.get("away_team"), "PICKEM_EQUITY_AWAY_REQUIRED")
        side = _required(row.get("pool_pick"), "PICKEM_EQUITY_BASELINE_SIDE_REQUIRED")
        if eid in ids or home == away or side not in (home, away):
            raise PoolEquityInputError("PICKEM_PROJECTED_EVENT_IDENTITY_INVALID")
        hp = _prob(row.get("home_probability"), "PICKEM_EQUITY_PROBABILITY_INVALID")
        ap = _prob(row.get("away_probability"), "PICKEM_EQUITY_PROBABILITY_INVALID")
        sp = _prob(row.get("selected_probability"), "PICKEM_EQUITY_PROBABILITY_INVALID")
        if (not isclose(hp + ap, 1.0, abs_tol=1e-9)
                or not isclose(sp, hp if side == home else ap, abs_tol=1e-9)
                or sp + 1e-9 < max(hp, ap)):
            raise PoolEquityInputError("PICKEM_EQUITY_BASELINE_MODEL_CONFLICT")
        lb = _bound(row.get("calibrated_selection_lower_bound"), "PICKEM_EQUITY_BOUNDS_REQUIRED")
        ub = _bound(row.get("calibrated_selection_upper_bound"), "PICKEM_EQUITY_BOUNDS_REQUIRED")
        if not lb <= sp <= ub:
            raise PoolEquityInputError("PICKEM_EQUITY_BOUNDS_INVALID")
        pid = _required(row.get("source_prediction_id"), "PICKEM_EQUITY_PREDICTION_RECEIPT_REQUIRED")
        if pid in receipts:
            raise PoolEquityInputError("PICKEM_EQUITY_DUPLICATE_PREDICTION")
        receipts.add(pid)
        _required(row.get("source_snapshot_id"), "PICKEM_EQUITY_SOURCE_SNAPSHOT_REQUIRED")
        kickoff = _time(row.get("scheduled_start_utc"), "PICKEM_EQUITY_KICKOFF_REQUIRED")
        model_at = _time(row.get("immutable_model_timestamp"), "PICKEM_EQUITY_MODEL_TIMESTAMP_INVALID")
        if model_at > lock or model_at >= kickoff or lock >= kickoff:
            raise PoolEquityInputError("PICKEM_EQUITY_STALE_OR_POST_LOCK_MODEL")
        ids.append(eid)
        homes.append(home)
        aways.append(away)
        probs.append(hp)
        if side == home:
            baseline_mask |= 1 << i

    validated = validate_prelock_ownership_forecast(
        ownership_forecast,
        lock_at=lock_at,
        events=[
            {"official_event_id": ids[i], "home_team": homes[i], "away_team": aways[i]}
            for i in range(len(ids))
        ],
    )
    ownership = [validated["home_ownership"][eid] for eid in ids]
    n = len(ids)
    mask_all = (1 << n) - 1
    rng = Random(seed)
    # Use the SAME random scenarios for every candidate (common random numbers).
    # No synthetic opponent card is ever represented as a verified actual entry.
    scenarios: list[tuple[int, int, int]] = []
    for _ in range(draws):
        outcome = 0
        for i, probability in enumerate(probs):
            if rng.random() < probability:
                outcome |= 1 << i
        opponents = []
        for _entry in range(pool_size - 1):
            opponent = 0
            for i, share in enumerate(ownership):
                if rng.random() < share:
                    opponent |= 1 << i
            opponents.append(n - ((opponent ^ outcome) & mask_all).bit_count())
        high = max(opponents)
        scenarios.append((outcome, high, sum(v == high for v in opponents)))

    def expected(mask: int) -> float:
        return sum(p if mask & (1 << i) else 1 - p for i, p in enumerate(probs))

    def grade(mask: int) -> tuple[dict[str, float], list[float]]:
        sole = tied = 0
        shares = []
        for outcome, best, tied_best in scenarios:
            own = n - ((mask ^ outcome) & mask_all).bit_count()
            if own > best:
                sole += 1
                shares.append(1.0)
            elif own == best:
                tied += 1
                shares.append(1.0 / (1 + tied_best))
            else:
                shares.append(0.0)
        return ({
            "sole_first_probability_proxy": sole / draws,
            "tied_first_probability_proxy": tied / draws,
            "first_or_tied_probability_proxy": (sole + tied) / draws,
            "equal_split_first_share_proxy": sum(shares) / draws,
            "expected_correct": expected(mask),
        }, shares)

    baseline, _ = grade(baseline_mask)
    chosen_mask = baseline_mask
    chosen, chosen_shares = grade(chosen_mask)
    for _ in range(max_flips):
        best_choice = None
        for i in range(n):
            candidate = chosen_mask ^ (1 << i)
            if (candidate ^ baseline_mask).bit_count() > max_flips:
                continue
            sacrifice = baseline["expected_correct"] - expected(candidate)
            if sacrifice > max_expected_correct_sacrifice + 1e-12:
                continue
            score, shares = grade(candidate)
            differences = [a - b for a, b in zip(shares, chosen_shares)]
            delta = sum(differences) / draws
            # Paired Monte Carlo uncertainty: a mere noisy improvement cannot
            # justify a strategic change, even in research.
            variance = max(0.0, sum(d * d for d in differences) / draws - delta * delta)
            standard_error = sqrt(variance / draws)
            if delta <= max(2.5 * standard_error, 1e-10):
                continue
            ranking = (delta - 2.5 * standard_error, score["expected_correct"], -i)
            if best_choice is None or ranking > best_choice[0]:
                best_choice = (ranking, candidate, score, shares)
        if best_choice is None:
            break
        _, chosen_mask, chosen, chosen_shares = best_choice

    return {
        "status": "PROJECTED_POOL_EQUITY_RESEARCH_COMPLETE",
        "serving_mode": SERVING_MODE,
        "event_count": n,
        "pool_size": pool_size,
        "draws": draws,
        "seed": seed,
        "ownership_snapshot_id": validated["snapshot_id"],
        "ownership_semantics": validated["ownership_semantics"],
        "ownership_is_assumption": True,
        "simulated_opponent_cards_are_actual": False,
        "baseline": baseline,
        "research_challenger": chosen,
        "changed_picks": [
            {
                "official_event_id": eid,
                "baseline_side": homes[i] if baseline_mask & (1 << i) else aways[i],
                "research_challenger_side": homes[i] if chosen_mask & (1 << i) else aways[i],
            }
            for i, eid in enumerate(ids) if (chosen_mask ^ baseline_mask) & (1 << i)
        ],
        "expected_correct_sacrifice": baseline["expected_correct"] - chosen["expected_correct"],
        "probability_assumptions": ["INDEPENDENT_BINARY_GAME_RESULTS", "INDEPENDENT_OPPONENT_SELECTIONS"],
        "nfl_ties_modeled": False,
        "tiebreaker_model": "EQUAL_SPLIT_PROXY_ONLY",
        "certified_first_place_equity": False,
        "automatic_promotion": False,
        "production_pick_mutation_allowed": False,
        "production_probability_mutation_allowed": False,
        "can_execute": False,
    }
