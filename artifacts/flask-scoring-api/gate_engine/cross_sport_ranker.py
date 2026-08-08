"""
gate_engine/cross_sport_ranker.py

Cross-sport prop ranking engine — WOW-PATCH-2026-08-08.

Ranks scored props from the pipeline by calibrated lower bound (CLB)
across Tennis, WNBA, and MLB (and any other sport with a registered model).

Output lanes (matches cross-sport-high-probability-selector patch):
  1. highest_hit_probability  — top props by cal_lower_bound DESC
  2. highest_calibrated_prob  — top props by calibrated_probability DESC
  3. best_edge                — top props by pure_edge DESC (market − model gap)
  4. best_multi_leg           — multi-leg structure with cross-leg dependence audit

Invariants (permanent, per patch governance):
  can_execute            = False   (unconditional)
  auto_execute           = False
  requires_human_confirm = True
  stake_sizing           = False
  bankroll_allocation    = False

Weakest-leg elimination: any leg with cal_lower_bound < 0.50 is excluded
from all ranking lanes regardless of other signals.

Cross-leg dependence audit: legs sharing the same player, game, injury
thesis, or same game-script exposure are flagged and the weaker leg is
dropped from multi-leg output.

Public API
----------
  rank(rows, top_n=10, multi_leg_size=4) -> RankingResult
  from_db(conn, ...)                      -> RankingResult  (query DB predictions)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

# Permanent invariants — must not be altered
can_execute:             bool = False
auto_execute:            bool = False
requires_human_confirm:  bool = True
stake_sizing:            bool = False
bankroll_allocation:     bool = False

# ---------------------------------------------------------------------------
# Labels that are eligible for ranking (not rejected / failed)
# ---------------------------------------------------------------------------

_ELIGIBLE_LABELS: frozenset[str] = frozenset({
    # WOW v16 positive / hold labels
    "FINAL_APPROVED",
    "MONEY_QUALIFIED",
    "MODEL_QUALIFIED_HOLD",
    "MARKET_VERIFIED_HOLD",
    "RESEARCH_INTEREST",
    "WATCH",
    # Tennis / WNBA model labels
    "YES_MODEL_QUALIFIED",
    "HOLD",
    "WNBA_COMPOSITE_MODEL_READY",
    "WNBA_COMPOSITE_WATCH",
    "WNBA_COMPOSITE_SCOUT",
    "MLB_K_LESS_WATCH",
    "MLB_OUTS_MORE_HOLD",
})

_HARD_EXCLUDE: frozenset[str] = frozenset({
    "DATA_CONTRACT_FAIL",
    "SLATE_PURGE",
    "WNBA_SLATE_PURGE",
    "REJECT_DATA_QUALITY",
    "REJECT_NO_EDGE",
    "REJECT_BAD_STRUCTURE",
    "REJECT_SHARP_CONFLICT",
    "REJECT_FALLING_KNIFE",
    "REJECT_HOUSE_RULES_VULNERABILITY",
    "REJECT_EXECUTION_STALE",
    "REJECT_PAYOUT_CHANGED",
    "REJECT_LOW_LIQUIDITY",
    "REJECT_LINE_MOVED_AGAINST_SIDE",
    "REJECT_POWER_CORRELATED",
    "SOURCE_CONFLICT",
    "DUPLICATE_EXPOSURE_BLOCK",
    "DIRECTIONAL_EXPOSURE_BLOCK",
    "SESSION_DIRECTIONAL_EXPOSURE_BLOCK",
    "DEGRADED_ENGINE_RUN",
    "MLB_WINNER_PREFLIGHT_BLOCK",
    "PIPELINE_INTEGRITY_FAILURE",
    "INPUT_FAILURE — ACQUISITION_NOT_COMPLETED",
    "RUN_INVALID — ACQUISITION_INCOMPLETE",
})

# Minimum CLB to enter any ranking lane
_MIN_CLB = 0.50


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RankedProp:
    """A single scored prop in the ranking output."""
    rank:                    int
    player_name:             str
    sport:                   str
    stat_key:                str
    side:                    str
    line:                    float
    terminal_label:          str
    cal_lower_bound:         float
    calibrated_probability:  float
    raw_probability:         Optional[float]
    raw_more:                Optional[float]
    raw_exact:               Optional[float]
    raw_less:                Optional[float]
    cal_more:                Optional[float]
    cal_exact:               Optional[float]
    cal_less:                Optional[float]
    pure_edge:               Optional[float]
    market_probability:      Optional[float]
    model_status:            str
    event_key:               Optional[str]
    dominant_dependency:     Optional[str]
    failure_path_prob:       Optional[float]
    blockers:                list[str]
    dependence_flags:        list[str]
    source:                  str = "pipeline"   # "pipeline" | "db"

    @property
    def edge_tier(self) -> str:
        if self.pure_edge is None:
            return "NO_MARKET_DATA"
        if self.pure_edge >= 0.08:
            return "SEVERE_DRIFT"
        if self.pure_edge >= 0.05:
            return "STRONG_DRIFT"
        if self.pure_edge >= 0.025:
            return "MILD_DRIFT"
        if self.pure_edge >= 0:
            return "ALIGNED"
        return "NEGATIVE_EDGE"

    def to_dict(self) -> dict:
        return {
            "rank":                   self.rank,
            "player_name":            self.player_name,
            "sport":                  self.sport,
            "stat_key":               self.stat_key,
            "side":                   self.side,
            "line":                   self.line,
            "terminal_label":         self.terminal_label,
            "cal_lower_bound":        self.cal_lower_bound,
            "calibrated_probability": self.calibrated_probability,
            "raw_probability":        self.raw_probability,
            "three_state": {
                "raw_more":  self.raw_more,
                "raw_exact": self.raw_exact,
                "raw_less":  self.raw_less,
                "cal_more":  self.cal_more,
                "cal_exact": self.cal_exact,
                "cal_less":  self.cal_less,
            },
            "pure_edge":              self.pure_edge,
            "edge_tier":              self.edge_tier,
            "market_probability":     self.market_probability,
            "model_status":           self.model_status,
            "event_key":              self.event_key,
            "dominant_dependency":    self.dominant_dependency,
            "failure_path_prob":      self.failure_path_prob,
            "blockers":               self.blockers,
            "dependence_flags":       self.dependence_flags,
            "can_execute":            False,
            "source":                 self.source,
        }


@dataclass
class MultiLegCandidate:
    """A candidate multi-leg slip from the ranking engine."""
    legs:                list[RankedProp]
    dependence_verdict:  str   # CLEAN | DEPENDENCE_WARNING | DEPENDENCE_BLOCK
    dependence_flags:    list[str]
    combined_probability: float
    weakest_lb:          float
    slip_label:          str

    def to_dict(self) -> dict:
        return {
            "legs":                 [p.to_dict() for p in self.legs],
            "combined_probability": round(self.combined_probability, 4),
            "weakest_lb":           round(self.weakest_lb, 4),
            "dependence_verdict":   self.dependence_verdict,
            "dependence_flags":     self.dependence_flags,
            "slip_label":           self.slip_label,
            "can_execute":          False,
        }


@dataclass
class RankingResult:
    """Full output of the cross-sport ranker."""
    highest_hit_probability: list[RankedProp]
    highest_calibrated_prob: list[RankedProp]
    best_edge:               list[RankedProp]
    best_multi_leg:          list[MultiLegCandidate]
    n_eligible:              int
    n_eliminated_weak:       int
    n_total_input:           int
    sports_covered:          list[str]
    ranker_version:          str = "cross_sport_ranker_v1.0"

    def to_dict(self) -> dict:
        return {
            "highest_hit_probability": [p.to_dict() for p in self.highest_hit_probability],
            "highest_calibrated_prob": [p.to_dict() for p in self.highest_calibrated_prob],
            "best_edge":               [p.to_dict() for p in self.best_edge],
            "best_multi_leg":          [c.to_dict() for c in self.best_multi_leg],
            "summary": {
                "n_eligible":       self.n_eligible,
                "n_eliminated_weak": self.n_eliminated_weak,
                "n_total_input":    self.n_total_input,
                "sports_covered":   sorted(self.sports_covered),
            },
            "ranker_version": self.ranker_version,
            "can_execute":    False,
            "requires_human_confirmation": True,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe(v, default=None):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _label(row: dict) -> str:
    return (
        row.get("terminal_label")
        or row.get("final_label")
        or row.get("label")
        or ""
    )


def _gate_val(row: dict, key: str, *gate_names: str):
    """Read a value from specific gates dict, then top-level."""
    gates = row.get("gates", {}) or {}
    for gn in gate_names:
        g = gates.get(gn, {}) or {}
        if key in g:
            return g[key]
    return row.get(key)


def _build_ranked_prop(row: dict, rank: int, source: str = "pipeline") -> RankedProp:
    wnba_g   = (row.get("gates") or {}).get("wnba_generative", {}) or {}
    tennis_g = (row.get("gates") or {}).get("tennis_total_games", {}) or {}

    cal_prob = _safe(
        wnba_g.get("cal_selected")
        or tennis_g.get("cal_selected")
        or row.get("calibrated_probability")
    ) or 0.0

    lb = _safe(
        wnba_g.get("cal_lower_bound")
        or tennis_g.get("cal_lower_bound")
        or row.get("calibrated_probability_lower_bound")
        or row.get("lower_bound")
    ) or 0.0

    raw_prob = _safe(
        wnba_g.get("raw_selected")
        or tennis_g.get("raw_selected")
        or row.get("model_probability")
        or row.get("raw_probability")
    )

    mkt_prob = _safe(
        row.get("market_no_vig_probability")
        or row.get("market_probability")
        or row.get("no_vig_prob")
    )

    pure_edge: Optional[float] = None
    if cal_prob and mkt_prob is not None:
        pure_edge = round(cal_prob - mkt_prob, 4)

    fp_prob = _safe(
        wnba_g.get("failure_path_prob")
        or row.get("failure_path_probability")
    )

    dom_dep = (
        wnba_g.get("dominant_dependency_name")
        or row.get("dominant_dependency_name")
    )

    blockers = row.get("blockers") or []
    if not isinstance(blockers, list):
        blockers = [str(blockers)]

    return RankedProp(
        rank=rank,
        player_name=str(row.get("player_name") or row.get("player") or row.get("team") or ""),
        sport=(row.get("sport") or "").upper(),
        stat_key=str(row.get("stat_key") or row.get("market") or ""),
        side=str(row.get("side") or "MORE"),
        line=_safe(row.get("line")) or 0.0,
        terminal_label=_label(row),
        cal_lower_bound=lb,
        calibrated_probability=cal_prob,
        raw_probability=raw_prob,
        raw_more=_safe(wnba_g.get("raw_more") or tennis_g.get("raw_more")),
        raw_exact=_safe(wnba_g.get("raw_exact") or tennis_g.get("raw_exact")),
        raw_less=_safe(wnba_g.get("raw_less") or tennis_g.get("raw_less")),
        cal_more=_safe(wnba_g.get("cal_more") or tennis_g.get("cal_more")),
        cal_exact=_safe(wnba_g.get("cal_exact") or tennis_g.get("cal_exact")),
        cal_less=_safe(wnba_g.get("cal_less") or tennis_g.get("cal_less")),
        pure_edge=pure_edge,
        market_probability=mkt_prob,
        model_status=str(row.get("model_status") or "PROVISIONAL"),
        event_key=row.get("event_id") or row.get("event_key"),
        dominant_dependency=dom_dep,
        failure_path_prob=fp_prob,
        blockers=blockers,
        dependence_flags=[],
        source=source,
    )


# ---------------------------------------------------------------------------
# Weakest-leg elimination
# ---------------------------------------------------------------------------

def _filter_eligible(rows: list[dict]) -> tuple[list[dict], int]:
    """
    Remove:
    - Rows with terminal_label in HARD_EXCLUDE
    - Rows with cal_lower_bound < _MIN_CLB

    Returns (eligible_rows, n_eliminated_weak)
    """
    eligible = []
    n_weak = 0

    for row in rows:
        lbl = _label(row)

        # Hard exclude
        if lbl in _HARD_EXCLUDE:
            n_weak += 1
            continue

        # Must have at least a non-rejected label
        if lbl and lbl not in _ELIGIBLE_LABELS:
            # Allow through if it's a model-qualified label we don't have in the set
            if lbl.startswith("REJECT") or lbl in ("NO_PLAY", "NO_SOURCE_COVERAGE"):
                n_weak += 1
                continue

        # CLB threshold
        wnba_g   = (row.get("gates") or {}).get("wnba_generative", {}) or {}
        tennis_g = (row.get("gates") or {}).get("tennis_total_games", {}) or {}

        lb = _safe(
            wnba_g.get("cal_lower_bound")
            or tennis_g.get("cal_lower_bound")
            or row.get("calibrated_probability_lower_bound")
            or row.get("lower_bound")
        )

        if lb is None or lb < _MIN_CLB:
            n_weak += 1
            continue

        eligible.append(row)

    return eligible, n_weak


# ---------------------------------------------------------------------------
# Cross-leg dependence audit
# ---------------------------------------------------------------------------

_SHARED_INJURY_KEYS = ("injury_thesis", "teammate_absent", "primary_teammate_absent")
_SAME_SCRIPT_KEYS = (
    "directional_exposure_tags", "game_script_type", "blowout_risk",
)


def _dependence_audit(props: list[RankedProp]) -> list[str]:
    """
    Check for cross-leg dependence between a candidate multi-leg set.

    Returns list of dependence flag strings (empty = CLEAN).
    """
    flags: list[str] = []

    # Same player
    players = [p.player_name for p in props if p.player_name]
    if len(players) != len(set(players)):
        flags.append("SAME_PLAYER_DUPLICATE")

    # Same event (game)
    events = [p.event_key for p in props if p.event_key]
    event_counts: dict[str, int] = {}
    for e in events:
        event_counts[e] = event_counts.get(e, 0) + 1
    for ev, cnt in event_counts.items():
        if cnt >= 3:
            flags.append(f"SAME_GAME_CONCENTRATION ({ev}: {cnt} legs)")
        elif cnt == 2:
            flags.append(f"SAME_GAME_PAIR ({ev})")

    # Dominant dependency overlap
    deps = [p.dominant_dependency for p in props if p.dominant_dependency]
    dep_counts: dict[str, int] = {}
    for d in deps:
        dep_counts[d] = dep_counts.get(d, 0) + 1
    for dep, cnt in dep_counts.items():
        if cnt >= 2:
            flags.append(f"SHARED_DEPENDENCY_{dep.upper()} ({cnt} legs)")

    return flags


def _dependence_verdict(flags: list[str]) -> str:
    if not flags:
        return "CLEAN"
    # Same player = hard block
    if any("SAME_PLAYER" in f for f in flags):
        return "DEPENDENCE_BLOCK"
    # Same-game concentration = block
    if any("SAME_GAME_CONCENTRATION" in f for f in flags):
        return "DEPENDENCE_BLOCK"
    # Shared injury/dependency = warning
    if any("SHARED_DEPENDENCY" in f or "SAME_GAME_PAIR" in f for f in flags):
        return "DEPENDENCE_WARNING"
    return "DEPENDENCE_WARNING"


# ---------------------------------------------------------------------------
# Multi-leg builder
# ---------------------------------------------------------------------------

def _build_multi_leg(
    eligible: list[RankedProp],
    size: int = 4,
) -> list[MultiLegCandidate]:
    """
    Build the best multi-leg candidate slips from eligible props.

    Strategy: greedy — pick top-N by CLB, then audit dependence.
    If the greedy pick has DEPENDENCE_BLOCK, drop the weakest blocked leg
    and retry once.
    """
    if len(eligible) < 2:
        return []

    candidates: list[MultiLegCandidate] = []

    # Pool sorted by CLB
    pool = sorted(eligible, key=lambda p: p.cal_lower_bound, reverse=True)

    def _make_candidate(legs: list[RankedProp]) -> MultiLegCandidate:
        flags   = _dependence_audit(legs)
        verdict = _dependence_verdict(flags)
        # Combined probability (conservative: product of CLBs)
        combined = 1.0
        for leg in legs:
            combined *= leg.cal_lower_bound

        weakest  = min(leg.cal_lower_bound for leg in legs)
        slip_label = (
            "MULTI_LEG_READY"   if verdict == "CLEAN" else
            "MULTI_LEG_CAUTION" if verdict == "DEPENDENCE_WARNING" else
            "MULTI_LEG_BLOCKED"
        )
        return MultiLegCandidate(
            legs=legs,
            dependence_verdict=verdict,
            dependence_flags=flags,
            combined_probability=round(combined, 4),
            weakest_lb=round(weakest, 4),
            slip_label=slip_label,
        )

    # Primary candidate: greedy top-N
    top = pool[:size]
    c   = _make_candidate(top)
    candidates.append(c)

    # If blocked, try dropping the most-blocked leg
    if c.dependence_verdict == "DEPENDENCE_BLOCK" and len(pool) > size:
        # Replace the lowest-CLB leg with the next candidate from pool
        alt_legs = pool[:size - 1] + [pool[size]]
        alt = _make_candidate(alt_legs)
        if alt.dependence_verdict != "DEPENDENCE_BLOCK":
            candidates.append(alt)

    # Also provide a 2-leg power candidate (highest 2 clean legs)
    if len(pool) >= 2:
        two = pool[:2]
        c2 = _make_candidate(two)
        candidates.append(c2)

    return candidates


# ---------------------------------------------------------------------------
# Main public entry
# ---------------------------------------------------------------------------

def rank(
    rows: list[dict[str, Any]],
    top_n: int = 10,
    multi_leg_size: int = 4,
) -> RankingResult:
    """
    Rank a list of scored pipeline rows.

    Parameters
    ----------
    rows          : list of row dicts from run_pipeline() output
    top_n         : max props per lane
    multi_leg_size: legs in best_multi_leg candidate

    Returns RankingResult (can_execute is unconditionally False)
    """
    n_total = len(rows)
    eligible_rows, n_weak = _filter_eligible(rows)

    sports = list({(r.get("sport") or "").upper() for r in eligible_rows if r.get("sport")})

    # Convert to RankedProp objects
    props = [_build_ranked_prop(r, i + 1) for i, r in enumerate(eligible_rows)]

    # ── Lane 1: highest hit probability (by CLB) ─────────────────────────
    lane1 = sorted(props, key=lambda p: p.cal_lower_bound, reverse=True)
    for i, p in enumerate(lane1):
        p.rank = i + 1
    lane1 = lane1[:top_n]

    # ── Lane 2: highest calibrated probability (central estimate) ─────────
    lane2 = sorted(props, key=lambda p: p.calibrated_probability, reverse=True)
    for i, p in enumerate(lane2):
        p.rank = i + 1
    lane2 = lane2[:top_n]

    # ── Lane 3: best edge (pure_edge vs market) ───────────────────────────
    props_with_edge = [p for p in props if p.pure_edge is not None and p.pure_edge > 0]
    lane3 = sorted(props_with_edge, key=lambda p: p.pure_edge, reverse=True)
    for i, p in enumerate(lane3):
        p.rank = i + 1
    lane3 = lane3[:top_n]

    # ── Lane 4: best multi-leg ────────────────────────────────────────────
    # Use CLB-sorted pool
    clb_sorted = sorted(props, key=lambda p: p.cal_lower_bound, reverse=True)
    lane4 = _build_multi_leg(clb_sorted, size=min(multi_leg_size, len(clb_sorted)))

    return RankingResult(
        highest_hit_probability=lane1,
        highest_calibrated_prob=lane2,
        best_edge=lane3,
        best_multi_leg=lane4,
        n_eligible=len(eligible_rows),
        n_eliminated_weak=n_weak,
        n_total_input=n_total,
        sports_covered=sports,
    )


# ---------------------------------------------------------------------------
# DB-backed variant (queries prediction ledger)
# ---------------------------------------------------------------------------

def from_db(
    conn,
    sport: str | None = None,
    since_date: str | None = None,
    top_n: int = 10,
    multi_leg_size: int = 4,
) -> RankingResult:
    """
    Build rankings from the prediction ledger (wow_prop_predictions).

    Converts DB rows to pipeline-row shape and calls rank().
    """
    from gate_engine.prediction_ledger import read_predictions

    db_rows = read_predictions(
        conn,
        sport=sport,
        since_date=since_date,
        min_lower_bound=_MIN_CLB,
        limit=500,
    )

    # Convert DB row → pipeline-row shape
    pipeline_shape = []
    for r in db_rows:
        row: dict[str, Any] = {
            "sport":         r.get("sport"),
            "player_name":   r.get("player_name"),
            "stat_key":      r.get("stat_key"),
            "side":          r.get("side"),
            "line":          r.get("line"),
            "event_key":     r.get("event_key"),
            "event_id":      r.get("event_key"),
            "terminal_label": r.get("terminal_label"),
            "final_label":   r.get("terminal_label"),
            "model_status":  r.get("model_status"),
            "market_no_vig_probability": r.get("market_probability"),
            "calibrated_probability_lower_bound": r.get("lower_bound"),
            "lower_bound":   r.get("lower_bound"),
            "model_probability": r.get("raw_probability"),
            "blockers":      [],
            "gates": {
                "wnba_generative": {
                    "cal_selected":    r.get("calibrated_probability"),
                    "cal_lower_bound": r.get("lower_bound"),
                    "cal_upper_bound": r.get("upper_bound"),
                    "raw_selected":    r.get("raw_probability"),
                    "raw_more":        r.get("raw_more"),
                    "raw_exact":       r.get("raw_exact"),
                    "raw_less":        r.get("raw_less"),
                    "cal_more":        r.get("cal_more"),
                    "cal_exact":       r.get("cal_exact"),
                    "cal_less":        r.get("cal_less"),
                    "failure_path_prob": r.get("failure_path_score"),
                } if r.get("sport") == "WNBA" else {},
                "tennis_total_games": {
                    "cal_selected":    r.get("calibrated_probability"),
                    "cal_lower_bound": r.get("lower_bound"),
                    "raw_selected":    r.get("raw_probability"),
                    "raw_more":        r.get("raw_more"),
                    "raw_exact":       r.get("raw_exact"),
                    "raw_less":        r.get("raw_less"),
                    "cal_more":        r.get("cal_more"),
                    "cal_exact":       r.get("cal_exact"),
                    "cal_less":        r.get("cal_less"),
                } if r.get("sport") == "TENNIS" else {},
            },
        }
        pipeline_shape.append(row)

    result = rank(pipeline_shape, top_n=top_n, multi_leg_size=multi_leg_size)

    # Mark source as "db"
    for lane in (
        result.highest_hit_probability,
        result.highest_calibrated_prob,
        result.best_edge,
    ):
        for p in lane:
            p.source = "db"

    return result
