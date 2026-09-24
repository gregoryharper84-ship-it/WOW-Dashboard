"""Provider-neutral market features and value math for LLP team/event evidence.

This module is intentionally downstream of governed sporting probability.
It converts moneyline market quotes into market-only features and compares those
features with an already-completed calibrated probability package. It never
scores a sporting event, changes model probability, changes probability rank,
or enables execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import sqrt
from statistics import mean, median
from typing import Any, Iterable

CAN_EXECUTE = False
VALUE_POLICY = "VALUE_ONLY_DOES_NOT_CHANGE_PROBABILITY_RANK"
MARKET_TYPE = "MONEYLINE"
MOVEMENT_ROLE = "MARKET_EVIDENCE_ONLY_NOT_PROOF_OF_SHARP_MONEY_OR_NEWS"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def american_implied_probability(odds: Any) -> float | None:
    """Return raw breakeven probability for American odds.

    Negative odds use ``abs(odds) / (abs(odds) + 100)`` and positive odds use
    ``100 / (odds + 100)``. This is deliberately *not* a sporting probability.
    For a single executable price it is the raw breakeven threshold. No-vig
    market probability is computed separately from paired prices at the same
    bookmaker.
    """
    price = _number(odds)
    if price is None or price == 0:
        return None
    if price > 0:
        return 100.0 / (price + 100.0)
    return (-price) / ((-price) + 100.0)


def american_to_decimal(odds: Any) -> float | None:
    price = _number(odds)
    if price is None or price == 0:
        return None
    if price > 0:
        return 1.0 + price / 100.0
    return 1.0 + 100.0 / (-price)


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _quote_age_seconds(quote: dict[str, Any] | None, *, now: datetime) -> float | None:
    if not quote:
        return None
    stamp = _parse_timestamp(quote.get("updated_at"))
    if stamp is None:
        return None
    return max(0.0, (now - stamp).total_seconds())


def _priced_quotes(quotes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(q) for q in quotes if _number(q.get("american_odds")) is not None]


def _best_quote(quotes: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    usable = _priced_quotes(quotes)
    if not usable:
        return None
    # In American odds, the numerically largest price is always the most
    # favorable executable price for the bettor: -145 beats -175 and +148
    # beats +130.
    return dict(max(usable, key=lambda q: float(q["american_odds"])))


def _worst_quote(quotes: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    usable = _priced_quotes(quotes)
    if not usable:
        return None
    return dict(min(usable, key=lambda q: float(q["american_odds"])))


def _book_map(quotes: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for quote in quotes:
        book = str(quote.get("bookmaker") or "").strip()
        if not book or _number(quote.get("american_odds")) is None:
            continue
        incumbent = mapped.get(book)
        if incumbent is None:
            mapped[book] = dict(quote)
            continue
        current_ts = _parse_timestamp(quote.get("updated_at"))
        incumbent_ts = _parse_timestamp(incumbent.get("updated_at"))
        if incumbent_ts is None or (current_ts is not None and current_ts >= incumbent_ts):
            mapped[book] = dict(quote)
    return mapped


def _paired_no_vig(moneyline: dict[str, Any]) -> list[dict[str, Any]]:
    home = _book_map(moneyline.get("home_quotes") or [])
    away = _book_map(moneyline.get("away_quotes") or [])
    paired: list[dict[str, Any]] = []
    for bookmaker in sorted(set(home) & set(away)):
        home_raw = american_implied_probability(home[bookmaker].get("american_odds"))
        away_raw = american_implied_probability(away[bookmaker].get("american_odds"))
        if home_raw is None or away_raw is None:
            continue
        total = home_raw + away_raw
        if total <= 0:
            continue
        overround = total - 1.0
        paired.append(
            {
                "bookmaker": bookmaker,
                "home_raw_implied_probability": home_raw,
                "away_raw_implied_probability": away_raw,
                "market_overround": overround,
                "market_overround_pp": overround * 100.0,
                "home_no_vig_probability": home_raw / total,
                "away_no_vig_probability": away_raw / total,
                "home_american_odds": float(home[bookmaker]["american_odds"]),
                "away_american_odds": float(away[bookmaker]["american_odds"]),
                "home_updated_at": home[bookmaker].get("updated_at"),
                "away_updated_at": away[bookmaker].get("updated_at"),
            }
        )
    return paired


def _stddev(values: list[float]) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return 0.0
    avg = mean(values)
    return sqrt(sum((value - avg) ** 2 for value in values) / len(values))


def _line_shop_features(quotes: Iterable[dict[str, Any]]) -> dict[str, Any]:
    usable = _priced_quotes(quotes)
    best = _best_quote(usable)
    worst = _worst_quote(usable)
    if best is None or worst is None:
        return {
            "book_count": len(usable),
            "best_price": best,
            "worst_price": worst,
            "breakeven_improvement_pp": None,
            "decimal_return_improvement_per_unit_staked": None,
        }
    best_prob = american_implied_probability(best.get("american_odds"))
    worst_prob = american_implied_probability(worst.get("american_odds"))
    best_decimal = american_to_decimal(best.get("american_odds"))
    worst_decimal = american_to_decimal(worst.get("american_odds"))
    return {
        "book_count": len(usable),
        "best_price": best,
        "worst_price": worst,
        "breakeven_improvement_pp": (
            (worst_prob - best_prob) * 100.0
            if best_prob is not None and worst_prob is not None else None
        ),
        "decimal_return_improvement_per_unit_staked": (
            best_decimal - worst_decimal
            if best_decimal is not None and worst_decimal is not None else None
        ),
    }


def _snapshot_features(snapshot: dict[str, Any] | None, *, now: datetime) -> dict[str, Any]:
    snapshot = dict(snapshot or {})
    moneyline = dict(snapshot.get("moneyline") or {})
    pairs = _paired_no_vig(moneyline)
    home_probs = [float(row["home_no_vig_probability"]) for row in pairs]
    away_probs = [float(row["away_no_vig_probability"]) for row in pairs]
    overrounds = [float(row["market_overround_pp"]) for row in pairs]
    home_line_shop = _line_shop_features(moneyline.get("home_quotes") or [])
    away_line_shop = _line_shop_features(moneyline.get("away_quotes") or [])
    best_home = home_line_shop["best_price"]
    best_away = away_line_shop["best_price"]

    def side_stats(values: list[float]) -> dict[str, float | None]:
        if not values:
            return {"mean": None, "median": None, "range_pp": None, "stddev_pp": None}
        return {
            "mean": mean(values),
            "median": median(values),
            "range_pp": (max(values) - min(values)) * 100.0,
            "stddev_pp": (_stddev(values) or 0.0) * 100.0,
        }

    home_stats = side_stats(home_probs)
    away_stats = side_stats(away_probs)
    return {
        "status": "AVAILABLE" if pairs or best_home or best_away else "NO_MONEYLINE_QUOTES",
        "market_type": MARKET_TYPE,
        "market_selector_required": "MONEYLINE",
        "snapshot_kind": snapshot.get("snapshot_kind"),
        "timestamp": snapshot.get("timestamp"),
        "book_count": len(pairs),
        "paired_books": pairs,
        "average_market_overround_pp": mean(overrounds) if overrounds else None,
        "median_market_overround_pp": median(overrounds) if overrounds else None,
        "consensus_no_vig_probability": {
            "home": home_stats["mean"],
            "away": away_stats["mean"],
            "method": "MEAN_OF_SAME_BOOK_NO_VIG_PAIRS" if pairs else None,
        },
        "median_no_vig_probability": {"home": home_stats["median"], "away": away_stats["median"]},
        "book_probability_range_pp": {"home": home_stats["range_pp"], "away": away_stats["range_pp"]},
        "book_probability_stddev_pp": {"home": home_stats["stddev_pp"], "away": away_stats["stddev_pp"]},
        "best_price": {"home": best_home, "away": best_away},
        "best_executable_breakeven_probability": {
            "home": american_implied_probability(best_home.get("american_odds")) if best_home else None,
            "away": american_implied_probability(best_away.get("american_odds")) if best_away else None,
        },
        "best_price_age_seconds": {
            "home": _quote_age_seconds(best_home, now=now),
            "away": _quote_age_seconds(best_away, now=now),
        },
        "line_shopping": {
            "home": home_line_shop,
            "away": away_line_shop,
            "role": "EXECUTABLE_PRICE_OPTIMIZATION_ONLY",
        },
        "prediction_authority": False,
        "market_role_evidence_only": True,
        "can_execute": False,
    }


def build_moneyline_market_features(
    *,
    current: dict[str, Any] | None,
    opening: dict[str, Any] | None = None,
    closing: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build market-only features from typed opening/current/closing snapshots."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    current_features = _snapshot_features(current, now=now)
    opening_features = _snapshot_features(opening, now=now) if opening else None
    closing_features = _snapshot_features(closing, now=now) if closing else None

    def movement(reference: dict[str, Any] | None, target: dict[str, Any] | None) -> dict[str, float | None]:
        if not reference or not target:
            return {"home": None, "away": None}
        left = reference.get("consensus_no_vig_probability") or {}
        right = target.get("consensus_no_vig_probability") or {}
        out: dict[str, float | None] = {}
        for side in ("home", "away"):
            a = _number(left.get(side))
            b = _number(right.get(side))
            out[side] = (b - a) * 100.0 if a is not None and b is not None else None
        return out

    return {
        "status": current_features["status"],
        "market_type": MARKET_TYPE,
        "current": current_features,
        "opening": opening_features,
        "closing": closing_features,
        "movement_from_open_pp": movement(opening_features, current_features),
        "movement_current_to_close_pp": movement(current_features, closing_features),
        "movement_role": MOVEMENT_ROLE,
        "probability_mutated_by_evidence": False,
        "prediction_authority": False,
        "market_role_evidence_only": True,
        "rank_policy": VALUE_POLICY,
        "can_execute": False,
    }


def build_value_lane(scoring_result: dict[str, Any], market_features: dict[str, Any]) -> dict[str, Any]:
    """Compare an already-governed probability package with executable prices.

    The function is descriptive only. It does not modify ``scoring_result`` and
    the returned value metrics are never probability-rank inputs.
    """
    current = dict((market_features or {}).get("current") or {})
    consensus = dict(current.get("consensus_no_vig_probability") or {})
    breakeven = dict(current.get("best_executable_breakeven_probability") or {})
    best_price = dict(current.get("best_price") or {})
    line_shopping = dict(current.get("line_shopping") or {})
    config = {
        "home": ("calibrated_home_probability", "calibrated_home_lower_bound"),
        "away": ("calibrated_away_probability", "calibrated_away_lower_bound"),
    }
    sides: dict[str, Any] = {}
    complete = True
    for side, (p_key, lb_key) in config.items():
        probability = _number(scoring_result.get(p_key))
        lower_bound = _number(scoring_result.get(lb_key))
        price_probability = _number(breakeven.get(side))
        consensus_probability = _number(consensus.get(side))
        if probability is None or lower_bound is None:
            complete = False
        sides[side] = {
            "calibrated_probability": probability,
            "calibrated_lower_bound": lower_bound,
            "best_price": best_price.get(side),
            "line_shopping": line_shopping.get(side),
            "executable_raw_breakeven_probability": price_probability,
            "consensus_no_vig_probability": consensus_probability,
            "point_price_edge_pp": (
                (probability - price_probability) * 100.0
                if probability is not None and price_probability is not None else None
            ),
            "conservative_price_edge_pp": (
                (lower_bound - price_probability) * 100.0
                if lower_bound is not None and price_probability is not None else None
            ),
            "model_vs_consensus_pp": (
                (probability - consensus_probability) * 100.0
                if probability is not None and consensus_probability is not None else None
            ),
        }
    return {
        "status": "AVAILABLE" if complete else "MODEL_PROBABILITY_PACKAGE_INCOMPLETE",
        "market_type": MARKET_TYPE,
        "sides": sides,
        "probability_source": "GOVERNED_LLP_SPECIALIST_OUTPUT",
        "market_source": "RUNDOWN_MARKET_EVIDENCE",
        "value_basis": "GOVERNED_PROBABILITY_VS_BEST_EXECUTABLE_RAW_BREAKEVEN",
        "probability_rank_mutated": False,
        "rank_policy": VALUE_POLICY,
        "prediction_authority": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "MARKET_TYPE",
    "MOVEMENT_ROLE",
    "VALUE_POLICY",
    "american_implied_probability",
    "american_to_decimal",
    "build_moneyline_market_features",
    "build_value_lane",
]
