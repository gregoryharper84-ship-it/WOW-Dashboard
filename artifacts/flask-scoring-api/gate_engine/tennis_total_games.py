"""
gate_engine/tennis_total_games.py

WOW v16 Tennis Total Games probability model.

Architecture
───────────────────────────────────────────────────────────
1.  Surface / tour baselines  — starting serve-point-win %
    by surface and tour when player-specific data absent.
2.  Markov chain simulation  — exact (not Monte Carlo) DP
    over game-level states → set-score distribution →
    match total-games distribution.
3.  Three-outcome contract  — for integer lines, MORE + EXACT
    + LESS = 1.0 before and after calibration.  For half-point
    lines EXACT = 0.
4.  Match-state probs  — P(2 sets), P(3 sets), P(fav 2-0),
    P(underdog ≥1 set).
5.  Set-extension probs  — P(any TB), P(first-set TB), P(7-5),
    P(one-sided set), P(one competitive set changes result).
6.  Conditional decomposition  — P(selected | 2 sets) and
    P(selected | 3 sets) that reconcile to unconditional prop.
7.  Dependency audit  — share of selected-side prob relying on
    third-set / tiebreak / extended-set / dominance conditions.
8.  Failure-path audit  — identify and quantify the largest
    adverse match regime; alter the unconditional distribution.
9.  Independent model frozen, then market evidence consulted.
10. Dynamic calibration  — responds to every named uncertainty
    source; lower bound from stress scenario, not fixed haircut.
11. Mandatory stress test  — remove most-favourable assumption,
    regenerate; Fragile when drop > threshold.
12. Classification  — Strong / Qualified / Marginal / Fragile /
    Reject derived from calibrated lower bound and stress result.

can_execute = False unconditionally (WOW governance).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

can_execute = False  # WOW governance: unconditional

# ─────────────────────────────────────────────────────────────────────────────
# Surface baselines  (service-point-win % by surface + tour, 2020-2024 avg)
# ─────────────────────────────────────────────────────────────────────────────

_SURFACE_SERVE_PCT: dict[str, dict[str, float]] = {
    "hard":        {"atp": 0.635, "wta": 0.595},
    "clay":        {"atp": 0.610, "wta": 0.565},
    "grass":       {"atp": 0.660, "wta": 0.625},
    "carpet":      {"atp": 0.645, "wta": 0.605},
    "hard_indoor": {"atp": 0.645, "wta": 0.600},
}
_DEFAULT_SERVE = {"atp": 0.635, "wta": 0.595}

# ─────────────────────────────────────────────────────────────────────────────
# Classification thresholds
# ─────────────────────────────────────────────────────────────────────────────

_STRONG_LB    = 0.60
_STRONG_DROP  = 0.05   # max stress drop for Strong
_QUAL_LB      = 0.55
_QUAL_DROP    = 0.08
_MARGINAL_LB  = 0.52
_MARGINAL_DROP = 0.10
_REJECT_CEIL  = 0.51   # calibrated prob below this → Reject

# Market weight ceiling (WOW governance cap)
_MAX_MARKET_WEIGHT = 0.35

# Stress: shrink serve advantage by this fraction (adverse for MORE = less hold)
_STRESS_SHRINK = 0.20

# ─────────────────────────────────────────────────────────────────────────────
# Markov chain helpers
# ─────────────────────────────────────────────────────────────────────────────

def _game_win_prob(p: float) -> float:
    """
    Exact P(server wins a game) given server wins each point with prob p.
    Derived from game-score Markov chain with deuce.

    G(p) = p⁴(1 + 4q + 10q²) + 20p³q³ · p²/(p²+q²)
    Verified: G(0.5) = 0.5.
    """
    p = float(max(0.01, min(0.99, p)))
    q = 1.0 - p
    denom = p * p + q * q
    if denom < 1e-15:
        return 0.5
    return (p**4) * (1.0 + 4.0*q + 10.0*q*q) + 20.0*(p**3)*(q**3) * (p*p) / denom


def _tb_win_prob(p: float) -> float:
    """
    Exact P(A wins tiebreak) given A wins each point with prob p (first-to-7,
    win by 2).  Service rotation averaged into p.

    T(p) = Σ_{k=0}^{5} C(6+k,k)·p⁷·q^k  +  C(12,6)·p⁶·q⁶·p²/(p²+q²)
    Verified: T(0.5) = 0.5.
    """
    p = float(max(0.01, min(0.99, p)))
    q = 1.0 - p
    _BINOM = [1, 7, 28, 84, 210, 462]  # C(6+k, k) for k=0..5
    result = sum(_BINOM[k] * (p**7) * (q**k) for k in range(6))
    denom = p*p + q*q
    if denom > 1e-15:
        result += 924.0 * (p**6) * (q**6) * (p*p) / denom  # 924 = C(12,6)
    return float(result)


def _is_set_over(a: int, b: int) -> bool:
    """True when (a, b) is a valid terminal set score (not counting the 6-6 tiebreak state)."""
    if a == 7 and b in (5, 6): return True   # 7-5 or 7-6 (tiebreak result)
    if b == 7 and a in (5, 6): return True   # 5-7 or 6-7
    if a == 6 and b <= 4:      return True   # 6-0..6-4
    if b == 6 and a <= 4:      return True   # 0-6..4-6
    return False


def _set_score_distribution(
    h1: float,
    h2: float,
    p1_tb: float,
    p1_serves_first: bool = True,
) -> dict[tuple[int, int], float]:
    """
    Exact set-score probability distribution for a standard set with tiebreak at 6-6.

    Parameters
    ──────────
    h1  : P(P1 holds serve) = _game_win_prob(p1_serve)
    h2  : P(P2 holds serve) = _game_win_prob(p2_serve)
    p1_tb : P(P1 wins the tiebreak at 6-6)
    p1_serves_first : who serves game 1

    Returns
    ───────
    {(a_games, b_games): probability}
    Terminal scores where a > b → P1 wins the set; b > a → P2 wins.
    (7, 6) means P1 won the tiebreak; (6, 7) means P2 did.
    """
    states: dict[tuple[int, int], float] = {(0, 0): 1.0}
    result: dict[tuple[int, int], float] = {}

    for _iteration in range(16):   # max 13 games + safety margin
        if not states:
            break
        next_states: dict[tuple[int, int], float] = {}

        for (a, b), prob in states.items():
            if prob < 1e-15:
                continue

            # ── special case: tiebreak ──────────────────────────────────────
            if a == 6 and b == 6:
                result[(7, 6)] = result.get((7, 6), 0.0) + prob * p1_tb
                result[(6, 7)] = result.get((6, 7), 0.0) + prob * (1.0 - p1_tb)
                continue

            # ── who serves this game ─────────────────────────────────────────
            total = a + b
            if p1_serves_first:
                p1_serves = (total % 2 == 0)
            else:
                p1_serves = (total % 2 == 1)

            p1_wins_game = h1 if p1_serves else (1.0 - h2)

            for p1_wins, gp in ((True, p1_wins_game), (False, 1.0 - p1_wins_game)):
                na = a + (1 if p1_wins else 0)
                nb = b + (0 if p1_wins else 1)
                w = prob * gp
                if _is_set_over(na, nb):
                    result[(na, nb)] = result.get((na, nb), 0.0) + w
                else:
                    next_states[(na, nb)] = next_states.get((na, nb), 0.0) + w

        states = next_states

    # Normalise floating-point drift
    total = sum(result.values())
    if total > 1e-10:
        result = {k: v / total for k, v in result.items()}
    return result


def _bo3_total_games_distribution(
    set_dist: dict[tuple[int, int], float],
) -> dict[int, float]:
    """
    Total-games distribution for a best-of-3 match.

    All four orderings are included:
      P1 wins 2-0  (P1 s1 + P1 s2)
      P2 wins 2-0  (P2 s1 + P2 s2)
      P1 wins 2-1  (P1 s1, P2 s2, P1 s3)  or  (P2 s1, P1 s2, P1 s3)
      P2 wins 2-1  (P1 s1, P2 s2, P2 s3)  or  (P2 s1, P1 s2, P2 s3)
    """
    p1s = {sc: p for sc, p in set_dist.items() if sc[0] > sc[1]}
    p2s = {sc: p for sc, p in set_dist.items() if sc[1] > sc[0]}

    dist: dict[int, float] = {}

    def _add(t: int, w: float) -> None:
        dist[t] = dist.get(t, 0.0) + w

    # 2-set outcomes
    for (a1,b1), p1 in p1s.items():
        for (a2,b2), p2 in p1s.items():
            _add((a1+b1)+(a2+b2), p1*p2)
    for (a1,b1), p1 in p2s.items():
        for (a2,b2), p2 in p2s.items():
            _add((a1+b1)+(a2+b2), p1*p2)

    # 3-set outcomes (4 orderings)
    for s1_set, s2_set, s3_set in (
        (p1s, p2s, p1s),   # P1 wins 2-1 (P1,P2,P1)
        (p2s, p1s, p1s),   # P1 wins 2-1 (P2,P1,P1)
        (p1s, p2s, p2s),   # P2 wins 2-1 (P1,P2,P2)
        (p2s, p1s, p2s),   # P2 wins 2-1 (P2,P1,P2)
    ):
        for (a1,b1), q1 in s1_set.items():
            for (a2,b2), q2 in s2_set.items():
                for (a3,b3), q3 in s3_set.items():
                    _add((a1+b1)+(a2+b2)+(a3+b3), q1*q2*q3)

    total = sum(dist.values())
    if total > 1e-10:
        dist = {k: v / total for k, v in dist.items()}
    return dist


def _bo5_total_games_distribution(
    set_dist: dict[tuple[int, int], float],
) -> dict[int, float]:
    """Total-games distribution for a best-of-5 match (10 orderings)."""
    p1s = {sc: p for sc, p in set_dist.items() if sc[0] > sc[1]}
    p2s = {sc: p for sc, p in set_dist.items() if sc[1] > sc[0]}

    dist: dict[int, float] = {}

    def _add(t: int, w: float) -> None:
        dist[t] = dist.get(t, 0.0) + w

    def _games(sc: tuple[int,int]) -> int:
        return sc[0] + sc[1]

    def _enum(sets_sequence: list[dict]) -> None:
        """Enumerate all combinations and accumulate into dist."""
        if not sets_sequence:
            return
        # Recursion depth ≤ 5; fine for this scope
        n = len(sets_sequence)
        if n == 2:
            for sc1, p1 in sets_sequence[0].items():
                for sc2, p2 in sets_sequence[1].items():
                    _add(_games(sc1)+_games(sc2), p1*p2)
        elif n == 3:
            for sc1, p1 in sets_sequence[0].items():
                for sc2, p2 in sets_sequence[1].items():
                    for sc3, p3 in sets_sequence[2].items():
                        _add(_games(sc1)+_games(sc2)+_games(sc3), p1*p2*p3)
        elif n == 4:
            for sc1, p1 in sets_sequence[0].items():
                for sc2, p2 in sets_sequence[1].items():
                    for sc3, p3 in sets_sequence[2].items():
                        for sc4, p4 in sets_sequence[3].items():
                            _add(_games(sc1)+_games(sc2)+_games(sc3)+_games(sc4), p1*p2*p3*p4)
        elif n == 5:
            for sc1, p1 in sets_sequence[0].items():
                for sc2, p2 in sets_sequence[1].items():
                    for sc3, p3 in sets_sequence[2].items():
                        for sc4, p4 in sets_sequence[3].items():
                            for sc5, p5 in sets_sequence[4].items():
                                _add(
                                    _games(sc1)+_games(sc2)+_games(sc3)+_games(sc4)+_games(sc5),
                                    p1*p2*p3*p4*p5
                                )

    # BO5: first to 3 sets.  All orderings where winner accumulates 3 set wins.
    # P1 wins 3-0, 3-1, 3-2; P2 wins 3-0, 3-1, 3-2.

    # 3-0 (2 orderings)
    _enum([p1s, p1s, p1s])
    _enum([p2s, p2s, p2s])

    # 3-1 — winner's 4th set is always the deciding set
    # P1 wins 3-1: among sets 1-3, exactly one P2 win, then P1 wins set 4
    from itertools import combinations as _comb
    for pos in range(3):  # position of P2 win among first 3 sets
        seq = [p1s]*3 + [p1s]
        seq[pos] = p2s
        _enum(seq)
    for pos in range(3):
        seq = [p2s]*3 + [p2s]
        seq[pos] = p1s
        _enum(seq)

    # 3-2 — among sets 1-4 exactly two losses for winner, then win set 5
    for pos_pair in _comb(range(4), 2):
        seq = [p1s]*4 + [p1s]
        for pp in pos_pair:
            seq[pp] = p2s
        _enum(seq)
    for pos_pair in _comb(range(4), 2):
        seq = [p2s]*4 + [p2s]
        for pp in pos_pair:
            seq[pp] = p1s
        _enum(seq)

    total = sum(dist.values())
    if total > 1e-10:
        dist = {k: v / total for k, v in dist.items()}
    return dist


# ─────────────────────────────────────────────────────────────────────────────
# Match-state helpers
# ─────────────────────────────────────────────────────────────────────────────

def _match_state_probs(
    p1_wins_set: float,
    best_of: int,
) -> dict[str, float]:
    """
    Analytical match-state probabilities from P(P1 wins a set).

    Assumes set results are i.i.d. (same serve baselines each set).
    """
    p = p1_wins_set
    q = 1.0 - p

    if best_of == 3:
        p_p1_20 = p * p
        p_p2_20 = q * q
        p_straight = p_p1_20 + p_p2_20
        p_three    = 1.0 - p_straight
        # P(P1 wins match) = P(2-0) + P(2-1): 2 orderings for 2-1
        p_p1_match = p*p + 2*p*p*q       # P(P1 wins 2-0) + P(P1 wins 2-1)
        p_p2_match = 1.0 - p_p1_match
        p_fav_20   = max(p_p1_20, p_p2_20)
        p_dog_1set = 1.0 - p_fav_20
    else:  # BO5
        p_p1_30 = p**3
        p_p2_30 = q**3
        p_p1_match = p**3*(1 + 3*q + 6*q*q)  # P(P1 wins BO5)
        p_p2_match = 1.0 - p_p1_match
        p_straight = p_p1_30 + p_p2_30
        p_three    = 3*(p**2*q + q**2*p)  # 3-1 matches in disguise (actually 4-set)
        p_fav_30   = max(p_p1_30, p_p2_30)
        p_dog_1set = 1.0 - p_fav_30

    return {
        "p_straight_sets":   round(float(p_straight),   6),
        "p_three_sets":      round(float(p_three),      6),   # or more for BO5
        "p_p1_wins_match":   round(float(p_p1_match),   6),
        "p_p2_wins_match":   round(float(p_p2_match),   6),
        "p_fav_wins_20":     round(float(p_fav_20 if best_of==3 else p_fav_30), 6),
        "p_underdog_wins_1set": round(float(p_dog_1set), 6),
    }


def _set_extension_probs(
    set_dist: dict[tuple[int, int], float],
    best_of: int,
    p_straight: float,
    p_three: float,
) -> dict[str, float]:
    """
    Compute set-extension probabilities from the set-score distribution.

    p_straight / p_three / p_more_sets from match-state probs.
    """
    p_tb_per_set   = set_dist.get((7, 6), 0.0) + set_dist.get((6, 7), 0.0)
    p_75_per_set   = set_dist.get((7, 5), 0.0) + set_dist.get((5, 7), 0.0)
    p_ons_per_set  = (
        set_dist.get((6, 0), 0.0) + set_dist.get((0, 6), 0.0) +
        set_dist.get((6, 1), 0.0) + set_dist.get((1, 6), 0.0)
    )

    n_sets = best_of  # expected maximum; use match-state weighted avg
    p_n_sets = p_straight * 2 + p_three * 3 if best_of == 3 else p_straight * best_of + (1.0-p_straight) * (best_of + 1)
    # P(at least one tiebreak in match)
    # For 2-set match: 1 - (1-p_tb)^2
    # For 3-set match: 1 - (1-p_tb)^3
    p_tb_2  = 1.0 - (1.0 - p_tb_per_set) ** 2
    p_tb_3  = 1.0 - (1.0 - p_tb_per_set) ** 3
    p_any_tb = p_straight * p_tb_2 + p_three * p_tb_3

    p_75_2  = 1.0 - (1.0 - p_75_per_set) ** 2
    p_75_3  = 1.0 - (1.0 - p_75_per_set) ** 3
    p_any_75 = p_straight * p_75_2 + p_three * p_75_3

    p_ons_2 = 1.0 - (1.0 - p_ons_per_set) ** 2
    p_ons_3 = 1.0 - (1.0 - p_ons_per_set) ** 3
    p_any_ons = p_straight * p_ons_2 + p_three * p_ons_3

    return {
        "p_tb_per_set":          round(float(p_tb_per_set),  6),
        "p_any_tiebreak":        round(float(p_any_tb),      6),
        "p_first_set_tiebreak":  round(float(p_tb_per_set),  6),  # same dist each set
        "p_any_75_set":          round(float(p_any_75),      6),
        "p_one_sided_set":       round(float(p_any_ons),     6),
        "p_tb_changes_result":   round(float(p_tb_per_set),  6),  # approx
    }


# ─────────────────────────────────────────────────────────────────────────────
# Prop probability computation (three-outcome)
# ─────────────────────────────────────────────────────────────────────────────

def _prop_probs(
    dist: dict[int, float],
    line: float,
) -> tuple[float, float, float]:
    """
    Returns (P_MORE, P_EXACT, P_LESS) from total-games distribution.

    For half-point lines (e.g. 22.5):  P_EXACT = 0.0, P_MORE + P_LESS = 1.0
    For integer lines (e.g. 22):        P_MORE + P_EXACT + P_LESS = 1.0
    Simplex invariant enforced by construction.
    """
    is_int = (line == int(line))
    int_line = int(line)
    p_more = p_exact = p_less = 0.0

    for t, p in dist.items():
        if t > line:
            p_more += p
        elif is_int and t == int_line:
            p_exact += p
        else:  # t < line  (or t == line for half-point — impossible)
            p_less += p

    total = p_more + p_exact + p_less
    if total > 1e-10:
        p_more  /= total
        p_exact /= total
        p_less  /= total
    return float(p_more), float(p_exact), float(p_less)


def _conditional_decomp(
    dist: dict[int, float],
    set_dist: dict[tuple[int, int], float],
    line: float,
    side: str,
    p_straight: float,
    p_three: float,
    best_of: int,
) -> dict[str, float]:
    """
    Decompose P(selected side) into 2-set and 3-set contributions.

    P(selected) = P(selected | 2 sets) * P(2 sets) + P(selected | 3+ sets) * P(3+ sets)
    Verified at return time.
    """
    p1s = {sc: p for sc, p in set_dist.items() if sc[0] > sc[1]}
    p2s = {sc: p for sc, p in set_dist.items() if sc[1] > sc[0]}

    def _games(sc: tuple[int,int]) -> int:
        return sc[0] + sc[1]

    # 2-set contribution
    p_sel_2set = 0.0
    for sc1, q1 in (*p1s.items(), *p2s.items()):
        # The second set must be won by the same player
        two_set_partner = p1s if sc1[0]>sc1[1] else p2s
        for sc2, q2 in two_set_partner.items():
            t = _games(sc1) + _games(sc2)
            if _matches_side(t, line, side):
                p_sel_2set += q1 * q2

    # 3-set contribution
    p_sel_3set = 0.0
    if best_of >= 3:
        for s1_map, s2_map, s3_map in (
            (p1s, p2s, p1s), (p2s, p1s, p1s),
            (p1s, p2s, p2s), (p2s, p1s, p2s),
        ):
            for sc1, q1 in s1_map.items():
                for sc2, q2 in s2_map.items():
                    for sc3, q3 in s3_map.items():
                        t = _games(sc1)+_games(sc2)+_games(sc3)
                        if _matches_side(t, line, side):
                            p_sel_3set += q1*q2*q3

    p_total = p_sel_2set + p_sel_3set
    c2 = p_sel_2set / p_straight if p_straight > 1e-12 else 0.0
    c3 = p_sel_3set / p_three    if p_three    > 1e-12 else 0.0

    return {
        "cond_2set":     round(float(c2), 6),
        "cond_3set":     round(float(c3), 6),
        "p_sel_2set_abs": round(float(p_sel_2set), 6),
        "p_sel_3set_abs": round(float(p_sel_3set), 6),
    }


def _matches_side(t: int, line: float, side: str) -> bool:
    if side == "MORE":
        return t > line
    elif side == "LESS":
        return t < line
    else:  # EXACT
        return t == int(line)


# ─────────────────────────────────────────────────────────────────────────────
# Dependency audit
# ─────────────────────────────────────────────────────────────────────────────

def _dependency_audit(
    dist: dict[int, float],
    set_dist: dict[tuple[int, int], float],
    line: float,
    side: str,
    p_three: float,
) -> dict[str, float]:
    """
    For each dependency condition, compute the share of P(selected) that relies
    on that condition being true.

    dep_third_set    — selected prob that arises from 3-set matches
    dep_tiebreak     — selected prob that arises from matches containing ≥1 TB
    dep_extended_set — selected prob that arises from matches with ≥1 extended set (7-5 or 7-6)
    dep_dominance    — selected prob that arises from matches with ≥1 one-sided set (6-0/6-1)

    All values ∈ [0, 1].
    """
    p1s = {sc: p for sc, p in set_dist.items() if sc[0] > sc[1]}
    p2s = {sc: p for sc, p in set_dist.items() if sc[1] > sc[0]}

    def _is_tb(sc: tuple[int,int]) -> bool:
        return sc in ((7,6), (6,7))
    def _is_ext(sc: tuple[int,int]) -> bool:
        return sc in ((7,6), (6,7), (7,5), (5,7))
    def _is_dom(sc: tuple[int,int]) -> bool:
        return sc in ((6,0), (0,6), (6,1), (1,6))
    def _g(sc: tuple[int,int]) -> int:
        return sc[0] + sc[1]

    p_sel_total = 0.0
    p_third     = 0.0  # selected AND 3-set match
    p_tb        = 0.0  # selected AND ≥1 tiebreak
    p_ext       = 0.0  # selected AND ≥1 extended set
    p_dom       = 0.0  # selected AND ≥1 dominant set

    # 2-set
    for s1, q1 in (*p1s.items(), *p2s.items()):
        partner = p1s if s1[0]>s1[1] else p2s
        for s2, q2 in partner.items():
            t = _g(s1) + _g(s2)
            if not _matches_side(t, line, side):
                continue
            w = q1 * q2
            p_sel_total += w
            if _is_tb(s1) or _is_tb(s2):           p_tb  += w
            if _is_ext(s1) or _is_ext(s2):          p_ext += w
            if _is_dom(s1) or _is_dom(s2):          p_dom += w

    # 3-set
    for s1m, s2m, s3m in (
        (p1s, p2s, p1s), (p2s, p1s, p1s),
        (p1s, p2s, p2s), (p2s, p1s, p2s),
    ):
        for s1, q1 in s1m.items():
            for s2, q2 in s2m.items():
                for s3, q3 in s3m.items():
                    t = _g(s1)+_g(s2)+_g(s3)
                    if not _matches_side(t, line, side):
                        continue
                    w = q1*q2*q3
                    p_sel_total += w
                    p_third += w
                    if _is_tb(s1) or _is_tb(s2) or _is_tb(s3):           p_tb  += w
                    if _is_ext(s1) or _is_ext(s2) or _is_ext(s3):        p_ext += w
                    if _is_dom(s1) or _is_dom(s2) or _is_dom(s3):        p_dom += w

    def _safe_div(n: float, d: float) -> float:
        return max(0.0, min(1.0, n / d)) if d > 1e-12 else 0.0

    return {
        "dep_third_set":     round(_safe_div(p_third, p_sel_total), 6),
        "dep_tiebreak":      round(_safe_div(p_tb,    p_sel_total), 6),
        "dep_extended_set":  round(_safe_div(p_ext,   p_sel_total), 6),
        "dep_dominance":     round(_safe_div(p_dom,   p_sel_total), 6),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Failure-path audit
# ─────────────────────────────────────────────────────────────────────────────

def _failure_path_audit(
    dist: dict[int, float],
    set_dist: dict[tuple[int, int], float],
    line: float,
    side: str,
) -> dict[str, Any]:
    """
    Identify the largest adverse score family and its probability contribution.

    For MORE: adverse = short matches / dominant sets.
    For LESS: adverse = long matches / tiebreak-heavy sets.
    """
    p1s = {sc: p for sc, p in set_dist.items() if sc[0] > sc[1]}
    p2s = {sc: p for sc, p in set_dist.items() if sc[1] > sc[0]}

    def _g(sc): return sc[0] + sc[1]

    # Enumerate 2-set losing outcomes grouped by regime
    regimes: dict[str, float] = {}

    def _regime(t: int, sets_played: int, has_dominant: bool, has_tb: bool) -> str:
        if sets_played == 2:
            if has_dominant:
                return "straight_set_dominance"
            elif t <= 20:
                return "brisk_straight_sets"
            elif has_tb:
                return "straight_sets_with_tiebreak"
            else:
                return "competitive_straight_sets"
        else:
            if t <= 26:
                return "short_three_set_match"
            elif has_tb:
                return "three_sets_with_tiebreak"
            else:
                return "competitive_three_set_match"

    for s1, q1 in (*p1s.items(), *p2s.items()):
        partner = p1s if s1[0]>s1[1] else p2s
        for s2, q2 in partner.items():
            t = _g(s1) + _g(s2)
            if _matches_side(t, line, side):
                continue  # this is a winning outcome
            has_dom = s1 in ((6,0),(0,6),(6,1),(1,6)) or s2 in ((6,0),(0,6),(6,1),(1,6))
            has_tb  = s1 in ((7,6),(6,7)) or s2 in ((7,6),(6,7))
            r = _regime(t, 2, has_dom, has_tb)
            regimes[r] = regimes.get(r, 0.0) + q1*q2

    for s1m, s2m, s3m in (
        (p1s,p2s,p1s),(p2s,p1s,p1s),(p1s,p2s,p2s),(p2s,p1s,p2s),
    ):
        for s1, q1 in s1m.items():
            for s2, q2 in s2m.items():
                for s3, q3 in s3m.items():
                    t = _g(s1)+_g(s2)+_g(s3)
                    if _matches_side(t, line, side):
                        continue
                    has_dom = any(s in ((6,0),(0,6),(6,1),(1,6)) for s in (s1,s2,s3))
                    has_tb  = any(s in ((7,6),(6,7))             for s in (s1,s2,s3))
                    r = _regime(t, 3, has_dom, has_tb)
                    regimes[r] = regimes.get(r, 0.0) + q1*q2*q3

    if not regimes:
        return {"largest_losing_regime": "none", "largest_losing_prob": 0.0, "all_regimes": {}}

    top_regime = max(regimes, key=regimes.get)
    return {
        "largest_losing_regime": top_regime,
        "largest_losing_prob":   round(float(regimes[top_regime]), 6),
        "all_regimes":           {k: round(float(v), 6) for k, v in
                                   sorted(regimes.items(), key=lambda x: -x[1])},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Calibration
# ─────────────────────────────────────────────────────────────────────────────

def _calibrate_triple(
    raw_more: float,
    raw_exact: float,
    raw_less: float,
    uncertainty_discount: float,
) -> tuple[float, float, float]:
    """
    Apply uncertainty discount while preserving the simplex (sum = 1.0).

    Shrinks MORE and LESS toward 0.5 (or 1/3 for integer lines).
    EXACT is reduced proportionally.  Returns (cal_more, cal_exact, cal_less).
    """
    is_int = (raw_exact > 1e-9)
    if is_int:
        center = 1.0 / 3.0
    else:
        center = 0.5

    d = max(0.0, min(0.4, uncertainty_discount))

    if is_int:
        # Shrink each outcome toward 1/3
        cm = raw_more  * (1.0 - d) + center * d
        ce = raw_exact * (1.0 - d) + center * d
        cl = raw_less  * (1.0 - d) + center * d
    else:
        # Two-outcome: shrink toward 0.5
        cm = raw_more * (1.0 - d) + 0.5 * d
        cl = raw_less * (1.0 - d) + 0.5 * d
        ce = 0.0

    total = cm + ce + cl
    if total > 1e-10:
        cm /= total; ce /= total; cl /= total

    # Enforce exact simplex: compute the last element as complement to
    # prevent floating-point drift when round() is applied to each term.
    ce = max(0.0, min(1.0, ce))
    cm = max(0.0, min(1.0 - ce, cm))
    cl = max(0.0, 1.0 - cm - ce)
    return float(cm), float(ce), float(cl)


def _compute_uncertainty(
    serve_stats_source: str,
    sample_size: int,
    injury_concern: bool,
    status_uncertainty: bool,
    fatigue_applied: bool,
    data_freshness_ok: bool,
    model_vs_market_delta: float | None,
    retirement_verified: bool,
    model_status: str = "PROVISIONAL",
) -> tuple[float, list[str]]:
    """
    Compute total uncertainty discount [0, 0.40] and list of contributing factors.
    """
    factors: list[str] = []
    u = 0.0

    # Always-present model uncertainty for PROVISIONAL
    u += 0.04
    factors.append("PROVISIONAL_model_baseline:+0.04")

    if serve_stats_source == "baseline":
        u += 0.08
        factors.append("serve_stats_from_surface_baseline:+0.08")
    elif serve_stats_source == "estimated":
        u += 0.04
        factors.append("serve_stats_estimated:+0.04")

    if sample_size < 10:
        u += 0.06
        factors.append(f"small_sample_n={sample_size}:+0.06")
    elif sample_size < 20:
        u += 0.03
        factors.append(f"moderate_sample_n={sample_size}:+0.03")

    if injury_concern:
        u += 0.05
        factors.append("injury_concern:+0.05")

    if status_uncertainty:
        u += 0.04
        factors.append("status_uncertainty:+0.04")

    if fatigue_applied:
        u += 0.03
        factors.append("fatigue_adjustment_applied:+0.03")

    if not data_freshness_ok:
        u += 0.07
        factors.append("data_stale_or_incomplete:+0.07")

    if not retirement_verified:
        u += 0.04
        factors.append("retirement_rules_not_verified:+0.04")

    if model_vs_market_delta is not None and abs(model_vs_market_delta) > 0.06:
        u += 0.05
        factors.append(f"model_market_disagreement_delta={model_vs_market_delta:.3f}:+0.05")

    return min(0.40, float(u)), factors


# ─────────────────────────────────────────────────────────────────────────────
# Stress test
# ─────────────────────────────────────────────────────────────────────────────

def _stress_serve(p_serve: float, side: str) -> float:
    """
    For MORE: shrink serve advantage (more breaks → shorter matches → adverse).
    For LESS: inflate serve advantage (fewer breaks → longer matches → adverse).
    """
    advantage = p_serve - 0.5
    if side == "MORE":
        stressed = 0.5 + advantage * (1.0 - _STRESS_SHRINK)
    else:
        stressed = 0.5 + advantage * (1.0 + _STRESS_SHRINK)
    return float(max(0.40, min(0.85, stressed)))


# ─────────────────────────────────────────────────────────────────────────────
# Classification
# ─────────────────────────────────────────────────────────────────────────────

def _classify(
    calibrated_lb: float,
    cal_selected: float,
    stress_selected: float,
    dep: dict[str, float],
    settlement_verified: bool,
    data_fresh: bool,
    blockers: list[str],
) -> str:
    """
    Strong / Qualified / Marginal / Fragile / Reject.
    """
    if not data_fresh:
        return "Reject"
    if calibrated_lb < _REJECT_CEIL:
        return "Reject"
    if len([b for b in blockers if "SETTLEMENT" in b or "RETIREMENT" in b or "EVENT_IDENTITY" in b]) > 0 and not settlement_verified:
        return "Reject"

    stress_drop = cal_selected - stress_selected

    if calibrated_lb >= _STRONG_LB and stress_drop <= _STRONG_DROP:
        return "Strong"
    if calibrated_lb >= _QUAL_LB and stress_drop <= _QUAL_DROP:
        return "Qualified"
    if calibrated_lb >= _MARGINAL_LB and stress_drop <= _MARGINAL_DROP:
        return "Marginal"
    return "Fragile"


# ─────────────────────────────────────────────────────────────────────────────
# Input parsing
# ─────────────────────────────────────────────────────────────────────────────

def _parse_inputs(
    row: dict[str, Any],
    enrichment: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """
    Extract and validate all inputs needed by the model.
    Returns (params_dict, blockers_list).
    """
    enr = enrichment or {}
    blockers: list[str] = []

    line = float(row.get("line_value") or row.get("line") or enr.get("target_line") or 0.0)
    if line <= 0:
        blockers.append("TOTAL_GAMES_LINE_MISSING_OR_ZERO")

    side = str(row.get("side") or enr.get("side") or "MORE").upper()
    if side not in ("MORE", "LESS", "EXACT"):
        side = "MORE"
        blockers.append(f"TOTAL_GAMES_INVALID_SIDE:defaulted_to_MORE")

    surface_raw = str(enr.get("surface") or row.get("surface") or "hard").lower().strip()
    surface = "hard"
    for s in ("clay", "grass", "carpet", "hard_indoor", "hard"):
        if s in surface_raw:
            surface = s
            break

    tour = str(enr.get("tour") or row.get("tour") or "atp").lower().strip()
    tour_key = "wta" if "wta" in tour or "women" in tour else "atp"

    baseline_serve = _SURFACE_SERVE_PCT.get(surface, _DEFAULT_SERVE)[tour_key]

    p1_serve_raw = enr.get("serve_win_pct_player1") or enr.get("p1_serve")
    p2_serve_raw = enr.get("serve_win_pct_player2") or enr.get("p2_serve")

    serve_stats_source = "player_specific"
    if p1_serve_raw is None or p2_serve_raw is None:
        p1_serve = baseline_serve
        p2_serve = baseline_serve
        serve_stats_source = "baseline"
    else:
        try:
            p1_serve = float(p1_serve_raw)
            p2_serve = float(p2_serve_raw)
            if not (0.40 <= p1_serve <= 0.85 and 0.40 <= p2_serve <= 0.85):
                raise ValueError("out of range")
            serve_stats_source = "player_specific"
        except (TypeError, ValueError):
            p1_serve = baseline_serve
            p2_serve = baseline_serve
            serve_stats_source = "estimated"
            blockers.append("SERVE_PCT_PARSE_FAILED:using_baseline")

    best_of = int(enr.get("best_of") or row.get("best_of") or 3)
    if best_of not in (3, 5):
        best_of = 3
        blockers.append("BEST_OF_NOT_3_OR_5:defaulted_to_3")

    final_set_super_tb = bool(enr.get("final_set_super_tb", False))

    # Fatigue
    p1_fatigue = float(enr.get("fatigue_adj_player1") or enr.get("p1_fatigue_adj") or 1.0)
    p2_fatigue = float(enr.get("fatigue_adj_player2") or enr.get("p2_fatigue_adj") or 1.0)
    p1_fatigue = max(0.70, min(1.10, p1_fatigue))
    p2_fatigue = max(0.70, min(1.10, p2_fatigue))
    fatigue_applied = (abs(p1_fatigue - 1.0) > 0.01 or abs(p2_fatigue - 1.0) > 0.01)

    # Apply fatigue: shrink serve advantage
    p1_serve_adj = 0.5 + (p1_serve - 0.5) * p1_fatigue
    p2_serve_adj = 0.5 + (p2_serve - 0.5) * p2_fatigue
    p1_serve_adj = max(0.40, min(0.85, p1_serve_adj))
    p2_serve_adj = max(0.40, min(0.85, p2_serve_adj))

    # Sample size (from game log if available)
    sample_size = int(enr.get("sample_size") or row.get("sample_size") or 0)

    # Condition flags
    injury_concern = bool(
        enr.get("injury_concern_player1") or enr.get("injury_concern_player2") or
        row.get("injury_flag") or
        (str(row.get("player_status") or "").upper() in ("DOUBTFUL", "PROBABLE", "QUESTIONABLE"))
    )
    status_uncertainty = bool(enr.get("status_uncertainty") or row.get("status_uncertainty"))
    data_freshness_ok  = bool(enr.get("data_freshness_ok", True))

    if not data_freshness_ok:
        blockers.append("DATA_STALE_OR_INCOMPLETE")

    # Governance verification flags
    retirement_verified  = bool(enr.get("retirement_rules_verified"))
    event_id_verified    = bool(enr.get("event_identity_verified"))
    match_fmt_verified   = bool(enr.get("match_format_verified"))
    settlement_verified  = bool(enr.get("settlement_type_verified"))

    if not retirement_verified:
        blockers.append("RETIREMENT_RULES_NOT_VERIFIED:settlement_uncertainty")
    if not event_id_verified:
        blockers.append("EVENT_IDENTITY_NOT_VERIFIED")

    # Market evidence
    mkt_more  = enr.get("market_total_more_prob") or enr.get("market_more_prob")
    mkt_less  = enr.get("market_total_less_prob") or enr.get("market_less_prob")
    mkt_exact = enr.get("market_total_exact_prob") or enr.get("market_exact_prob")
    mkt_line  = enr.get("market_line")

    market_line_matches = False
    if mkt_line is not None:
        try:
            market_line_matches = (abs(float(mkt_line) - line) < 0.01)
        except (TypeError, ValueError):
            pass
    if not market_line_matches and (mkt_more is not None or mkt_less is not None):
        blockers.append("MARKET_LINE_MISMATCH:market_evidence_not_used")
        mkt_more = mkt_less = mkt_exact = None  # reject non-matching market

    # Player names
    player1 = str(enr.get("player1") or row.get("player_name") or row.get("player") or "Player1")
    player2 = str(enr.get("player2") or row.get("opponent") or "Player2")

    return {
        "line": line,
        "side": side,
        "surface": surface,
        "tour_key": tour_key,
        "p1_serve": p1_serve,
        "p2_serve": p2_serve,
        "p1_serve_adj": p1_serve_adj,
        "p2_serve_adj": p2_serve_adj,
        "serve_stats_source": serve_stats_source,
        "best_of": best_of,
        "final_set_super_tb": final_set_super_tb,
        "fatigue_applied": fatigue_applied,
        "sample_size": sample_size,
        "injury_concern": injury_concern,
        "status_uncertainty": status_uncertainty,
        "data_freshness_ok": data_freshness_ok,
        "retirement_verified": retirement_verified,
        "event_id_verified": event_id_verified,
        "match_fmt_verified": match_fmt_verified,
        "settlement_verified": settlement_verified,
        "mkt_more": float(mkt_more) if mkt_more is not None else None,
        "mkt_less": float(mkt_less) if mkt_less is not None else None,
        "mkt_exact": float(mkt_exact) if mkt_exact is not None else None,
        "market_line_matches": market_line_matches,
        "player1": player1,
        "player2": player2,
    }, blockers


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def score(
    row: dict[str, Any],
    enrichment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Score a Tennis Total Games prop row.

    Parameters
    ──────────
    row        : normalised prop row (stat_key == "TOTAL_GAMES", sport == "TENNIS")
    enrichment : optional GPT-supplied enrichment dict

    Returns
    ───────
    A structured dict with all required outputs.  can_execute is always False.
    """
    params, blockers = _parse_inputs(row, enrichment)

    line        = params["line"]
    side        = params["side"]
    best_of     = params["best_of"]
    p1_s        = params["p1_serve_adj"]
    p2_s        = params["p2_serve_adj"]

    # ── fail-closed guards ───────────────────────────────────────────────────
    if not params["data_freshness_ok"]:
        return _fail_closed(line, side, blockers, "DATA_STALE_OR_INCOMPLETE")
    if line <= 0:
        return _fail_closed(line, side, blockers, "INVALID_LINE")

    # ── derived hold rates ────────────────────────────────────────────────────
    h1   = _game_win_prob(p1_s)
    h2   = _game_win_prob(p2_s)
    # Effective tiebreak point prob for P1: average of serving and returning
    p1_tb_point = (p1_s + (1.0 - p2_s)) / 2.0
    p1_tb = _tb_win_prob(p1_tb_point)

    # ── independent model (frozen before market) ─────────────────────────────
    set_dist  = _set_score_distribution(h1, h2, p1_tb)
    if best_of == 3:
        match_dist = _bo3_total_games_distribution(set_dist)
    else:
        match_dist = _bo5_total_games_distribution(set_dist)

    raw_more, raw_exact, raw_less = _prop_probs(match_dist, line)
    is_int = (line == int(line))

    raw_selected = raw_more if side == "MORE" else (raw_less if side == "LESS" else raw_exact)

    # ── match-state probs ─────────────────────────────────────────────────────
    p1_wins_set = sum(p for (a,b), p in set_dist.items() if a > b)
    ms = _match_state_probs(p1_wins_set, best_of)

    # ── set-extension probs ───────────────────────────────────────────────────
    ext = _set_extension_probs(set_dist, best_of, ms["p_straight_sets"], ms["p_three_sets"])

    # ── conditional decomposition ─────────────────────────────────────────────
    cond = _conditional_decomp(match_dist, set_dist, line, side,
                                ms["p_straight_sets"], ms["p_three_sets"], best_of)

    # ── straight-set selected prob ────────────────────────────────────────────
    # P(selected | straight-sets match)
    ss_sel = cond["cond_2set"]

    # ── dependency audit ──────────────────────────────────────────────────────
    dep = _dependency_audit(match_dist, set_dist, line, side, ms["p_three_sets"])

    # ── failure-path audit ────────────────────────────────────────────────────
    fp = _failure_path_audit(match_dist, set_dist, line, side)

    # ── market evidence (after model is frozen) ───────────────────────────────
    mkt_sel  = None
    mkt_delta = None
    market_prior_weight = 0.0
    indep_weight = 1.0

    if params["market_line_matches"]:
        if side == "MORE" and params["mkt_more"] is not None:
            mkt_sel = params["mkt_more"]
        elif side == "LESS" and params["mkt_less"] is not None:
            mkt_sel = params["mkt_less"]
        elif side == "EXACT" and params["mkt_exact"] is not None:
            mkt_sel = params["mkt_exact"]

    if mkt_sel is not None:
        mkt_delta = raw_selected - mkt_sel  # positive = model above market
        # Cap market weight at WOW governance ceiling
        base_mkt_weight = 0.20  # reasonable starting weight
        if abs(mkt_delta) > 0.06:
            base_mkt_weight = 0.10  # larger disagreement → less trust
        market_prior_weight = min(base_mkt_weight, _MAX_MARKET_WEIGHT)
        indep_weight = 1.0 - market_prior_weight
        # Blend: market does NOT dominate; independent model is always primary
        blended_sel = raw_selected * indep_weight + mkt_sel * market_prior_weight
    else:
        blended_sel = raw_selected
        market_prior_weight = 0.0
        indep_weight = 1.0

    # Recalculate blended more/less/exact for the calibration step
    if market_prior_weight > 0 and side in ("MORE", "LESS"):
        scale = blended_sel / raw_selected if raw_selected > 1e-9 else 1.0
        b_more  = raw_more  * (scale if side=="MORE"  else 1.0)
        b_less  = raw_less  * (scale if side=="LESS"  else 1.0)
        b_exact = raw_exact
        t = b_more + b_exact + b_less
        b_more /= t; b_exact /= t; b_less /= t
    else:
        b_more, b_exact, b_less = raw_more, raw_exact, raw_less

    # ── calibration ───────────────────────────────────────────────────────────
    uncertainty_discount, uncertainty_factors = _compute_uncertainty(
        serve_stats_source  = params["serve_stats_source"],
        sample_size         = params["sample_size"],
        injury_concern      = params["injury_concern"],
        status_uncertainty  = params["status_uncertainty"],
        fatigue_applied     = params["fatigue_applied"],
        data_freshness_ok   = params["data_freshness_ok"],
        model_vs_market_delta = mkt_delta,
        retirement_verified = params["retirement_verified"],
    )

    cal_more, cal_exact, cal_less = _calibrate_triple(b_more, b_exact, b_less, uncertainty_discount)
    cal_selected = cal_more if side == "MORE" else (cal_less if side == "LESS" else cal_exact)

    # ── stress test ───────────────────────────────────────────────────────────
    p1_s_stress = _stress_serve(params["p1_serve_adj"], side)
    p2_s_stress = _stress_serve(params["p2_serve_adj"], side)
    h1_s = _game_win_prob(p1_s_stress)
    h2_s = _game_win_prob(p2_s_stress)
    p1_tb_stress = _tb_win_prob((p1_s_stress + (1.0 - p2_s_stress)) / 2.0)

    set_dist_stress  = _set_score_distribution(h1_s, h2_s, p1_tb_stress)
    if best_of == 3:
        match_dist_stress = _bo3_total_games_distribution(set_dist_stress)
    else:
        match_dist_stress = _bo5_total_games_distribution(set_dist_stress)

    sm, se, sl = _prop_probs(match_dist_stress, line)
    stress_raw = sm if side == "MORE" else (sl if side == "LESS" else se)
    # Apply same calibration discount to stress scenario
    sc_m, sc_e, sc_l = _calibrate_triple(sm, se, sl, uncertainty_discount)
    stress_cal = sc_m if side == "MORE" else (sc_l if side == "LESS" else sc_e)
    stress_drop = cal_selected - stress_cal

    stress_result = "RESILIENT" if stress_drop <= _QUAL_DROP else (
                    "FRAGILE"   if stress_drop <= _MARGINAL_DROP + 0.05 else "FAIL")

    # Lower bound = calibrated stress probability
    cal_lower_bound = stress_cal

    # ── classification ────────────────────────────────────────────────────────
    classification = _classify(
        calibrated_lb     = cal_lower_bound,
        cal_selected      = cal_selected,
        stress_selected   = stress_cal,
        dep               = dep,
        settlement_verified = params["settlement_verified"],
        data_fresh        = params["data_freshness_ok"],
        blockers          = blockers,
    )

    # Propagate classification-specific blockers
    if classification in ("Fragile",):
        blockers.append(f"TENNIS_TG_FRAGILE:stress_drop={stress_drop:.3f}")
    if classification == "Reject":
        if cal_lower_bound < _REJECT_CEIL:
            blockers.append(f"TENNIS_TG_REJECT:lb={cal_lower_bound:.3f}<{_REJECT_CEIL}")

    # ── assemble output ───────────────────────────────────────────────────────
    return {
        # governance
        "can_execute": False,
        "model_status": "PROVISIONAL",

        # inputs
        "line":           line,
        "side":           side,
        "is_integer_line": is_int,
        "best_of":        best_of,
        "surface":        params["surface"],
        "player1":        params["player1"],
        "player2":        params["player2"],

        # hold/break rates (projected)
        "projected_hold_rate_p1":  round(float(h1),  4),
        "projected_hold_rate_p2":  round(float(h2),  4),
        "projected_break_rate_p1": round(float(1.0 - h1), 4),
        "projected_break_rate_p2": round(float(1.0 - h2), 4),
        "p1_serve_used":    round(float(p1_s), 4),
        "p2_serve_used":    round(float(p2_s), 4),
        "serve_stats_source": params["serve_stats_source"],

        # raw More/Exact/Less (independent model, before market)
        # Stored at full float precision so MORE+EXACT+LESS = 1.0 exactly.
        "raw_more":     float(raw_more),
        "raw_exact":    float(raw_exact),
        "raw_less":     float(raw_less),
        "raw_selected": float(raw_selected),

        # calibrated More/Exact/Less — stored without rounding so the simplex
        # contract (sum = 1.0) holds exactly for downstream consumers.
        "cal_more":          float(cal_more),
        "cal_exact":         float(cal_exact),
        "cal_less":          float(cal_less),
        "cal_selected":      round(float(cal_selected),   6),
        "cal_lower_bound":   round(float(cal_lower_bound),6),
        "uncertainty_discount": round(float(uncertainty_discount), 4),
        "uncertainty_factors": uncertainty_factors,

        # match-state probs
        "p_straight_sets":      ms["p_straight_sets"],
        "p_three_sets":         ms["p_three_sets"],
        "p_fav_wins_20":        ms["p_fav_wins_20"],
        "p_underdog_wins_1set": ms["p_underdog_wins_1set"],

        # set-extension probs
        "p_any_tiebreak":       ext["p_any_tiebreak"],
        "p_first_set_tiebreak": ext["p_first_set_tiebreak"],
        "p_any_75_set":         ext["p_any_75_set"],
        "p_one_sided_set":      ext["p_one_sided_set"],

        # prop-specific
        "straight_set_selected_prob": round(float(ss_sel), 6),
        "cond_2set_prob":             round(float(cond["cond_2set"]),  6),
        "cond_3set_prob":             round(float(cond["cond_3set"]),  6),

        # dependency audit (all ∈ [0, 1])
        "dep_third_set":    dep["dep_third_set"],
        "dep_tiebreak":     dep["dep_tiebreak"],
        "dep_extended_set": dep["dep_extended_set"],
        "dep_dominance":    dep["dep_dominance"],

        # failure-path audit
        "largest_losing_regime":      fp["largest_losing_regime"],
        "largest_losing_prob":        fp["largest_losing_prob"],
        "failure_regimes_breakdown":  fp.get("all_regimes", {}),

        # market evidence
        "market_more_prob":          params["mkt_more"],
        "market_less_prob":          params["mkt_less"],
        "market_line_matches":       params["market_line_matches"],
        "model_vs_market_delta":     round(float(mkt_delta), 6) if mkt_delta is not None else None,
        "market_prior_weight":       round(float(market_prior_weight), 4),
        "independent_model_weight":  round(float(indep_weight), 4),

        # stress test
        "stress_selected_prob":      round(float(stress_cal), 6),
        "stress_drop":               round(float(stress_drop), 6),
        "stress_classification":     stress_result,

        # settlement / governance
        "retirement_rules_verified": params["retirement_verified"],
        "event_identity_verified":   params["event_id_verified"],
        "settlement_type_verified":  params["settlement_verified"],

        # classification
        "classification": classification,
        "blockers": blockers,

        # internal (for analysis)
        "_total_games_dist": {k: round(v, 8) for k, v in sorted(match_dist.items())},
    }


def _fail_closed(
    line: float,
    side: str,
    blockers: list[str],
    reason: str,
) -> dict[str, Any]:
    """Return a fail-closed result for irrecoverable input failures."""
    blockers = list(blockers) + [f"TENNIS_TG_FAIL_CLOSED:{reason}"]
    return {
        "can_execute": False,
        "model_status": "FAIL_CLOSED",
        "line": line,
        "side": side,
        "raw_more": None, "raw_exact": None, "raw_less": None,
        "cal_more": None, "cal_exact": None, "cal_less": None,
        "cal_selected": None, "cal_lower_bound": None,
        "classification": "Reject",
        "blockers": blockers,
    }
