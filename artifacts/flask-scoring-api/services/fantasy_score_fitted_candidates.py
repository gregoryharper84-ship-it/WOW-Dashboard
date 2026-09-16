"""V17 fitted-candidate parity for non-NFL Fantasy Score props.

NFL already owns its dedicated ``nfl_dfs_fitted_simulator`` candidate.  This
module brings the other Fantasy Score sports already implemented by the engine
(NBA, WNBA, MLB hitter, MLB pitcher) to the same lifecycle stage without
claiming production authority.

Candidate-stage invariants match NFL:
- whole-event chronological train/calibration/untouched-test split;
- player/cohort fitted baselines from strictly prior events;
- joint residual-vector bootstrap to preserve within-player component dependence;
- exact externally supplied scoring-profile identity;
- >= 50,000 simulations for standard candidate evaluation;
- explicit failure-regime mixture support;
- calibrated probability/bounds remain null;
- probability_publishable=false, rank_eligible=false, can_execute=false.

This is deliberately candidate infrastructure.  Certification, promotion and
publication remain separate governed lifecycle steps.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import random
from statistics import mean, median, pstdev
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
MIN_STANDARD_SIMULATIONS = 50_000

NBA = "NBA"
WNBA = "WNBA"
MLB_HITTER = "MLB_HITTER"
MLB_PITCHER = "MLB_PITCHER"

SUPPORTED_LANES = frozenset({NBA, WNBA, MLB_HITTER, MLB_PITCHER})

CONTROLLING_SPECIALISTS = {
    NBA: "wow.nba-dfs-fantasy-score-expert",
    WNBA: "wow.wnba-dfs-fantasy-score-expert",
    MLB_HITTER: "wow.mlb-hitter-fantasy-score-expert",
    MLB_PITCHER: "wow.mlb-pitcher-fantasy-score-expert",
}

COMPONENTS = {
    NBA: ("points", "rebounds", "assists", "steals", "blocks", "turnovers"),
    WNBA: ("points", "rebounds", "assists", "steals", "blocks", "turnovers"),
    MLB_HITTER: (
        "singles", "doubles", "triples", "home_runs", "runs", "rbi",
        "walks", "hbp", "stolen_bases",
    ),
    MLB_PITCHER: ("wins", "quality_starts", "strikeouts", "outs_recorded", "earned_runs"),
}

COUNT_COMPONENTS = {lane: frozenset(parts) for lane, parts in COMPONENTS.items()}

FULL_CONTEXT_KEYS = {
    NBA: ("minutes_model", "pace_model", "usage_model"),
    WNBA: ("minutes_model", "pace_model", "usage_model"),
    MLB_HITTER: ("plate_appearance_model", "lineup_role_model", "opponent_pitching_model"),
    MLB_PITCHER: ("workload_model", "opponent_model", "bullpen_hook_model"),
}

FAILURE_REGIMES = {
    NBA: {
        "NORMAL_ROLE", "LIMITED_MINUTES", "FOUL_TROUBLE", "BLOWOUT_REDUCTION",
        "USAGE_COMPRESSION", "INJURY_OR_EARLY_EXIT", "PACE_COLLAPSE",
    },
    WNBA: {
        "NORMAL_ROLE", "LIMITED_MINUTES", "FOUL_TROUBLE", "BLOWOUT_REDUCTION",
        "USAGE_COMPRESSION", "INJURY_OR_EARLY_EXIT", "PACE_COLLAPSE",
    },
    MLB_HITTER: {
        "NORMAL_ROLE", "PLATE_APPEARANCE_SUPPRESSION", "LINEUP_DEMOTION",
        "PLATOON_SUPPRESSION", "PITCHING_MATCHUP_SUPPRESSION", "EARLY_EXIT",
    },
    MLB_PITCHER: {
        "NORMAL_ROLE", "INEFFICIENT_SURVIVING", "EARLY_HOOK", "COMMAND_COLLAPSE",
        "WORKLOAD_RESTRICTION", "ENVIRONMENTAL_DISRUPTION", "OPPONENT_EXTENSION",
        "INJURY_OR_EARLY_EXIT",
    },
}


class FantasyScoreCandidateError(ValueError):
    """Deterministic fitted-candidate validation failure."""


@dataclass(frozen=True)
class ChronologicalSplit:
    train: tuple[Mapping[str, Any], ...]
    calibration: tuple[Mapping[str, Any], ...]
    test: tuple[Mapping[str, Any], ...]
    train_end: str
    calibration_end: str
    test_end: str


@dataclass(frozen=True)
class CandidateArtifact:
    lane: str
    model_version: str
    source_id: str
    source_sha256: str
    generated_at: str
    rows_total: int
    split: ChronologicalSplit
    player_means: Mapping[str, Mapping[str, float]]
    cohort_means: Mapping[str, Mapping[str, float]]
    residual_vectors: Mapping[str, tuple[Mapping[str, float], ...]]
    calibration_status: str = "BLOCKED_NO_CERTIFIED_EXACT_LINE_CALIBRATION_ARTIFACT"
    certification_status: str = "CANDIDATE_ONLY"
    probability_publishable: bool = False
    rank_eligible: bool = False
    can_execute: bool = CAN_EXECUTE

    @property
    def controlling_specialist(self) -> str:
        return CONTROLLING_SPECIALISTS[self.lane]

    def as_metadata(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "model_version": self.model_version,
            "source_id": self.source_id,
            "source_sha256": self.source_sha256,
            "generated_at": self.generated_at,
            "rows_total": self.rows_total,
            "split": {
                "train_rows": len(self.split.train),
                "calibration_rows": len(self.split.calibration),
                "test_rows": len(self.split.test),
                "train_end": self.split.train_end,
                "calibration_end": self.split.calibration_end,
                "test_end": self.split.test_end,
            },
            "calibration_status": self.calibration_status,
            "certification_status": self.certification_status,
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
            "controlling_specialist": self.controlling_specialist,
        }


def _lane(value: str) -> str:
    lane = str(value or "").strip().upper()
    if lane not in SUPPORTED_LANES:
        raise FantasyScoreCandidateError(f"unsupported Fantasy Score lane: {lane or '<missing>'}")
    return lane


def _f(value: Any, *, field: str = "value") -> float:
    try:
        out = float(value or 0.0)
    except (TypeError, ValueError) as exc:
        raise FantasyScoreCandidateError(f"{field} must be numeric") from exc
    if not math.isfinite(out):
        raise FantasyScoreCandidateError(f"{field} must be finite")
    return out


def _date_text(row: Mapping[str, Any]) -> str:
    raw = str(row.get("event_date") or row.get("game_date") or "").strip()
    if not raw:
        raise FantasyScoreCandidateError("historical rows require event_date or game_date")
    return raw[:10]


def _event_key(row: Mapping[str, Any]) -> tuple[str, str]:
    event_id = str(row.get("event_id") or row.get("game_id") or "").strip()
    if not event_id:
        raise FantasyScoreCandidateError("historical rows require event_id or game_id")
    return (_date_text(row), event_id)


def _player_key(row: Mapping[str, Any]) -> str:
    value = str(row.get("player_id") or row.get("player") or row.get("player_name") or "").strip()
    if not value:
        raise FantasyScoreCandidateError("historical rows require player identity")
    return value


def _cohort_key(row: Mapping[str, Any], lane: str) -> str:
    if lane in {NBA, WNBA}:
        value = str(row.get("position") or row.get("role") or "ALL").strip().upper()
        return value or "ALL"
    return lane


def _source_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    payload = json.dumps(
        [dict(sorted((str(k), v) for k, v in row.items())) for row in rows],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def chronological_split(
    rows: Sequence[Mapping[str, Any]],
    *,
    train_fraction: float = 0.70,
    calibration_fraction: float = 0.15,
) -> ChronologicalSplit:
    if not (0.50 <= train_fraction < 0.90):
        raise FantasyScoreCandidateError("train_fraction must be in [0.50, 0.90)")
    if not (0.05 <= calibration_fraction < 0.30):
        raise FantasyScoreCandidateError("calibration_fraction must be in [0.05, 0.30)")
    if train_fraction + calibration_fraction >= 0.95:
        raise FantasyScoreCandidateError("leave at least 5% for untouched test")

    ordered = sorted((dict(row) for row in rows), key=_event_key)
    events = sorted({_event_key(row) for row in ordered})
    if len(events) < 7:
        raise FantasyScoreCandidateError("at least seven chronological events are required")

    train_n = max(1, int(len(events) * train_fraction))
    cal_n = max(1, int(len(events) * calibration_fraction))
    if train_n + cal_n >= len(events):
        cal_n = max(1, len(events) - train_n - 1)

    train_events = set(events[:train_n])
    calibration_events = set(events[train_n: train_n + cal_n])
    test_events = set(events[train_n + cal_n:])
    if not test_events:
        raise FantasyScoreCandidateError("chronological split produced no untouched test events")
    if train_events & calibration_events or train_events & test_events or calibration_events & test_events:
        raise FantasyScoreCandidateError("event leakage across chronological splits")

    return ChronologicalSplit(
        train=tuple(row for row in ordered if _event_key(row) in train_events),
        calibration=tuple(row for row in ordered if _event_key(row) in calibration_events),
        test=tuple(row for row in ordered if _event_key(row) in test_events),
        train_end=max(date for date, _ in train_events),
        calibration_end=max(date for date, _ in calibration_events),
        test_end=max(date for date, _ in test_events),
    )


def _component_mean(rows: Sequence[Mapping[str, Any]], lane: str) -> dict[str, float]:
    components = COMPONENTS[lane]
    if not rows:
        return {name: 0.0 for name in components}
    return {name: mean(_f(row.get(name, 0.0), field=name) for row in rows) for name in components}


def _shrunk_mean(
    player_rows: Sequence[Mapping[str, Any]],
    cohort_rows: Sequence[Mapping[str, Any]],
    lane: str,
    *,
    prior_games: float = 3.0,
) -> dict[str, float]:
    cohort = _component_mean(cohort_rows, lane)
    if not player_rows:
        return cohort
    player = _component_mean(player_rows, lane)
    n = float(len(player_rows))
    return {
        name: (player[name] * n + cohort[name] * prior_games) / (n + prior_games)
        for name in COMPONENTS[lane]
    }


def fit_candidate(
    rows: Sequence[Mapping[str, Any]],
    *,
    lane: str,
    source_id: str,
    model_version: str | None = None,
) -> CandidateArtifact:
    """Fit one candidate using only strictly prior events for residual construction."""
    lane_n = _lane(lane)
    if not rows:
        raise FantasyScoreCandidateError("historical rows are required")
    normalized = tuple(dict(row) for row in rows)
    for row in normalized:
        _event_key(row)
        _player_key(row)
        for component in COMPONENTS[lane_n]:
            _f(row.get(component, 0.0), field=component)

    split = chronological_split(normalized)
    train = split.train
    by_cohort: dict[str, list[Mapping[str, Any]]] = {}
    by_player: dict[str, list[Mapping[str, Any]]] = {}
    residuals: dict[str, list[Mapping[str, float]]] = {}
    seen: list[Mapping[str, Any]] = []

    for event_key in sorted({_event_key(row) for row in train}):
        event_rows = [row for row in train if _event_key(row) == event_key]
        for row in event_rows:
            cohort = _cohort_key(row, lane_n)
            player = _player_key(row)
            prior_cohort = [r for r in seen if _cohort_key(r, lane_n) == cohort]
            prior_player = [r for r in prior_cohort if _player_key(r) == player]
            if prior_cohort:
                baseline = _shrunk_mean(prior_player, prior_cohort, lane_n)
                residuals.setdefault(cohort, []).append({
                    name: _f(row.get(name, 0.0), field=name) - baseline[name]
                    for name in COMPONENTS[lane_n]
                })
        seen.extend(event_rows)
        for row in event_rows:
            cohort = _cohort_key(row, lane_n)
            player = _player_key(row)
            by_cohort.setdefault(cohort, []).append(row)
            by_player.setdefault(player, []).append(row)

    cohort_means = {
        cohort: _component_mean(items, lane_n) for cohort, items in by_cohort.items() if items
    }
    player_means: dict[str, Mapping[str, float]] = {}
    for player, items in by_player.items():
        cohort = _cohort_key(items[-1], lane_n)
        player_means[player] = _shrunk_mean(items, by_cohort[cohort], lane_n)

    frozen_residuals = {key: tuple(values) for key, values in residuals.items() if values}
    if not frozen_residuals:
        raise FantasyScoreCandidateError("insufficient sequential history to fit residual vectors")

    version = model_version or f"{lane_n}_FANTASY_SCORE_EMPIRICAL_RESIDUAL_CANDIDATE_V1"
    return CandidateArtifact(
        lane=lane_n,
        model_version=version,
        source_id=str(source_id),
        source_sha256=_source_hash(normalized),
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        rows_total=len(normalized),
        split=split,
        player_means=player_means,
        cohort_means=cohort_means,
        residual_vectors=frozen_residuals,
    )


def scoring_profile_sha256(scoring_profile: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(scoring_profile), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _score_components(
    components: Mapping[str, float],
    *,
    lane: str,
    scoring_profile: Mapping[str, Any],
) -> float:
    profile_id = str(scoring_profile.get("profile_id") or "").strip()
    if not profile_id:
        raise FantasyScoreCandidateError("exact scoring profile_id is required")
    if scoring_profile.get("verified") is not True:
        raise FantasyScoreCandidateError("scoring profile must be explicitly verified")
    weights = scoring_profile.get("weights")
    if not isinstance(weights, Mapping):
        raise FantasyScoreCandidateError("scoring profile weights are required")
    missing = [name for name in COMPONENTS[lane] if name not in weights]
    if missing:
        raise FantasyScoreCandidateError(f"scoring profile missing weights: {','.join(missing)}")
    return sum(_f(components[name], field=name) * _f(weights[name], field=f"weight:{name}") for name in COMPONENTS[lane])


def _validate_regimes(lane: str, regimes: Sequence[Mapping[str, Any]] | None) -> tuple[tuple[Mapping[str, Any], ...], bool]:
    if not regimes:
        return (({"name": "NORMAL_ROLE", "probability": 1.0, "component_multipliers": {}, "certified": False},), False)
    total = 0.0
    all_certified = True
    cleaned: list[Mapping[str, Any]] = []
    for regime in regimes:
        name = str(regime.get("name") or "").strip().upper()
        if name not in FAILURE_REGIMES[lane]:
            raise FantasyScoreCandidateError(f"unknown {lane} failure regime: {name or '<missing>'}")
        probability = _f(regime.get("probability"), field="regime probability")
        if not 0.0 <= probability <= 1.0:
            raise FantasyScoreCandidateError("regime probabilities must be in [0,1]")
        multipliers = regime.get("component_multipliers") or {}
        if not isinstance(multipliers, Mapping):
            raise FantasyScoreCandidateError("component_multipliers must be a mapping")
        for component, multiplier in multipliers.items():
            if component not in COMPONENTS[lane]:
                raise FantasyScoreCandidateError(f"unknown component multiplier: {component}")
            if _f(multiplier, field=f"multiplier:{component}") < 0:
                raise FantasyScoreCandidateError("component multipliers must be non-negative")
        certified = bool(regime.get("certified"))
        all_certified = all_certified and certified
        total += probability
        cleaned.append({
            "name": name,
            "probability": probability,
            "component_multipliers": dict(multipliers),
            "certified": certified,
        })
    if abs(total - 1.0) > 1e-8:
        raise FantasyScoreCandidateError("regime probabilities must sum to 1.0")
    return tuple(cleaned), all_certified


def _pick_regime(rng: random.Random, regimes: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    needle = rng.random()
    cumulative = 0.0
    for regime in regimes:
        cumulative += float(regime["probability"])
        if needle <= cumulative:
            return regime
    return regimes[-1]


def _quantile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * min(1.0, max(0.0, q))
    lo = int(math.floor(index))
    hi = int(math.ceil(index))
    if lo == hi:
        return float(ordered[lo])
    weight = index - lo
    return float(ordered[lo] * (1 - weight) + ordered[hi] * weight)


def simulate_exact_line(
    artifact: CandidateArtifact,
    *,
    player_id: str,
    cohort: str,
    exact_line: float,
    side: str,
    scoring_profile: Mapping[str, Any],
    simulation_count: int = MIN_STANDARD_SIMULATIONS,
    seed: int = 17,
    opportunity_context: Mapping[str, Any] | None = None,
    regimes: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return raw candidate probability while keeping governed publication closed."""
    lane = _lane(artifact.lane)
    if simulation_count < MIN_STANDARD_SIMULATIONS:
        raise FantasyScoreCandidateError(
            f"Fantasy Score standard simulation requires at least {MIN_STANDARD_SIMULATIONS} draws"
        )
    side_n = str(side or "").strip().upper()
    if side_n not in {"MORE", "LESS"}:
        raise FantasyScoreCandidateError("side must be MORE or LESS")
    line = _f(exact_line, field="exact_line")
    cohort_n = str(cohort or "").strip().upper() or (lane if lane.startswith("MLB_") else "ALL")
    residual_library = artifact.residual_vectors.get(cohort_n)
    if residual_library is None and len(artifact.residual_vectors) == 1:
        residual_library = next(iter(artifact.residual_vectors.values()))
    if not residual_library:
        raise FantasyScoreCandidateError(f"no fitted residual library for cohort {cohort_n}")

    means = dict(artifact.player_means.get(player_id) or artifact.cohort_means.get(cohort_n) or {})
    if not means and len(artifact.cohort_means) == 1:
        means = dict(next(iter(artifact.cohort_means.values())))
    if not means:
        raise FantasyScoreCandidateError(f"no fitted baseline for cohort {cohort_n}")

    context = opportunity_context or {}
    explicit = context.get("component_means")
    if isinstance(explicit, Mapping):
        for name in COMPONENTS[lane]:
            if explicit.get(name) is not None:
                means[name] = _f(explicit[name], field=f"component_mean:{name}")
    full_context_ready = bool(context.get("certified")) and all(
        isinstance(context.get(key), Mapping) for key in FULL_CONTEXT_KEYS[lane]
    )

    regime_set, regimes_certified = _validate_regimes(lane, regimes)
    rng = random.Random(seed)
    scores: list[float] = []
    totals = {name: 0.0 for name in COMPONENTS[lane]}
    regime_counts = {str(regime["name"]): 0 for regime in regime_set}

    for _ in range(simulation_count):
        residual = residual_library[rng.randrange(len(residual_library))]
        regime = _pick_regime(rng, regime_set)
        regime_counts[str(regime["name"])] += 1
        multipliers = regime.get("component_multipliers") or {}
        components: dict[str, float] = {}
        for name in COMPONENTS[lane]:
            value = max(0.0, _f(means.get(name, 0.0), field=name) + _f(residual.get(name, 0.0), field=name))
            value *= _f(multipliers.get(name, 1.0), field=f"multiplier:{name}")
            if name in COUNT_COMPONENTS[lane]:
                value = float(max(0, int(round(value))))
            components[name] = value
            totals[name] += value
        scores.append(_score_components(components, lane=lane, scoring_profile=scoring_profile))

    more = sum(score > line for score in scores) / simulation_count
    less = sum(score < line for score in scores) / simulation_count
    push = max(0.0, 1.0 - more - less)
    requested = more if side_n == "MORE" else less

    blockers: list[str] = []
    if not full_context_ready:
        blockers.append(f"{lane}_FULL_MODEL_CONTEXT_NOT_CERTIFIED")
    if not regimes_certified:
        blockers.append("FAILURE_REGIME_PACKAGE_NOT_CERTIFIED")
    if artifact.calibration_status != "CERTIFIED":
        blockers.append(artifact.calibration_status)
    if artifact.certification_status != "CERTIFIED":
        blockers.append("FITTED_MODEL_ARTIFACT_NOT_PROMOTED")

    if not full_context_ready or not regimes_certified:
        terminal_status = "MODEL_INPUTS_INSUFFICIENT"
    elif artifact.calibration_status != "CERTIFIED":
        terminal_status = "CALIBRATION_BLOCKED_NO_PUBLISH"
    else:
        terminal_status = "MODEL_QUALIFIED_HOLD"

    return {
        "market_family": f"{lane}_FANTASY_SCORE",
        "controlling_specialist": artifact.controlling_specialist,
        "model_version": artifact.model_version,
        "model_source_sha256": artifact.source_sha256,
        "scoring_profile_id": str(scoring_profile.get("profile_id")),
        "scoring_profile_sha256": scoring_profile_sha256(scoring_profile),
        "simulation_count": simulation_count,
        "seed": seed,
        "exact_line": line,
        "side": side_n,
        "raw_candidate_probability": requested,
        "P(MORE)": more,
        "P(LESS)": less,
        "P(PUSH)": push,
        "calibrated_probability": None,
        "calibrated_lower_bound": None,
        "calibrated_upper_bound": None,
        "fantasy_score_mean": mean(scores),
        "fantasy_score_median": median(scores),
        "fantasy_score_std": pstdev(scores),
        "fantasy_score_p10": _quantile(scores, 0.10),
        "fantasy_score_p25": _quantile(scores, 0.25),
        "fantasy_score_p75": _quantile(scores, 0.75),
        "fantasy_score_p90": _quantile(scores, 0.90),
        "component_means": {name: totals[name] / simulation_count for name in COMPONENTS[lane]},
        "regime_frequencies": {name: count / simulation_count for name, count in regime_counts.items()},
        "terminal_status": terminal_status,
        "blockers": blockers,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def candidate_parity_manifest() -> dict[str, Any]:
    """Machine-readable statement of the current Fantasy Score candidate universe."""
    lanes = []
    for lane in sorted(SUPPORTED_LANES):
        lanes.append({
            "lane": lane,
            "controlling_specialist": CONTROLLING_SPECIALISTS[lane],
            "candidate_model_family": "EMPIRICAL_RESIDUAL_JOINT_BOOTSTRAP",
            "minimum_simulations": MIN_STANDARD_SIMULATIONS,
            "chronological_whole_event_split": True,
            "untouched_test_required": True,
            "exact_scoring_profile_required": True,
            "failure_regime_mixture_supported": True,
            "calibrated_probability": None,
            "calibrated_lower_bound": None,
            "certification_status": "CANDIDATE_ONLY",
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        })
    return {
        "lifecycle_target": "NFL_DFS_FITTED_SIMULATOR_CANDIDATE_PARITY",
        "lanes": lanes,
        "all_candidate_lanes_fail_closed": True,
        "can_execute": False,
    }
