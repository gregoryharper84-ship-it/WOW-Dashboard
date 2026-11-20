"""Research-only exact finite-slate NFL Pick'em pool-equity challenger.

This module never scores games, overrides a V17 probability, or publishes a card.
It enumerates a TWO-OUTCOME INDEPENDENCE scenario model. NFL tied games and
cross-game outcome dependence are NOT modeled; reported equity is a diagnostic
proxy, not certified weekly prize-winning probability.
Only verifiably pre-lock, available opponent cards can influence a challenger.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isclose, isfinite
from typing import Any, Mapping, Sequence

from nfl_event_model_contract import CONTROLLING_SPECIALIST

SERVING_MODE = "RESEARCH_SHADOW_ONLY"
OBJECTIVE = "MAX_WEEKLY_WIN_EQUITY_RESEARCH"
CAN_EXECUTE = False
PRODUCTION_PICK_MUTATION_ALLOWED = False
PRODUCTION_PROBABILITY_MUTATION_ALLOWED = False
AUTOMATIC_PROMOTION = False
MAX_GAMES = 16
SOURCE_TERMINALS = frozenset({
    "MODEL_QUALIFIED_HOLD", "MARKET_VERIFIED_HOLD",
    "MONEY_QUALIFIED", "FINAL_APPROVED",
})


class PoolEquityInputError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _required(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PoolEquityInputError(code)
    return value.strip()


def _time(value: Any, code: str) -> datetime:
    token = _required(value, code)
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PoolEquityInputError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PoolEquityInputError(code)
    return parsed.astimezone(timezone.utc)


def _prob(value: Any, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise PoolEquityInputError(code)
    result = float(value)
    if not isfinite(result) or not 0 < result < 1:
        raise PoolEquityInputError(code)
    return result


def _bound(value: Any, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise PoolEquityInputError(code)
    result = float(value)
    if not isfinite(result) or not 0 <= result <= 1:
        raise PoolEquityInputError(code)
    return result


def evaluate_full_pool_shadow(
    governed_picks: Sequence[Mapping[str, Any]],
    opponent_entries: Sequence[Mapping[str, Any]],
    *,
    lock_at: str,
    pool_size: int,
    max_flips: int = 3,
    max_expected_correct_sacrifice: float = 0.25,
) -> dict[str, Any]:
    """Compare model-max card to hill-climbed challenger, with frozen inputs.

    Assumes independent binary outcomes solely for research. Opponent cards must
    have been both submitted AND available for observation before the pool lock.
    Returns a fixed-card diagnostic; never submits or updates production picks.
    """
    lock = _time(lock_at, "PICKEM_EQUITY_LOCK_INVALID")
    if not isinstance(governed_picks, (list, tuple)) or not 1 <= len(governed_picks) <= MAX_GAMES:
        raise PoolEquityInputError("PICKEM_EQUITY_GAME_COUNT_INVALID")
    if not isinstance(opponent_entries, (list, tuple)) or not opponent_entries:
        raise PoolEquityInputError("PICKEM_EQUITY_PRELOCK_OPPONENTS_REQUIRED")
    if isinstance(pool_size, bool) or not isinstance(pool_size, int) or pool_size < 2:
        raise PoolEquityInputError("PICKEM_EQUITY_POOL_SIZE_INVALID")
    if len(opponent_entries) != pool_size - 1:
        raise PoolEquityInputError("PICKEM_EQUITY_POOL_SIZE_MISMATCH")
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
    home_probs: list[float] = []
    baseline_mask = 0
    prediction_ids: set[str] = set()
    for i, row in enumerate(governed_picks):
        if not isinstance(row, Mapping) or row.get("status") != "PICKEM_READY" or row.get("can_execute") is not False:
            raise PoolEquityInputError("PICKEM_EQUITY_SOURCE_NOT_READY")
        if row.get("source_terminal_label") not in SOURCE_TERMINALS:
            raise PoolEquityInputError("PICKEM_EQUITY_SOURCE_TERMINAL_BLOCKED")
        if row.get("controlling_specialist") != CONTROLLING_SPECIALIST:
            raise PoolEquityInputError("PICKEM_EQUITY_SPECIALIST_MISMATCH")
        event = _required(row.get("official_event_id"), "PICKEM_EQUITY_EVENT_ID_REQUIRED")
        if event in ids:
            raise PoolEquityInputError("PICKEM_EQUITY_DUPLICATE_EVENT")
        home = _required(row.get("home_team"), "PICKEM_EQUITY_HOME_REQUIRED")
        away = _required(row.get("away_team"), "PICKEM_EQUITY_AWAY_REQUIRED")
        selected = _required(row.get("pool_pick"), "PICKEM_EQUITY_BASELINE_SIDE_REQUIRED")
        if home == away or selected not in (home, away):
            raise PoolEquityInputError("PICKEM_EQUITY_SIDE_IDENTITY_INVALID")
        p_home = _prob(row.get("home_probability"), "PICKEM_EQUITY_PROBABILITY_INVALID")
        p_away = _prob(row.get("away_probability"), "PICKEM_EQUITY_PROBABILITY_INVALID")
        p_selected = _prob(row.get("selected_probability"), "PICKEM_EQUITY_PROBABILITY_INVALID")
        if (not isclose(p_home + p_away, 1.0, abs_tol=1e-9)
                or not isclose(p_selected, p_home if selected == home else p_away, abs_tol=1e-9)
                or p_selected + 1e-9 < max(p_home, p_away)):
            raise PoolEquityInputError("PICKEM_EQUITY_BASELINE_MODEL_CONFLICT")
        lower = _bound(row.get("calibrated_selection_lower_bound"), "PICKEM_EQUITY_BOUNDS_REQUIRED")
        upper = _bound(row.get("calibrated_selection_upper_bound"), "PICKEM_EQUITY_BOUNDS_REQUIRED")
        if not lower <= p_selected <= upper:
            raise PoolEquityInputError("PICKEM_EQUITY_BOUNDS_INVALID")
        pred = _required(row.get("source_prediction_id"), "PICKEM_EQUITY_PREDICTION_RECEIPT_REQUIRED")
        if pred in prediction_ids:
            raise PoolEquityInputError("PICKEM_EQUITY_DUPLICATE_PREDICTION")
        prediction_ids.add(pred)
        _required(row.get("source_snapshot_id"), "PICKEM_EQUITY_SOURCE_SNAPSHOT_REQUIRED")
        kickoff = _time(row.get("scheduled_start_utc"), "PICKEM_EQUITY_KICKOFF_REQUIRED")
        model_at = _time(row.get("immutable_model_timestamp"), "PICKEM_EQUITY_MODEL_TIMESTAMP_INVALID")
        if model_at > lock or model_at >= kickoff or lock >= kickoff:
            raise PoolEquityInputError("PICKEM_EQUITY_STALE_OR_POST_LOCK_MODEL")
        ids.append(event)
        homes.append(home)
        aways.append(away)
        home_probs.append(p_home)
        if selected == home:
            baseline_mask |= 1 << i

    opponents: list[int] = []
    opponent_ids: set[str] = set()
    opponent_receipts: set[str] = set()
    for entry in opponent_entries:
        if not isinstance(entry, Mapping):
            raise PoolEquityInputError("PICKEM_EQUITY_OPPONENT_INVALID")
        entry_id = _required(entry.get("entry_id"), "PICKEM_EQUITY_OPPONENT_ID_REQUIRED")
        if entry_id in opponent_ids:
            raise PoolEquityInputError("PICKEM_EQUITY_DUPLICATE_OPPONENT")
        opponent_ids.add(entry_id)
        receipt = _required(entry.get("receipt_id"), "PICKEM_EQUITY_OPPONENT_RECEIPT_REQUIRED")
        if receipt in opponent_receipts:
            raise PoolEquityInputError("PICKEM_EQUITY_DUPLICATE_OPPONENT_RECEIPT")
        opponent_receipts.add(receipt)
        if entry.get("audience") != "OPPONENT_ENTRIES":
            raise PoolEquityInputError("PICKEM_EQUITY_OPPONENT_AUDIENCE_INVALID")
        _required(entry.get("availability_source"), "PICKEM_EQUITY_OPPONENT_AVAILABILITY_SOURCE_REQUIRED")
        submitted = _time(entry.get("submitted_at"), "PICKEM_EQUITY_OPPONENT_SUBMISSION_INVALID")
        available = _time(entry.get("available_to_user_at"), "PICKEM_EQUITY_OPPONENT_AVAILABILITY_INVALID")
        if available < submitted:
            raise PoolEquityInputError("PICKEM_EQUITY_OPPONENT_AVAILABILITY_BEFORE_SUBMISSION")
        if submitted > lock or available > lock:
            raise PoolEquityInputError("PICKEM_EQUITY_OPPONENT_NOT_PRELOCK_AVAILABLE")
        picks = entry.get("picks")
        if not isinstance(picks, Mapping) or set(picks) != set(ids):
            raise PoolEquityInputError("PICKEM_EQUITY_OPPONENT_EVENT_SET_MISMATCH")
        mask = 0
        for i, event in enumerate(ids):
            side = picks[event]
            if side == homes[i]:
                mask |= 1 << i
            elif side != aways[i]:
                raise PoolEquityInputError("PICKEM_EQUITY_OPPONENT_SIDE_INVALID")
        opponents.append(mask)

    n = len(ids)
    full_mask = (1 << n) - 1
    # Exact enumeration, rather than Monte Carlo sampling, eliminates seed variance.
    scenarios: list[tuple[int, float, int, int]] = []
    for outcome in range(1 << n):
        weight = 1.0
        for i, p_home in enumerate(home_probs):
            weight *= p_home if outcome & (1 << i) else 1 - p_home
        scores = [n - ((mask ^ outcome) & full_mask).bit_count() for mask in opponents]
        best = max(scores)
        tied_best = sum(score == best for score in scores)
        scenarios.append((outcome, weight, best, tied_best))

    def expected_correct(mask: int) -> float:
        return sum(p if mask & (1 << i) else 1 - p for i, p in enumerate(home_probs))

    baseline_expected = expected_correct(baseline_mask)

    def grade(mask: int) -> dict[str, float]:
        sole = tied = split = 0.0
        for outcome, weight, best, n_best in scenarios:
            points = n - ((mask ^ outcome) & full_mask).bit_count()
            if points > best:
                sole += weight
                split += weight
            elif points == best:
                tied += weight
                split += weight / (1 + n_best)
        return {
            "sole_first_probability_proxy": sole,
            "tied_first_probability_proxy": tied,
            "first_or_tied_probability_proxy": sole + tied,
            "equal_split_first_share_proxy": split,
            "expected_correct": expected_correct(mask),
        }

    baseline = grade(baseline_mask)
    selected_mask = baseline_mask
    selected_grade = baseline
    while (baseline_mask ^ selected_mask).bit_count() < max_flips:
        winning_candidate = None
        for i in range(n):
            candidate = selected_mask ^ (1 << i)
            if (baseline_mask ^ candidate).bit_count() > max_flips:
                continue
            if baseline_expected - expected_correct(candidate) > max_expected_correct_sacrifice + 1e-12:
                continue
            score = grade(candidate)
            improvement = score["equal_split_first_share_proxy"] - selected_grade["equal_split_first_share_proxy"]
            if improvement <= 1e-12:
                continue
            key = (score["equal_split_first_share_proxy"], score["expected_correct"], -i)
            if winning_candidate is None or key > winning_candidate[0]:
                winning_candidate = (key, candidate, score)
        if winning_candidate is None:
            break
        _, selected_mask, selected_grade = winning_candidate

    changed = [
        {
            "official_event_id": event,
            "baseline_side": homes[i] if baseline_mask & (1 << i) else aways[i],
            "research_challenger_side": homes[i] if selected_mask & (1 << i) else aways[i],
        }
        for i, event in enumerate(ids)
        if (selected_mask ^ baseline_mask) & (1 << i)
    ]
    return {
        "status": "RESEARCH_SHADOW_COMPLETE",
        "serving_mode": SERVING_MODE,
        "objective": OBJECTIVE,
        "scenario_count": len(scenarios),
        "pool_size": pool_size,
        "event_count": n,
        "baseline": baseline,
        "research_challenger": selected_grade,
        "changed_picks": changed,
        "expected_correct_sacrifice": baseline_expected - selected_grade["expected_correct"],
        "full_pool_evaluation": True,
        "probability_assumption": "INDEPENDENT_BINARY_GAME_OUTCOMES",
        "nfl_ties_modeled": False,
        "game_dependence_modeled": False,
        "tie_breaker_model": "UNAVAILABLE_EQUAL_SPLIT_PROXY_ONLY",
        "certified_first_place_equity": False,
        "opponents_must_be_prelock_available": True,
        "production_pick_mutation_allowed": False,
        "production_probability_mutation_allowed": False,
        "automatic_promotion": False,
        "can_execute": False,
    }
