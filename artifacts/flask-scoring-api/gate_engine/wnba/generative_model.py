"""
gate_engine/wnba/generative_model.py  —  WOW v16 Clean Core
WNBA Player-Prop Generative Probability Engine

Architecture
============
  Stage 1  Verification   — slate / event / player / status / lineup / settlement
  Stage 2  Role regimes   — NORMAL_STARTER / USAGE_BUMP / BENCH_SECONDARY /
                            MINUTES_LIMIT / BLOWOUT_TRUNCATION / DNP_RISK
  Stage 3  Opportunity    — minutes-conditional possession and stat-specific
                            opportunity projection
  Stage 4  PMF            — mixture of Poisson distributions, correlated
                            through shared minutes for PRA composites
  Stage 5  Three-outcome  — More/Exact/Less (integer line) | binary (half-point)
  Stage 6  Dependency     — minutes / efficiency / close_game /
                            teammate_absence / overtime / 3PA / dominant share
  Stage 7  Failure path   — unconditional integration of adverse regimes
  Stage 8  Stress test    — conservative lower bound derived from adverse-but-
                            reasonable assumptions, not a fixed haircut
  Stage 9  Calibration    — uncertainty discount; market blend capped at 25%;
                            independent_model_weight always ≥ 0.75
  Stage 10 L5/L10         — diagnostic evidence only; large divergence flagged
  Stage 11 Market sanity  — exact-line no-vig check on same settlement basis
  Stage 12 Final refresh  — mandatory when status/lineup > 2 h before game
  Stage 13 Label          — YES_MODEL_QUALIFIED ≥ 65% LB | HOLD | WATCH | REJECT

can_execute = False  (unconditional; module-level)
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

# Stage-2 provenance identifier for this model's calibration transform —
# emitted with every scored output so downstream ledgers carry real provenance.
CALIBRATION_METHOD = "wnba_generative_role_regime_mixture_v1"

can_execute = False  # UNCONDITIONAL — never set True

# ---------------------------------------------------------------------------
# League-average per-minute rates (WNBA starter baseline)
# These are used only when player-specific data is absent.
# Derived from WNBA season-average starter minutes of ~30/game.
# ---------------------------------------------------------------------------
_LEAGUE_PTS_PER_MIN  = 0.5625   # ≈18 pts / 32 min
_LEAGUE_REB_PER_MIN  = 0.1563   # ≈5 reb / 32 min
_LEAGUE_AST_PER_MIN  = 0.0938   # ≈3 ast / 32 min
_LEAGUE_STL_PER_MIN  = 0.0375   # ≈1.2 stl / 32 min
_LEAGUE_BLK_PER_MIN  = 0.0219   # ≈0.7 blk / 32 min
_LEAGUE_TOV_PER_MIN  = 0.0625   # ≈2.0 tov / 32 min
_LEAGUE_3PM_PER_MIN  = 0.0469   # ≈1.5 3pm / 32 min
_LEAGUE_3PA_PER_MIN  = 0.1250   # ≈4.0 3pa / 32 min
_LEAGUE_3P_PCT       = 0.370
_LEAGUE_FG_PCT       = 0.450
_LEAGUE_FT_PCT       = 0.780
_WNBA_POSSESSIONS_PER_MIN = 1.81  # ≈87 poss / 48 min

# ---------------------------------------------------------------------------
# PMF and grid constants
# ---------------------------------------------------------------------------
_MAX_K              = 55    # maximum stat value considered in PMF
_MINUTES_GRID_STEPS = 40    # grid resolution for truncated-normal minutes integration

# Precomputed log factorials for fast Poisson PMF evaluation
_LOG_FACT: list[float] = [0.0]
for _i in range(1, _MAX_K + 2):
    _LOG_FACT.append(_LOG_FACT[-1] + math.log(_i))

# ---------------------------------------------------------------------------
# Governance thresholds (65% floor for YES_MODEL_QUALIFIED)
# ---------------------------------------------------------------------------
_YES_QUALIFIED_FLOOR = 0.65  # calibrated lower bound must be ≥ 65%
_HOLD_FLOOR          = 0.52
_WATCH_FLOOR         = 0.47
_MAX_MARKET_PRIOR_WT = 0.25  # market data can never exceed 25% weight
_OT_PROBABILITY_WNBA = 0.12  # ~12% WNBA games go to OT
_OT_EXTRA_MINUTES    = 5.0   # expected extra minutes when OT occurs

# ---------------------------------------------------------------------------
# Role regime names
# ---------------------------------------------------------------------------
ROLE_NORMAL_STARTER     = "NORMAL_STARTER"
ROLE_USAGE_BUMP         = "USAGE_BUMP"
ROLE_BENCH_SECONDARY    = "BENCH_SECONDARY"
ROLE_MINUTES_LIMIT      = "MINUTES_LIMIT"
ROLE_BLOWOUT_TRUNCATION = "BLOWOUT_TRUNCATION"
ROLE_DNP_RISK           = "DNP_RISK"

# ---------------------------------------------------------------------------
# Stat key → component rate keys
# For composite stats, the total rate = sum of component rates.
# Correlation between components is captured through shared minutes.
# ---------------------------------------------------------------------------
_STAT_COMPONENTS: dict[str, list[str]] = {
    "PTS":          ["pts"],
    "POINTS":       ["pts"],
    "REB":          ["reb"],
    "REBOUNDS":     ["reb"],
    "AST":          ["ast"],
    "ASSISTS":      ["ast"],
    "STL":          ["stl"],
    "STEALS":       ["stl"],
    "BLK":          ["blk"],
    "BLOCKS":       ["blk"],
    "TOV":          ["tov"],
    "TO":           ["tov"],
    "3PM":          ["threepm"],
    "FG3M":         ["threepm"],
    "PRA":          ["pts", "reb", "ast"],
    "PTS+REB+AST":  ["pts", "reb", "ast"],
    "PTS+REB":      ["pts", "reb"],
    "PTS+AST":      ["pts", "ast"],
    "REB+AST":      ["reb", "ast"],
}

SUPPORTED_STAT_KEYS: frozenset[str] = frozenset(_STAT_COMPONENTS)

_STAT_KEY_ALIASES: dict[str, str] = {
    "POINTS":                    "PTS",
    "REBOUNDS":                  "REB",
    "ASSISTS":                   "AST",
    "STEALS":                    "STL",
    "BLOCKS":                    "BLK",
    "TO":                        "TOV",
    "FG3M":                      "3PM",
    "THREES":                    "3PM",
    "THREE_POINTERS_MADE":       "3PM",
    "PTS_REB_AST":               "PRA",
    "POINTS_REBOUNDS_ASSISTS":   "PRA",
    "POINTS+REBOUNDS+ASSISTS":   "PRA",
    "PTS_REB":                   "PTS+REB",
    "PTS_AST":                   "PTS+AST",
    "REB_AST":                   "REB+AST",
}

_STAT_ALIASES: dict[str, list[str]] = {
    "pts":     ["PTS", "pts", "points", "POINTS"],
    "reb":     ["REB", "reb", "rebounds", "REBOUNDS", "TRB"],
    "ast":     ["AST", "ast", "assists", "ASSISTS"],
    "stl":     ["STL", "stl", "steals", "STEALS"],
    "blk":     ["BLK", "blk", "blocks", "BLOCKS"],
    "tov":     ["TOV", "tov", "TO", "to", "turnovers"],
    "threepm": ["3PM", "FG3M", "3pm", "fg3m", "3P"],
}


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

def _poisson_pmf(k: int, lam: float) -> float:
    """Exact Poisson PMF using precomputed log factorials to avoid overflow."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    if k < 0 or k > _MAX_K:
        return 0.0
    return math.exp(k * math.log(lam) - lam - _LOG_FACT[k])


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via erfc."""
    return 0.5 * math.erfc(-x * math.sqrt(0.5))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _truncnorm_weights(
    grid: list[float],
    mu: float,
    sd: float,
    a: float,
    b: float,
) -> list[float]:
    """
    Unnormalized PDF weights for a truncated normal distribution on a discrete grid.
    When sd == 0, all weight is placed at the grid point nearest to clamp(mu, a, b).
    Returns non-negative weights (not normalized to sum=1).
    """
    if sd <= 0:
        clamped = max(a, min(b, mu))
        nearest = min(range(len(grid)), key=lambda i: abs(grid[i] - clamped))
        return [1.0 if i == nearest else 0.0 for i in range(len(grid))]
    za    = (a - mu) / sd
    zb    = (b - mu) / sd
    denom = _norm_cdf(zb) - _norm_cdf(za)
    if denom < 1e-12:
        denom = 1e-12
    out = []
    for x in grid:
        z = (x - mu) / sd
        out.append(max(0.0, _norm_pdf(z) / (sd * denom)))
    return out


# ---------------------------------------------------------------------------
# Role regime class
# ---------------------------------------------------------------------------

class RoleRegime:
    """One row in the discrete role-regime mixture."""
    __slots__ = (
        "name", "prior", "minutes_mean", "minutes_sd",
        "minutes_floor", "minutes_ceiling", "opp_multiplier",
    )

    def __init__(
        self,
        name: str,
        prior: float,
        minutes_mean: float,
        minutes_sd: float,
        minutes_floor: float,
        minutes_ceiling: float,
        opp_multiplier: float = 1.0,
    ) -> None:
        self.name            = name
        self.prior           = max(0.0, float(prior))
        self.minutes_mean    = float(minutes_mean)
        self.minutes_sd      = max(0.0, float(minutes_sd))
        self.minutes_floor   = float(minutes_floor)
        self.minutes_ceiling = float(minutes_ceiling)
        self.opp_multiplier  = float(opp_multiplier)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":            self.name,
            "prior":           round(self.prior, 4),
            "minutes_mean":    round(self.minutes_mean, 2),
            "minutes_sd":      round(self.minutes_sd, 2),
            "minutes_floor":   round(self.minutes_floor, 2),
            "minutes_ceiling": round(self.minutes_ceiling, 2),
            "opp_multiplier":  round(self.opp_multiplier, 3),
        }


# ---------------------------------------------------------------------------
# Stage 2: Role regime builder
# ---------------------------------------------------------------------------

def _build_regimes(
    enrichment: dict[str, Any],
    opp_gate: dict[str, Any],
) -> list[RoleRegime]:
    """
    Construct the role-regime mixture from enrichment and opportunity-gate data.
    Adjusts regime priors based on teammate dependency, restrictions, DNP risk,
    and blowout context.  Priors are re-normalized to sum exactly to 1.0.
    """
    # Base minutes: prefer opportunity gate (already validated) over enrichment
    base_min = float(
        opp_gate.get("expected_minutes")
        or enrichment.get("avg_minutes")
        or enrichment.get("player_minutes_per_game")
        or 30.0
    )
    base_min = max(1.0, base_min)
    min_sd   = max(2.0, base_min * 0.14)   # ~14% coefficient of variation baseline

    role_state = str(
        opp_gate.get("role_state")
        or enrichment.get("role_state")
        or "STARTER"
    ).upper()
    is_starter = "STARTER" in role_state or "STAR" in role_state

    if not is_starter:
        base_min = min(base_min, 22.0)

    # Default priors
    p_normal  = 0.70 if is_starter else 0.45
    p_bump    = 0.08
    p_bench   = 0.05 if is_starter else 0.30
    p_mlimit  = 0.05
    p_blowout = 0.10
    p_dnp     = 0.02

    # Primary teammate dependency → shifts USAGE_BUMP weight
    ptd = str(
        opp_gate.get("primary_teammate_dependency")
        or enrichment.get("primary_teammate_dependency")
        or ""
    ).upper()
    if ptd == "HIGH":
        p_bump   = 0.25
        p_normal = max(0.30, p_normal - 0.17)
    elif ptd == "MEDIUM":
        p_bump   = 0.15
        p_normal = max(0.40, p_normal - 0.07)

    # Minutes restriction
    restriction  = enrichment.get("restriction_flag") or enrichment.get("minutes_restriction")
    min_limit_v: float | None = None
    if restriction:
        p_mlimit  = 0.30
        p_normal  = max(0.30, p_normal - 0.25)
        try:
            min_limit_v = float(enrichment.get("minutes_limit") or base_min * 0.75)
        except (TypeError, ValueError):
            min_limit_v = base_min * 0.75

    # DNP risk
    dnp_risk = str(enrichment.get("dnp_risk") or "").upper()
    if dnp_risk == "HIGH":
        p_dnp    = 0.12
        p_normal = max(0.25, p_normal - 0.10)
    elif dnp_risk == "MEDIUM":
        p_dnp    = 0.06
        p_normal = max(0.35, p_normal - 0.04)

    # Blowout risk
    blowout_risk = str(enrichment.get("blowout_risk") or "").upper()
    if blowout_risk == "HIGH":
        p_blowout = 0.22
        p_normal  = max(0.25, p_normal - 0.12)

    # Build the six regimes
    normal = RoleRegime(
        ROLE_NORMAL_STARTER, p_normal,
        minutes_mean    = base_min,
        minutes_sd      = min_sd,
        minutes_floor   = max(10.0, base_min * 0.55),
        minutes_ceiling = min(40.0, base_min * 1.35),
    )
    usage_bump = RoleRegime(
        ROLE_USAGE_BUMP, p_bump,
        minutes_mean    = min(40.0, base_min * 1.15),
        minutes_sd      = max(2.0, min_sd * 0.75),
        minutes_floor   = max(25.0, base_min * 0.85),
        minutes_ceiling = 40.0,
        opp_multiplier  = 1.15,
    )
    bench = RoleRegime(
        ROLE_BENCH_SECONDARY, p_bench,
        minutes_mean    = min(base_min * 0.55, 18.0),
        minutes_sd      = 5.0,
        minutes_floor   = 2.0,
        minutes_ceiling = min(base_min * 0.80, 26.0),
        opp_multiplier  = 0.90,
    )
    mlimit_mean = min_limit_v if min_limit_v is not None else min(base_min * 0.75, 24.0)
    minlimit = RoleRegime(
        ROLE_MINUTES_LIMIT, p_mlimit,
        minutes_mean    = mlimit_mean,
        minutes_sd      = 2.5,
        minutes_floor   = max(8.0, mlimit_mean - 6.0),
        minutes_ceiling = min(mlimit_mean + 3.0, 35.0),
    )
    blowout = RoleRegime(
        ROLE_BLOWOUT_TRUNCATION, p_blowout,
        minutes_mean    = min(base_min * 0.65, 22.0),
        minutes_sd      = 5.5,
        minutes_floor   = 5.0,
        minutes_ceiling = min(base_min * 0.90, 35.0),
        opp_multiplier  = 0.85,
    )
    dnp = RoleRegime(
        ROLE_DNP_RISK, p_dnp,
        minutes_mean    = 0.0,
        minutes_sd      = 0.0,
        minutes_floor   = 0.0,
        minutes_ceiling = 0.0,
    )

    regimes = [normal, usage_bump, bench, minlimit, blowout, dnp]

    # Normalize priors to sum exactly to 1.0; enforce complement for last element
    total = sum(r.prior for r in regimes)
    if total > 1e-10:
        for r in regimes:
            r.prior /= total
        # Complement normalization
        adj = 1.0 - sum(r.prior for r in regimes[:-1])
        regimes[-1].prior = max(0.0, adj)

    return regimes


# ---------------------------------------------------------------------------
# Stage 3: Per-minute rate derivation
# ---------------------------------------------------------------------------

def _derive_rates(
    enrichment: dict[str, Any],
    opp_gate: dict[str, Any],
    matchup_adj: float = 1.0,
) -> dict[str, Any]:
    """
    Derive per-minute rates for all stats.
    Prefers player-specific data from enrichment; falls back to WNBA league averages.
    matchup_adj is applied to offensive rates (PTS, AST, 3PM) only.
    """
    base_min = float(
        opp_gate.get("expected_minutes")
        or enrichment.get("avg_minutes")
        or enrichment.get("player_minutes_per_game")
        or 30.0
    )
    base_min = max(1.0, base_min)
    adj = max(0.50, min(1.50, float(matchup_adj)))

    def _rate(pgame_key: str, pmin_key: str, fallback: float) -> float:
        v = enrichment.get(pmin_key)
        if v is not None:
            try:
                return max(0.0, float(v))
            except (TypeError, ValueError):
                pass
        v = enrichment.get(pgame_key) or enrichment.get(f"player_{pgame_key}")
        if v is not None:
            try:
                return max(0.0, float(v)) / base_min
            except (TypeError, ValueError):
                pass
        return fallback

    from_player_data = (
        enrichment.get("pts_per_game") is not None
        or enrichment.get("pts_per_min") is not None
        or enrichment.get("player_pts_per_game") is not None
    )

    return {
        "pts":             _rate("pts_per_game",    "pts_per_min",    _LEAGUE_PTS_PER_MIN)  * adj,
        "reb":             _rate("reb_per_game",    "reb_per_min",    _LEAGUE_REB_PER_MIN),
        "ast":             _rate("ast_per_game",    "ast_per_min",    _LEAGUE_AST_PER_MIN)  * adj,
        "stl":             _rate("stl_per_game",    "stl_per_min",    _LEAGUE_STL_PER_MIN),
        "blk":             _rate("blk_per_game",    "blk_per_min",    _LEAGUE_BLK_PER_MIN),
        "tov":             _rate("tov_per_game",    "tov_per_min",    _LEAGUE_TOV_PER_MIN),
        "threepm":         _rate("threepm_per_game","threepm_per_min",_LEAGUE_3PM_PER_MIN)  * adj,
        "threepma":        _rate("threepa_per_game","threepa_per_min",_LEAGUE_3PA_PER_MIN),
        "three_pct":       float(enrichment.get("three_pct") or _LEAGUE_3P_PCT),
        "fg_pct":          float(enrichment.get("fg_pct") or _LEAGUE_FG_PCT),
        "ft_pct":          float(enrichment.get("ft_pct") or _LEAGUE_FT_PCT),
        "_from_player_data": from_player_data,
    }


# ---------------------------------------------------------------------------
# Stage 4: PMF computation
# ---------------------------------------------------------------------------

def _compute_regime_pmf(
    regime: RoleRegime,
    total_rate_per_min: float,
    max_k: int = _MAX_K,
) -> list[float]:
    """
    PMF of stat count for one regime.
    Minutes are drawn from a truncated normal distribution;
    stat count | minutes ~ Poisson(rate * minutes).
    """
    pmf = [0.0] * (max_k + 1)

    if regime.minutes_ceiling <= 0.0 or regime.prior <= 0.0:
        # DNP: P(stat = 0) = 1
        pmf[0] = 1.0
        return pmf

    a, b = regime.minutes_floor, regime.minutes_ceiling
    if a >= b:
        lam = total_rate_per_min * regime.opp_multiplier * regime.minutes_mean
        for k in range(max_k + 1):
            pmf[k] = _poisson_pmf(k, lam)
        return pmf

    grid = [a + (b - a) * i / (_MINUTES_GRID_STEPS - 1) for i in range(_MINUTES_GRID_STEPS)]
    wts  = _truncnorm_weights(grid, regime.minutes_mean, regime.minutes_sd, a, b)
    wsum = sum(wts)
    if wsum <= 0:
        pmf[0] = 1.0
        return pmf

    eff_rate = total_rate_per_min * regime.opp_multiplier

    for m, w in zip(grid, wts):
        lam = eff_rate * m
        nw  = w / wsum   # normalized weight for this grid point
        if lam <= 1e-12:
            pmf[0] += nw
            continue
        log_lam  = math.log(lam)
        log_elam = -lam
        for k in range(max_k + 1):
            if k == 0:
                pmf[0] += nw * math.exp(log_elam)
            else:
                pmf[k] += nw * math.exp(k * log_lam + log_elam - _LOG_FACT[k])

    return pmf


def _compute_full_pmf(
    regimes: list[RoleRegime],
    total_rate_per_min: float,
    max_k: int = _MAX_K,
) -> list[float]:
    """
    Full mixture PMF:  P(k) = Σ_r P(regime=r) * P(k | regime=r).
    Last element enforced as complement to preserve exact simplex sum = 1.
    """
    full = [0.0] * (max_k + 1)
    for r in regimes:
        if r.prior <= 0:
            continue
        rpmf = _compute_regime_pmf(r, total_rate_per_min, max_k)
        for k in range(max_k + 1):
            full[k] += r.prior * rpmf[k]

    # Enforce exact simplex: last element = complement
    full[-1] = max(0.0, 1.0 - sum(full[:-1]))
    return full


# ---------------------------------------------------------------------------
# Stage 5: Three-outcome extraction
# ---------------------------------------------------------------------------

def _pmf_to_outcomes(
    pmf: list[float],
    line: float,
    is_integer: bool,
) -> tuple[float, float, float]:
    """
    Extract (more, exact, less) from a PMF.
    Integer lines: three-outcome with P(k = int_line) as exact.
    Half-point lines: binary with exact = 0.
    Simplex preserved via complement.
    """
    int_line = int(round(line))
    p_more   = 0.0
    p_exact  = 0.0
    for k, p in enumerate(pmf):
        if k > line:
            p_more += p
        elif is_integer and k == int_line:
            p_exact += p
    p_less = max(0.0, 1.0 - p_more - p_exact)
    return float(p_more), float(p_exact), float(p_less)


def _outcomes_from_lambda(
    lam: float,
    line: float,
    is_integer: bool,
) -> tuple[float, float, float]:
    """
    Three-outcome extraction directly from a Poisson(lam).
    Avoids building a full PMF array; used in inner loops.
    """
    int_line = int(round(line))
    p_more   = 0.0
    p_exact  = 0.0
    if lam <= 1e-12:
        p_less = 1.0 if (not is_integer or int_line > 0) else 0.0
        p_exact_d = 1.0 if (is_integer and int_line == 0) else 0.0
        return 0.0, p_exact_d, p_less

    log_lam  = math.log(lam)
    log_elam = -lam
    for k in range(_MAX_K + 1):
        p_k = math.exp(k * log_lam + log_elam - _LOG_FACT[k]) if k > 0 else math.exp(log_elam)
        if k > line:
            p_more += p_k
        elif is_integer and k == int_line:
            p_exact += p_k
    p_less = max(0.0, 1.0 - p_more - p_exact)
    return float(p_more), float(p_exact), float(p_less)


# ---------------------------------------------------------------------------
# Stage 6: Dependency audit
# ---------------------------------------------------------------------------

def _compute_dependencies(
    regimes: list[RoleRegime],
    rates: dict[str, Any],
    stat_key: str,
    total_rate_pm: float,
    line: float,
    is_integer: bool,
    full_pmf: list[float],
    side: str,
) -> dict[str, Any]:
    """
    Compute all dependency measures.  All values are in [0, 1].

    minutes_dependency        — share of P(selected) from high-minutes scenarios
    efficiency_dependency     — sensitivity to 15% per-minute rate reduction
    close_game_dependency     — share of P(selected) excluding blowout regime
    teammate_absence_dependency — share from USAGE_BUMP (primary teammate out)
    overtime_dependency       — marginal P(selected) attributable to OT extra minutes
    three_pa_dependency       — fraction of projected PTS from 3-pointers (scoring only)
    dominant_dependency_share — maximum of all six measures
    dominant_dependency_name  — name of the dominant dependency
    """
    idx = 0 if side == "MORE" else 2
    p_sel = _pmf_to_outcomes(full_pmf, line, is_integer)[idx]
    if p_sel < 1e-12:
        return {
            "minutes_dependency":          0.0,
            "efficiency_dependency":       0.0,
            "close_game_dependency":       0.0,
            "teammate_absence_dependency": 0.0,
            "overtime_dependency":         0.0,
            "three_pa_dependency":         0.0,
            "dominant_dependency_share":   0.0,
            "dominant_dependency_name":    "none",
        }

    # 1. Minutes dependency — high-minutes threshold = 90% of normal-starter mean
    normal_r  = next((r for r in regimes if r.name == ROLE_NORMAL_STARTER), None)
    hi_thresh = (normal_r.minutes_mean if normal_r else 28.0) * 0.90

    p_sel_hi = 0.0
    for r in regimes:
        if r.prior <= 0 or r.minutes_ceiling <= 0:
            continue
        a, b = r.minutes_floor, r.minutes_ceiling
        grid = [a + (b - a) * i / (_MINUTES_GRID_STEPS - 1) for i in range(_MINUTES_GRID_STEPS)]
        wts  = _truncnorm_weights(grid, r.minutes_mean, r.minutes_sd, a, b)
        wsum = sum(wts)
        if wsum <= 0:
            continue
        eff = total_rate_pm * r.opp_multiplier
        for m, w in zip(grid, wts):
            if m < hi_thresh:
                continue
            lam = eff * m
            mo, ex, le = _outcomes_from_lambda(lam, line, is_integer)
            p_sel_hi += r.prior * (w / wsum) * (mo if side == "MORE" else le)

    minutes_dep = min(1.0, p_sel_hi / p_sel)

    # 2. Efficiency dependency — sensitivity to 15% rate reduction
    red_rate   = total_rate_pm * 0.85
    pmf_red    = _compute_full_pmf(regimes, red_rate)
    p_sel_red  = _pmf_to_outcomes(pmf_red, line, is_integer)[idx]
    eff_dep    = min(1.0, max(0.0, (p_sel - p_sel_red) / p_sel))

    # 3. Close-game dependency — P(selected) excluding blowout regime
    p_sel_no_blowout = 0.0
    for r in regimes:
        if r.name == ROLE_BLOWOUT_TRUNCATION or r.prior <= 0:
            continue
        r_pmf = _compute_regime_pmf(r, total_rate_pm)
        outcomes = _pmf_to_outcomes(r_pmf, line, is_integer)
        p_sel_no_blowout += r.prior * outcomes[idx]
    close_dep = min(1.0, p_sel_no_blowout / p_sel)

    # 4. Teammate absence dependency — USAGE_BUMP contribution
    bump_r = next((r for r in regimes if r.name == ROLE_USAGE_BUMP), None)
    teammate_dep = 0.0
    if bump_r and bump_r.prior > 0:
        bump_pmf = _compute_regime_pmf(bump_r, total_rate_pm)
        teammate_dep = min(1.0, bump_r.prior * _pmf_to_outcomes(bump_pmf, line, is_integer)[idx] / p_sel)

    # 5. Overtime dependency — marginal impact of WNBA OT
    ot_dep = _overtime_dependency(regimes, total_rate_pm, line, is_integer, side, p_sel)

    # 6. 3PA dependency (scoring props only)
    three_pa_dep = 0.0
    if "pts" in _STAT_COMPONENTS.get(stat_key, []):
        pts_rate = rates.get("pts", 1e-9) or 1e-9
        three_contrib = 3.0 * (rates.get("threepm") or 0.0)
        three_pa_dep = min(1.0, max(0.0, three_contrib / pts_rate))

    deps: dict[str, float] = {
        "minutes_dependency":          minutes_dep,
        "efficiency_dependency":       eff_dep,
        "close_game_dependency":       close_dep,
        "teammate_absence_dependency": teammate_dep,
        "overtime_dependency":         ot_dep,
        "three_pa_dependency":         three_pa_dep,
    }
    dom_name  = max(deps, key=lambda k_: deps[k_])
    dom_share = deps[dom_name]

    return {
        **deps,
        "dominant_dependency_share": float(dom_share),
        "dominant_dependency_name":  dom_name,
    }


def _overtime_dependency(
    regimes: list[RoleRegime],
    rate: float,
    line: float,
    is_integer: bool,
    side: str,
    p_sel: float,
) -> float:
    """
    Overtime dependency: estimated P(selected) attributable to OT minutes.
    Models one 5-minute OT period occurring with WNBA_OT probability.
    Uses NORMAL_STARTER as the reference regime.
    """
    normal_r = next((r for r in regimes if r.name == ROLE_NORMAL_STARTER), None)
    if not normal_r or normal_r.prior <= 0:
        return 0.0

    eff = rate * normal_r.opp_multiplier
    lam_no_ot = eff * normal_r.minutes_mean
    lam_ot    = eff * (normal_r.minutes_mean + _OT_EXTRA_MINUTES)

    mo_no, _, le_no = _outcomes_from_lambda(lam_no_ot, line, is_integer)
    mo_ot,  _, le_ot = _outcomes_from_lambda(lam_ot,  line, is_integer)

    if side == "MORE":
        delta = max(0.0, mo_ot - mo_no)
    else:
        delta = max(0.0, le_no - le_ot)

    impact = delta * _OT_PROBABILITY_WNBA * normal_r.prior
    return min(1.0, impact / max(p_sel, 1e-12))


# ---------------------------------------------------------------------------
# Stage 7: Failure-path audit
# ---------------------------------------------------------------------------

def _failure_path_audit(
    regimes: list[RoleRegime],
    total_rate_pm: float,
    line: float,
    is_integer: bool,
    side: str,
) -> tuple[str, float]:
    """
    Identify the regime that contributes most probability mass to the adverse outcome.
    For a MORE bet, the adverse outcome is LESS; for LESS, it is MORE.

    Returns (regime_name, adverse_probability_contribution).
    This is an unconditional integration — every regime participates.
    """
    adverse_idx = 2 if side == "MORE" else 0   # MORE bets lose on LESS outcomes

    best_name    = ROLE_DNP_RISK
    best_contrib = 0.0

    for r in regimes:
        if r.prior <= 0:
            continue
        r_pmf    = _compute_regime_pmf(r, total_rate_pm)
        outcomes = _pmf_to_outcomes(r_pmf, line, is_integer)
        contrib  = r.prior * outcomes[adverse_idx]
        if contrib > best_contrib:
            best_contrib = contrib
            best_name    = r.name

    return best_name, float(best_contrib)


# ---------------------------------------------------------------------------
# Stage 8: Stress test
# ---------------------------------------------------------------------------

def _stress_pmf(
    regimes: list[RoleRegime],
    total_rate_pm: float,
    side: str,
) -> list[float]:
    """
    Adverse-but-reasonable stress scenario.
    MORE: NORMAL_STARTER minutes mean -20%, rate -15%.
    LESS: NORMAL_STARTER minutes mean +20%, rate +15%.
    Other regimes unchanged.  Regime priors unchanged.
    """
    stressed: list[RoleRegime] = []
    for r in regimes:
        if r.name == ROLE_NORMAL_STARTER:
            if side == "MORE":
                sr = RoleRegime(
                    r.name, r.prior,
                    r.minutes_mean * 0.80, r.minutes_sd,
                    r.minutes_floor, r.minutes_ceiling, r.opp_multiplier,
                )
            else:
                sr = RoleRegime(
                    r.name, r.prior,
                    min(40.0, r.minutes_mean * 1.20), r.minutes_sd,
                    r.minutes_floor, r.minutes_ceiling, r.opp_multiplier,
                )
        else:
            sr = r
        stressed.append(sr)

    s_rate = total_rate_pm * (0.85 if side == "MORE" else 1.15)
    return _compute_full_pmf(stressed, s_rate)


# ---------------------------------------------------------------------------
# Stage 9: Calibration
# ---------------------------------------------------------------------------

def _uncertainty_discount(
    enrichment: dict[str, Any],
    regimes: list[RoleRegime],
    rates: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    """
    Compute uncertainty discount in [0, 0.60].
    Higher uncertainty → more shrinkage toward the naive prior (1/3 or 0.5).
    """
    factors: dict[str, float] = {}

    freshness = enrichment.get("status_freshness_hours")
    if freshness is None:
        factors["status_freshness"] = 0.15
    else:
        try:
            h = float(freshness)
            if h > 8:
                factors["status_freshness"] = 0.15
            elif h > 4:
                factors["status_freshness"] = 0.08
        except (TypeError, ValueError):
            factors["status_freshness"] = 0.10

    n_raw = enrichment.get("games_played") or enrichment.get("sample_size")
    if n_raw is None:
        factors["sample_size"] = 0.10
    else:
        try:
            n = int(float(n_raw))
            if n < 5:
                factors["sample_size"] = 0.12
            elif n < 10:
                factors["sample_size"] = 0.06
        except (TypeError, ValueError):
            factors["sample_size"] = 0.08

    dnp_r = next((r for r in regimes if r.name == ROLE_DNP_RISK), None)
    if dnp_r and dnp_r.prior > 0.05:
        factors["dnp_risk"] = 0.10

    if not rates.get("_from_player_data", False):
        factors["rate_source"] = 0.10

    if not enrichment.get("opponent_def_rating"):
        factors["matchup_quality"] = 0.05

    return min(0.60, sum(factors.values())), factors


def _calibrate_triple(
    raw_more: float,
    raw_exact: float,
    raw_less: float,
    discount: float,
    is_integer: bool,
) -> tuple[float, float, float]:
    """
    Shrink raw probabilities toward the naive prior, preserving the simplex.
    Integer lines → shrink toward 1/3; half-point → shrink toward 0.5.
    Last element computed as complement to prevent floating-point drift.
    """
    d = max(0.0, min(1.0, discount))
    if is_integer:
        ctr = 1.0 / 3.0
        cm = raw_more  * (1.0 - d) + ctr * d
        ce = raw_exact * (1.0 - d) + ctr * d
        cl = raw_less  * (1.0 - d) + ctr * d
        s  = cm + ce + cl
        if s > 1e-10:
            cm /= s; ce /= s
        cl = max(0.0, 1.0 - cm - ce)
    else:
        cm = raw_more * (1.0 - d) + 0.5 * d
        cl = raw_less * (1.0 - d) + 0.5 * d
        s  = cm + cl
        if s > 1e-10:
            cm /= s
        cl = max(0.0, 1.0 - cm)
        ce = 0.0
    return float(cm), float(ce), float(cl)


def _blend_market(
    cal_selected: float,
    market_prob: float | None,
    discount: float,
) -> tuple[float, float, float]:
    """
    Blend calibrated model probability with market no-vig probability.
    Market weight is hard-capped at MAX_MARKET_PRIOR_WT (25%).
    When market data is absent: market_prior_weight = 0, independent_model_weight = 1.

    Returns (blended_prob, market_prior_weight, independent_model_weight).
    """
    if market_prob is None:
        return float(cal_selected), 0.0, 1.0

    market_wt = min(_MAX_MARKET_PRIOR_WT, discount * 0.40)
    market_wt = max(0.05, market_wt)   # minimal floor when data is present
    model_wt  = 1.0 - market_wt

    blended = cal_selected * model_wt + float(market_prob) * market_wt
    return float(blended), float(market_wt), float(model_wt)


# ---------------------------------------------------------------------------
# Stage 10: L5/L10 diagnostic
# ---------------------------------------------------------------------------

def _l5_l10_diagnostic(
    enrichment: dict[str, Any],
    line: float,
    is_integer: bool,
    stat_key: str,
) -> dict[str, Any]:
    """
    Extract L5/L10 empirical statistics from game_log enrichment.
    These are DIAGNOSTIC EVIDENCE ONLY — they do not drive the generative
    probability.  Large divergence (> 0.15) between model and history is flagged.
    """
    game_log = enrichment.get("game_log") or []
    if not isinstance(game_log, list) or not game_log:
        return {
            "l5_stat_mean":   None,
            "l10_stat_mean":  None,
            "l5_more_rate":   None,
            "l10_more_rate":  None,
            "exact_line_l5":  None,
            "exact_line_l10": None,
            "l_history_note": "NO_GAME_LOG_PROVIDED",
        }

    vals: list[float] = []
    for g in game_log:
        if not isinstance(g, dict):
            continue
        v = _extract_stat_value(g, stat_key)
        if v is not None:
            vals.append(v)

    if not vals:
        return {
            "l5_stat_mean":   None,
            "l10_stat_mean":  None,
            "l5_more_rate":   None,
            "l10_more_rate":  None,
            "exact_line_l5":  None,
            "exact_line_l10": None,
            "l_history_note": "NO_PARSEABLE_STAT_VALUES",
        }

    l5  = vals[:5]
    l10 = vals[:10]
    int_line = int(round(line))

    def _more(vv: list[float]) -> float:
        return sum(1 for x in vv if x > line) / len(vv)

    def _exact(vv: list[float]) -> float | None:
        if not is_integer or not vv:
            return None
        return round(sum(1 for x in vv if int(round(x)) == int_line) / len(vv), 3)

    return {
        "l5_stat_mean":   round(sum(l5) / len(l5), 3) if l5 else None,
        "l10_stat_mean":  round(sum(l10) / len(l10), 3) if l10 else None,
        "l5_more_rate":   round(_more(l5), 3) if l5 else None,
        "l10_more_rate":  round(_more(l10), 3) if l10 else None,
        "exact_line_l5":  _exact(l5),
        "exact_line_l10": _exact(l10),
        "l_history_note": "OK",
    }


def _extract_stat_value(game: dict[str, Any], stat_key: str) -> float | None:
    """Extract a numeric stat total from a game-log dict using alias matching."""
    components = _STAT_COMPONENTS.get(stat_key, [])
    total = 0.0
    found = False
    for comp in components:
        for alias in _STAT_ALIASES.get(comp, [comp]):
            v = game.get(alias)
            if v is not None:
                try:
                    total += float(v)
                    found = True
                    break
                except (TypeError, ValueError):
                    pass
    return total if found else None


# ---------------------------------------------------------------------------
# Stage 11: Market sanity check
# ---------------------------------------------------------------------------

def _market_sanity(
    enrichment: dict[str, Any],
    line: float,
    is_integer: bool,
    raw_more: float,
) -> dict[str, Any]:
    """
    Exact-line no-vig market sanity check on the same settlement basis.
    Computes market_no_vig_prob for the MORE side from available market data.
    Does NOT drive the generative probability — informational only.
    Large model-market delta (> 0.10) is flagged.
    """
    more_p = enrichment.get("sportsbook_more_prob") or enrichment.get("market_total_more_prob")
    less_p = enrichment.get("sportsbook_less_prob") or enrichment.get("market_total_less_prob")
    m_odds = enrichment.get("sportsbook_more_odds") or enrichment.get("market_more_odds")
    l_odds = enrichment.get("sportsbook_less_odds") or enrichment.get("market_less_odds")

    market_no_vig: float | None = None

    if more_p is not None and less_p is not None:
        try:
            mp, lp = float(more_p), float(less_p)
            tot = mp + lp
            if tot > 0.01:
                market_no_vig = mp / tot
        except (TypeError, ValueError):
            pass
    elif m_odds is not None and l_odds is not None:
        try:
            market_no_vig = _american_to_no_vig(float(m_odds), float(l_odds))
        except (TypeError, ValueError):
            pass

    exact_market_no_vig: float | None = None
    if is_integer and market_no_vig is not None:
        exact_p = enrichment.get("market_exact_prob")
        if exact_p is not None:
            try:
                ep   = float(exact_p)
                madj = market_no_vig * (1 - ep)
                ladj = (1 - market_no_vig) * (1 - ep)
                tot  = madj + ep + ladj
                if tot > 0.01:
                    exact_market_no_vig = ep / tot
            except (TypeError, ValueError):
                pass

    delta: float | None = None
    large_delta = False
    if market_no_vig is not None:
        delta = round(raw_more - market_no_vig, 4)
        large_delta = abs(delta) > 0.10

    return {
        "market_no_vig_prob":        market_no_vig,
        "model_market_delta":        delta,
        "exact_line_market_no_vig":  exact_market_no_vig,
        "market_model_delta_large":  large_delta,
    }


def _american_to_no_vig(odds_more: float, odds_less: float) -> float:
    """Convert an American odds pair to the no-vig implied probability for MORE."""
    def _impl(o: float) -> float:
        return 100.0 / (100.0 + o) if o > 0 else (-o) / (-o + 100.0)

    mp  = _impl(odds_more)
    lp  = _impl(odds_less)
    tot = mp + lp
    return mp / tot if tot > 0.01 else 0.5


# ---------------------------------------------------------------------------
# Stage 12 / 1: Verification and final-refresh check
# ---------------------------------------------------------------------------

def _verify_inputs(
    row: dict[str, Any],
    enrichment: dict[str, Any],
) -> tuple[dict[str, bool], list[str], bool]:
    """
    Verify slate / event / player / status / lineup / settlement before modeling.
    Returns (verifications, blockers, final_refresh_required).
    """
    blockers: list[str] = []
    v: dict[str, bool] = {}

    # Slate
    v["slate_verified"] = bool(
        row.get("event_id") or row.get("slate_id") or enrichment.get("slate_id")
    )
    if not v["slate_verified"]:
        blockers.append("SLATE_NOT_VERIFIED:no_event_id_or_slate_id")

    # Event status
    ev_status = str(enrichment.get("event_status") or "UNKNOWN").upper()
    v["event_verified"] = ev_status in {"CONFIRMED", "ACTIVE", "LIVE", "SCHEDULED"}
    if not v["event_verified"]:
        blockers.append(f"EVENT_NOT_VERIFIED:status={ev_status}")

    # Player identity
    v["player_verified"] = bool(row.get("player_name") or row.get("player"))
    if not v["player_verified"]:
        blockers.append("PLAYER_NOT_VERIFIED:no_player_name")

    # Player status
    pl_status = str(enrichment.get("player_status") or "UNKNOWN").upper()
    active_set = {"ACTIVE", "CONFIRMED", "GAME_TIME_DECISION", "GTD", "PROBABLE"}
    v["status_verified"] = pl_status in active_set
    if not v["status_verified"] and pl_status != "UNKNOWN":
        if pl_status in {"OUT", "INACTIVE", "RULED_OUT", "DNP"}:
            blockers.append(f"PLAYER_STATUS_OUT:status={pl_status}")
        else:
            blockers.append(f"PLAYER_STATUS_UNRESOLVED:status={pl_status}")

    # Lineup
    lineup_ok = enrichment.get("lineup_confirmed")
    v["lineup_verified"] = bool(lineup_ok)
    if not v["lineup_verified"]:
        blockers.append("LINEUP_NOT_CONFIRMED")

    # Settlement basis
    settle = str(enrichment.get("settlement_basis") or "").upper()
    v["settlement_verified"] = settle in {
        "FULL_GAME_STATS", "OFFICIAL_BOX_SCORE", "VERIFIED",
    }
    if not v["settlement_verified"]:
        msg = settle if settle else "FIELD_ABSENT"
        blockers.append(f"SETTLEMENT_BASIS_UNVERIFIED:{msg}")

    # Final refresh required: status must be < 2 h stale
    freshness = enrichment.get("status_freshness_hours")
    final_refresh = False
    if freshness is None:
        final_refresh = True
        blockers.append("FINAL_REFRESH_REQUIRED:status_freshness_unknown")
    else:
        try:
            h = float(freshness)
            if h > 2.0:
                final_refresh = True
                blockers.append(
                    f"FINAL_REFRESH_REQUIRED:status_age={h:.1f}h>2h_threshold"
                )
        except (TypeError, ValueError):
            final_refresh = True
            blockers.append("FINAL_REFRESH_REQUIRED:status_freshness_unparseable")

    return v, blockers, final_refresh


# ---------------------------------------------------------------------------
# Stage 13: Final label (65% floor)
# ---------------------------------------------------------------------------

def _final_label(
    cal_lb: float,
    blockers: list[str],
    verifications: dict[str, bool],
    final_refresh_required: bool,
) -> str:
    """
    Assign the final probability label.

    YES_MODEL_QUALIFIED requires ALL of:
      - calibrated lower bound >= 65%
      - settlement_verified
      - final_refresh_required is False
      - no PLAYER_STATUS_OUT blocker

    A 53% calibrated lower bound → HOLD, not YES_MODEL_QUALIFIED.
    """
    is_out = any("PLAYER_STATUS_OUT" in b for b in blockers)
    if is_out:
        return "REJECT"

    # Refresh-required caps the label at HOLD
    if final_refresh_required:
        if cal_lb >= _HOLD_FLOOR:
            return "HOLD"
        elif cal_lb >= _WATCH_FLOOR:
            return "WATCH"
        return "REJECT"

    if cal_lb >= _YES_QUALIFIED_FLOOR:
        if not verifications.get("settlement_verified"):
            return "HOLD"
        return "YES_MODEL_QUALIFIED"
    elif cal_lb >= _HOLD_FLOOR:
        return "HOLD"
    elif cal_lb >= _WATCH_FLOOR:
        return "WATCH"
    return "REJECT"


# ---------------------------------------------------------------------------
# Opportunity projection helper
# ---------------------------------------------------------------------------

def _opportunity_projection(
    rates: dict[str, Any],
    regimes: list[RoleRegime],
    stat_key: str,
) -> dict[str, float]:
    """
    Project expected opportunities for the stat based on regime mixture and rates.
    """
    exp_min  = sum(r.prior * r.minutes_mean for r in regimes)
    exp_poss = exp_min * _WNBA_POSSESSIONS_PER_MIN

    proj: dict[str, float] = {
        "expected_minutes":     exp_min,
        "expected_possessions": exp_poss,
    }

    components = _STAT_COMPONENTS.get(stat_key, [])

    if "pts" in components:
        pts_pm = rates.get("pts") or 0.0
        three_pm = rates.get("threepm") or 0.0
        proj["expected_pts"]              = pts_pm * exp_min
        proj["expected_fga"]              = pts_pm * exp_min / max(rates.get("fg_pct", 0.45) * 2, 0.01)
        proj["expected_fta"]              = pts_pm * exp_min * 0.25
        proj["expected_3pa"]              = (rates.get("threepma") or 0.0) * exp_min
        proj["pts_from_3_fraction"]       = min(1.0, 3.0 * three_pm / pts_pm) if pts_pm > 1e-9 else 0.0

    if "reb" in components:
        proj["expected_reb"]              = (rates.get("reb") or 0.0) * exp_min
        proj["expected_reb_opportunities"]= exp_poss * 0.22

    if "ast" in components:
        proj["expected_ast"]              = (rates.get("ast") or 0.0) * exp_min
        proj["expected_ast_opportunities"]= exp_poss * 0.35

    if "stl" in components:
        proj["expected_stl"] = (rates.get("stl") or 0.0) * exp_min
    if "blk" in components:
        proj["expected_blk"] = (rates.get("blk") or 0.0) * exp_min
    if "tov" in components:
        proj["expected_tov"] = (rates.get("tov") or 0.0) * exp_min
    if "threepm" in components:
        proj["expected_3pm"] = (rates.get("threepm") or 0.0) * exp_min

    return proj


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def score(
    row: dict[str, Any],
    enrichment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Run the WNBA generative probability model for one prop row.
    Always returns a complete output dict.  can_execute=False unconditional.

    The model is independent of recent game-log outcome frequencies as a
    primary driver.  L5/L10 data is diagnostic/calibration evidence only.
    Market data can contribute at most 25% weight to the final probability.
    The conservative lower bound is derived from the stress scenario, not
    a fixed haircut.
    """
    enr      = dict(enrichment or {})
    blockers: list[str] = []

    # ── Parse stat key ─────────────────────────────────────────────────────
    raw_stat = str(
        row.get("stat_key") or row.get("prop_type") or row.get("prop")
        or row.get("stat_type") or ""
    ).upper().strip().replace(" ", "_")
    stat_key = _STAT_KEY_ALIASES.get(raw_stat, raw_stat)

    if stat_key not in SUPPORTED_STAT_KEYS:
        return {
            "can_execute":  False,
            "model_status": "UNSUPPORTED_STAT_KEY",
            "stat_key":     stat_key,
            "blockers":     [f"WNBA_GENERATIVE:UNSUPPORTED_STAT_KEY:{stat_key}"],
            "final_label":  "REJECT",
        }

    # ── Parse line and side ────────────────────────────────────────────────
    line_raw = row.get("line") or row.get("threshold") or row.get("number")
    try:
        line = float(line_raw)   # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {
            "can_execute":  False,
            "model_status": "INVALID_LINE",
            "stat_key":     stat_key,
            "blockers":     ["WNBA_GENERATIVE:LINE_NOT_PARSEABLE"],
            "final_label":  "REJECT",
        }

    if line < 0:
        return {
            "can_execute":  False,
            "model_status": "INVALID_LINE",
            "stat_key":     stat_key,
            "blockers":     ["WNBA_GENERATIVE:NEGATIVE_LINE"],
            "final_label":  "REJECT",
        }

    is_integer = (line == int(line))
    side = str(row.get("side") or row.get("direction") or "MORE").upper()
    if side not in ("MORE", "LESS"):
        side = "MORE"

    # ── Stage 1: Verification ──────────────────────────────────────────────
    verifications, verify_blockers, final_refresh = _verify_inputs(row, enr)
    blockers.extend(verify_blockers)

    # ── Read opportunity-gate output (if ran in first loop) ────────────────
    opp_gate: dict[str, Any] = (row.get("gates") or {}).get("wnba_opportunity_gate") or {}

    # ── Stage 2: Role regimes ──────────────────────────────────────────────
    matchup_adj = float(enr.get("matchup_adj") or enr.get("opponent_def_rating") or 1.0)
    regimes     = _build_regimes(enr, opp_gate)

    # ── Stage 3: Per-minute rates ──────────────────────────────────────────
    rates = _derive_rates(enr, opp_gate, matchup_adj)

    components    = _STAT_COMPONENTS[stat_key]
    total_rate_pm = max(1e-9, sum(rates.get(c, 0.0) for c in components))

    exp_min    = sum(r.prior * r.minutes_mean for r in regimes)
    exp_stat   = total_rate_pm * exp_min
    opp_proj   = _opportunity_projection(rates, regimes, stat_key)

    # ── Stage 4: PMF ───────────────────────────────────────────────────────
    pmf = _compute_full_pmf(regimes, total_rate_pm)

    # ── Stage 5: Three-outcome ─────────────────────────────────────────────
    raw_more, raw_exact, raw_less = _pmf_to_outcomes(pmf, line, is_integer)
    raw_selected = raw_more if side == "MORE" else raw_less

    # ── Stage 6: Dependencies ──────────────────────────────────────────────
    deps = _compute_dependencies(
        regimes, rates, stat_key, total_rate_pm,
        line, is_integer, pmf, side,
    )

    # ── Stage 7: Failure path ──────────────────────────────────────────────
    failure_regime, failure_prob = _failure_path_audit(
        regimes, total_rate_pm, line, is_integer, side
    )

    # ── Stage 8: Stress test ───────────────────────────────────────────────
    s_pmf               = _stress_pmf(regimes, total_rate_pm, side)
    s_more, s_exact, s_less = _pmf_to_outcomes(s_pmf, line, is_integer)
    stress_selected     = s_more if side == "MORE" else s_less
    stress_drop         = max(0.0, raw_selected - stress_selected)

    # ── Stage 9: Calibration ───────────────────────────────────────────────
    discount, disc_factors = _uncertainty_discount(enr, regimes, rates)
    cal_more, cal_exact, cal_less = _calibrate_triple(
        raw_more, raw_exact, raw_less, discount, is_integer
    )
    cal_selected = cal_more if side == "MORE" else cal_less

    # Conservative lower bound = calibrated probability in the stress scenario
    # (slightly higher discount for stress to reflect additional uncertainty)
    s_cal_more, _se, s_cal_less = _calibrate_triple(
        s_more, s_exact, s_less, min(discount + 0.05, 0.60), is_integer
    )
    cal_lower_bound = s_cal_more if side == "MORE" else s_cal_less

    # Market blend
    mkt       = _market_sanity(enr, line, is_integer, raw_more)
    mkt_nv    = mkt["market_no_vig_prob"]
    cal_sel_blended, mkt_wt, mdl_wt = _blend_market(cal_selected, mkt_nv, discount)

    # Invariant: lower_bound must always be <= the final blended calibrated probability.
    # Market blending can pull cal_sel_blended below the pre-blend cal_lower_bound
    # (computed from the stress PMF before blending), producing an inverted interval
    # that prob_ledger correctly marks rank_eligible=False.  Clamp here so the
    # schema invariant holds at the model boundary regardless of blend direction.
    if cal_lower_bound > cal_sel_blended:
        cal_lower_bound = cal_sel_blended

    # Optimistic-scenario upper bound — a real model quantity, symmetric to the
    # stress lower bound: the calibrated triple recomputed with the uncertainty
    # discount relaxed by the same 0.05 step used to tighten the stress bound.
    # Clamped so lower_bound <= calibrated <= upper_bound always holds at the
    # model boundary.  This is emitted BY the model (never synthesized
    # downstream by an adapter).
    o_cal_more, _oe, o_cal_less = _calibrate_triple(
        raw_more, raw_exact, raw_less, max(discount - 0.05, 0.0), is_integer
    )
    cal_upper_bound = o_cal_more if side == "MORE" else o_cal_less
    if cal_upper_bound < cal_sel_blended:
        cal_upper_bound = cal_sel_blended
    cal_upper_bound = min(cal_upper_bound, 0.999999)

    # ── Stage 10: L5/L10 diagnostic ───────────────────────────────────────
    l10_diag  = _l5_l10_diagnostic(enr, line, is_integer, stat_key)
    l10_rate  = l10_diag.get("l10_more_rate")
    div_note  = ""
    if l10_rate is not None and abs(cal_sel_blended - l10_rate) > 0.15:
        div_note = (
            f"GENERATIVE_MODEL_DIVERGES_FROM_L10:"
            f"model={cal_sel_blended:.3f}_l10={l10_rate:.3f}"
        )

    # ── Stage 13: Label (65% floor) ───────────────────────────────────────
    final_lbl = _final_label(
        cal_lower_bound, blockers, verifications, final_refresh
    )

    dominant_regime = max(regimes, key=lambda r: r.prior).name

    return {
        # governance
        "can_execute":    False,
        "model_status":   "PROVISIONAL",

        # inputs
        "stat_key":        stat_key,
        "line":            line,
        "side":            side,
        "is_integer_line": is_integer,

        # role regimes
        "role_regimes":    [r.to_dict() for r in regimes],
        "dominant_regime": dominant_regime,

        # opportunity projection
        "expected_minutes":       round(exp_min, 2),
        "expected_possessions":   round(opp_proj.get("expected_possessions", 0.0), 2),
        "opportunity_projection": {k: round(v, 4) for k, v in opp_proj.items()},
        "stat_projection":        round(exp_stat, 3),

        # raw More/Exact/Less — full float precision (no 6dp rounding) to preserve simplex
        "raw_more":     float(raw_more),
        "raw_exact":    float(raw_exact),
        "raw_less":     float(raw_less),
        "raw_selected": float(raw_selected),

        # calibrated More/Exact/Less — full float precision
        "cal_more":          float(cal_more),
        "cal_exact":         float(cal_exact),
        "cal_less":          float(cal_less),
        "cal_selected":      float(cal_sel_blended),
        "cal_lower_bound":   float(cal_lower_bound),
        "cal_upper_bound":   float(cal_upper_bound),

        # Stage-2 provenance — genuine model emissions (adapters copy these;
        # they never synthesize them)
        "model_timestamp":    datetime.now(timezone.utc).isoformat(),
        "calibration_method": CALIBRATION_METHOD,

        # L5/L10 (diagnostic only — do not drive generative probability)
        "l5_stat_mean":        l10_diag.get("l5_stat_mean"),
        "l10_stat_mean":       l10_diag.get("l10_stat_mean"),
        "l5_more_rate":        l10_diag.get("l5_more_rate"),
        "l10_more_rate":       l10_diag.get("l10_more_rate"),
        "exact_line_l5":       l10_diag.get("exact_line_l5"),
        "exact_line_l10":      l10_diag.get("exact_line_l10"),
        "l_history_note":      l10_diag.get("l_history_note", ""),
        "l10_divergence_note": div_note,

        # dependency measures (all in [0, 1])
        "minutes_dependency":          float(deps["minutes_dependency"]),
        "efficiency_dependency":       float(deps["efficiency_dependency"]),
        "close_game_dependency":       float(deps["close_game_dependency"]),
        "teammate_absence_dependency": float(deps["teammate_absence_dependency"]),
        "overtime_dependency":         float(deps["overtime_dependency"]),
        "three_pa_dependency":         float(deps["three_pa_dependency"]),
        "dominant_dependency_share":   float(deps["dominant_dependency_share"]),
        "dominant_dependency_name":    deps["dominant_dependency_name"],

        # failure path (unconditional integration)
        "largest_failure_path": failure_regime,
        "failure_path_prob":    round(failure_prob, 4),

        # stress test
        "stress_selected_prob": float(stress_selected),
        "stress_drop":          round(stress_drop, 4),

        # market sanity (informational — does not silently dominate)
        "market_no_vig_prob":        mkt["market_no_vig_prob"],
        "model_market_delta":        mkt["model_market_delta"],
        "exact_line_market_no_vig":  mkt["exact_line_market_no_vig"],
        "market_model_delta_large":  mkt["market_model_delta_large"],

        # calibration transparency
        "market_prior_weight":      round(mkt_wt, 4),
        "independent_model_weight": round(mdl_wt, 4),
        "uncertainty_discount":     round(discount, 4),
        "uncertainty_factors":      disc_factors,

        # verification
        **verifications,
        "final_refresh_required": final_refresh,

        # final label and blockers
        "final_label": final_lbl,
        "blockers":    blockers,
    }
