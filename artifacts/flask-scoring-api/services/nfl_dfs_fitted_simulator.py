"""Candidate fitted simulator for governed NFL DFS/fantasy-score props.

This module closes the gap between the V17 input/scoring contract and a future
production-certified NFL full model. It deliberately does *not* promote raw
research probabilities. Historical fitting, joint residual simulation and exact
scoring can be evaluated here; calibrated publication remains fail-closed until an
immutable exact-line calibration artifact is certified by the governed promotion
workflow.

Governance invariants:
    can_execute = False
    probability_publishable = False
    rank_eligible = False
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

from services.specialist_contracts import score_nfl_fantasy_components

CAN_EXECUTE = False
CONTROLLING_SPECIALIST = "wow.nfl-dfs-fantasy-score-expert"
MIN_STANDARD_SIMULATIONS = 50_000
SUPPORTED_POSITIONS = {"QB", "RB", "WR", "TE"}

COMPONENTS = (
    "passing_yards",
    "passing_td",
    "interceptions",
    "rushing_yards",
    "rushing_td",
    "receiving_yards",
    "receptions",
    "receiving_td",
    "fumbles_lost",
    "two_point_conversions",
)

COUNT_COMPONENTS = {
    "passing_td",
    "interceptions",
    "rushing_td",
    "receptions",
    "receiving_td",
    "fumbles_lost",
    "two_point_conversions",
}

FAILURE_REGIMES = {
    "NORMAL_ROLE",
    "LIMITED_ROLE",
    "SNAP_ROUTE_SUPPRESSION",
    "QB_CHANGE_OR_LIMITATION",
    "OL_PROTECTION_COLLAPSE",
    "PACE_COLLAPSE",
    "POSITIVE_BLOWOUT_SCRIPT",
    "NEGATIVE_TRAILING_SCRIPT",
    "WEATHER_SUPPRESSION",
    "COVERAGE_OR_MATCHUP_SUPPRESSION",
    "INJURY_OR_EARLY_EXIT",
}


class CandidateFitError(ValueError):
    """Typed candidate-model validation failure."""


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
    model_version: str
    source_id: str
    source_sha256: str
    generated_at: str
    rows_total: int
    split: ChronologicalSplit
    player_means: Mapping[str, Mapping[str, float]]
    position_means: Mapping[str, Mapping[str, float]]
    residual_vectors: Mapping[str, tuple[Mapping[str, float], ...]]
    calibration_status: str
    certification_status: str = "CANDIDATE_ONLY"
    probability_publishable: bool = False
    rank_eligible: bool = False
    can_execute: bool = CAN_EXECUTE

    def as_metadata(self) -> dict[str, Any]:
        return {
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
            "probability_publishable": self.probability_publishable,
            "rank_eligible": self.rank_eligible,
            "can_execute": self.can_execute,
            "controlling_specialist": CONTROLLING_SPECIALIST,
        }


def _f(value: Any) -> float:
    try:
        out = float(value or 0.0)
    except (TypeError, ValueError) as exc:
        raise CandidateFitError(f"non-numeric component value: {value!r}") from exc
    if math.isnan(out) or math.isinf(out):
        raise CandidateFitError("component values must be finite")
    return out


def _date_text(row: Mapping[str, Any]) -> str:
    raw = str(row.get("event_date") or row.get("game_date") or "").strip()
    if not raw:
        raise CandidateFitError("historical rows require event_date or game_date")
    return raw[:10]


def _event_key(row: Mapping[str, Any]) -> tuple[str, str]:
    event_id = str(row.get("event_id") or row.get("game_id") or "").strip()
    if not event_id:
        raise CandidateFitError("historical rows require event_id or game_id")
    return (_date_text(row), event_id)


def _player_key(row: Mapping[str, Any]) -> str:
    key = str(row.get("player_id") or row.get("player") or row.get("player_name") or "").strip()
    if not key:
        raise CandidateFitError("historical rows require player_id/player/player_name")
    return key


def _position(row: Mapping[str, Any]) -> str:
    position = str(row.get("position") or "").strip().upper()
    if position not in SUPPORTED_POSITIONS:
        raise CandidateFitError(f"unsupported NFL DFS position: {position or '<missing>'}")
    return position


def _canonical_source_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    canonical = json.dumps(
        [dict(sorted((str(k), v) for k, v in row.items())) for row in rows],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def chronological_split(
    rows: Sequence[Mapping[str, Any]],
    *,
    train_fraction: float = 0.70,
    calibration_fraction: float = 0.15,
) -> ChronologicalSplit:
    """Split by whole events so an event never straddles train/calibration/test."""
    if not (0.50 <= train_fraction < 0.90):
        raise CandidateFitError("train_fraction must be in [0.50, 0.90)")
    if not (0.05 <= calibration_fraction < 0.30):
        raise CandidateFitError("calibration_fraction must be in [0.05, 0.30)")
    if train_fraction + calibration_fraction >= 0.95:
        raise CandidateFitError("leave at least 5% for untouched test")

    ordered = sorted(rows, key=_event_key)
    events = sorted({_event_key(row) for row in ordered})
    if len(events) < 7:
        raise CandidateFitError("at least seven chronological events are required")

    train_n = max(1, int(len(events) * train_fraction))
    cal_n = max(1, int(len(events) * calibration_fraction))
    if train_n + cal_n >= len(events):
        cal_n = max(1, len(events) - train_n - 1)

    train_events = set(events[:train_n])
    cal_events = set(events[train_n : train_n + cal_n])
    test_events = set(events[train_n + cal_n :])
    if not test_events:
        raise CandidateFitError("chronological split produced no untouched test events")

    train = tuple(row for row in ordered if _event_key(row) in train_events)
    calibration = tuple(row for row in ordered if _event_key(row) in cal_events)
    test = tuple(row for row in ordered if _event_key(row) in test_events)

    train_end = max(date for date, _ in train_events)
    calibration_end = max(date for date, _ in cal_events)
    test_end = max(date for date, _ in test_events)
    if not (train_end <= calibration_end <= test_end):
        raise CandidateFitError("chronological split ordering invariant failed")
    if train_events & cal_events or train_events & test_events or cal_events & test_events:
        raise CandidateFitError("event leakage across chronological splits")

    return ChronologicalSplit(
        train=train,
        calibration=calibration,
        test=test,
        train_end=train_end,
        calibration_end=calibration_end,
        test_end=test_end,
    )


def _component_mean(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    if not rows:
        return {name: 0.0 for name in COMPONENTS}
    return {name: mean(_f(row.get(name, 0.0)) for row in rows) for name in COMPONENTS}


def _shrunk_mean(
    player_rows: Sequence[Mapping[str, Any]],
    position_rows: Sequence[Mapping[str, Any]],
    *,
    prior_games: float = 3.0,
) -> dict[str, float]:
    position_mean = _component_mean(position_rows)
    if not player_rows:
        return position_mean
    player_mean = _component_mean(player_rows)
    n = float(len(player_rows))
    return {
        name: (player_mean[name] * n + position_mean[name] * prior_games) / (n + prior_games)
        for name in COMPONENTS
    }


def fit_candidate(
    rows: Sequence[Mapping[str, Any]],
    *,
    source_id: str,
    model_version: str = "NFL_DFS_EMPIRICAL_RESIDUAL_CANDIDATE_V1",
) -> CandidateArtifact:
    """Fit a leakage-resistant empirical baseline and joint residual library.

    Rows from the event currently being fitted are never used as predictors for that
    same event. This preserves the strictly-prior-event requirement.
    """
    if not rows:
        raise CandidateFitError("historical rows are required")
    normalized = tuple(dict(row) for row in rows)
    for row in normalized:
        _event_key(row)
        _player_key(row)
        _position(row)
        for name in COMPONENTS:
            _f(row.get(name, 0.0))

    split = chronological_split(normalized)
    train = split.train
    by_position: dict[str, list[Mapping[str, Any]]] = {p: [] for p in SUPPORTED_POSITIONS}
    by_player: dict[str, list[Mapping[str, Any]]] = {}
    residuals: dict[str, list[Mapping[str, float]]] = {p: [] for p in SUPPORTED_POSITIONS}

    seen: list[Mapping[str, Any]] = []
    event_keys = sorted({_event_key(row) for row in train})
    for event_key in event_keys:
        event_rows = [row for row in train if _event_key(row) == event_key]
        for row in event_rows:
            pos = _position(row)
            player = _player_key(row)
            prior_pos = [r for r in seen if _position(r) == pos]
            prior_player = [r for r in prior_pos if _player_key(r) == player]
            if prior_pos:
                baseline = _shrunk_mean(prior_player, prior_pos)
                residuals[pos].append(
                    {name: _f(row.get(name, 0.0)) - baseline[name] for name in COMPONENTS}
                )
        seen.extend(event_rows)
        for row in event_rows:
            pos = _position(row)
            player = _player_key(row)
            by_position[pos].append(row)
            by_player.setdefault(player, []).append(row)

    position_means = {pos: _component_mean(items) for pos, items in by_position.items() if items}
    player_means: dict[str, Mapping[str, float]] = {}
    for player, items in by_player.items():
        pos = _position(items[-1])
        player_means[player] = _shrunk_mean(items, by_position[pos])

    frozen_residuals = {pos: tuple(vectors) for pos, vectors in residuals.items() if vectors}
    if not frozen_residuals:
        raise CandidateFitError("insufficient sequential history to fit residual vectors")

    return CandidateArtifact(
        model_version=model_version,
        source_id=str(source_id),
        source_sha256=_canonical_source_hash(normalized),
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        rows_total=len(normalized),
        split=split,
        player_means=player_means,
        position_means=position_means,
        residual_vectors=frozen_residuals,
        calibration_status="BLOCKED_NO_CERTIFIED_EXACT_LINE_CALIBRATION_ARTIFACT",
    )


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    index = (len(sorted_values) - 1) * min(1.0, max(0.0, q))
    low = int(math.floor(index))
    high = int(math.ceil(index))
    if low == high:
        return float(sorted_values[low])
    weight = index - low
    return float(sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight)


def _validate_regimes(
    regimes: Sequence[Mapping[str, Any]] | None,
) -> tuple[tuple[Mapping[str, Any], ...], bool]:
    if not regimes:
        return (({
            "name": "NORMAL_ROLE",
            "probability": 1.0,
            "component_multipliers": {},
            "certified": False,
        },), False)

    total = 0.0
    all_certified = True
    cleaned: list[Mapping[str, Any]] = []
    for regime in regimes:
        name = str(regime.get("name") or "").strip().upper()
        if name not in FAILURE_REGIMES:
            raise CandidateFitError(f"unknown failure regime: {name or '<missing>'}")
        probability = _f(regime.get("probability"))
        if not 0.0 <= probability <= 1.0:
            raise CandidateFitError("regime probabilities must be in [0, 1]")
        multipliers = regime.get("component_multipliers") or {}
        if not isinstance(multipliers, Mapping):
            raise CandidateFitError("component_multipliers must be a mapping")
        for component, multiplier in multipliers.items():
            if component not in COMPONENTS:
                raise CandidateFitError(f"unknown component multiplier: {component}")
            if _f(multiplier) < 0.0:
                raise CandidateFitError("component multipliers must be non-negative")
        total += probability
        certified = bool(regime.get("certified"))
        all_certified = all_certified and certified
        cleaned.append({
            "name": name,
            "probability": probability,
            "component_multipliers": dict(multipliers),
            "certified": certified,
        })
    if abs(total - 1.0) > 1e-8:
        raise CandidateFitError("regime probabilities must sum to 1.0")
    return tuple(cleaned), all_certified


def _pick_weighted(rng: random.Random, regimes: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    needle = rng.random()
    cumulative = 0.0
    for regime in regimes:
        cumulative += float(regime["probability"])
        if needle <= cumulative:
            return regime
    return regimes[-1]


def _target_means(
    artifact: CandidateArtifact,
    *,
    player_id: str,
    position: str,
    opportunity_context: Mapping[str, Any] | None,
) -> tuple[dict[str, float], bool]:
    position_n = position.upper()
    if position_n not in SUPPORTED_POSITIONS:
        raise CandidateFitError(f"unsupported NFL DFS position: {position_n}")
    base = dict(artifact.player_means.get(player_id) or artifact.position_means.get(position_n) or {})
    if not base:
        raise CandidateFitError(f"no fitted baseline for position {position_n}")

    context = opportunity_context or {}
    player_model = context.get("player_opportunity_model")
    if isinstance(player_model, Mapping):
        explicit = player_model.get("component_means")
        if isinstance(explicit, Mapping):
            for name in COMPONENTS:
                if explicit.get(name) is not None:
                    base[name] = _f(explicit[name])

    full_context = (
        isinstance(context.get("team_play_distribution"), Mapping)
        and isinstance(context.get("game_state_model"), Mapping)
        and isinstance(context.get("player_opportunity_model"), Mapping)
        and bool(context.get("certified"))
    )
    return {name: max(0.0, _f(base.get(name, 0.0))) for name in COMPONENTS}, full_context


def simulate_exact_line(
    artifact: CandidateArtifact,
    *,
    player_id: str,
    position: str,
    exact_line: float,
    side: str,
    scoring_profile: Mapping[str, Any],
    simulation_count: int = MIN_STANDARD_SIMULATIONS,
    seed: int = 17,
    opportunity_context: Mapping[str, Any] | None = None,
    regimes: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Produce a raw candidate distribution while keeping publication fail-closed."""
    if simulation_count < MIN_STANDARD_SIMULATIONS:
        raise CandidateFitError(
            f"NFL DFS standard simulation requires at least {MIN_STANDARD_SIMULATIONS} draws"
        )
    side_n = str(side).strip().upper()
    if side_n not in {"MORE", "LESS"}:
        raise CandidateFitError("side must be MORE or LESS")
    line = _f(exact_line)
    position_n = position.upper()
    residual_library = artifact.residual_vectors.get(position_n)
    if not residual_library:
        raise CandidateFitError(f"no fitted residual library for position {position_n}")

    means, full_context_ready = _target_means(
        artifact,
        player_id=player_id,
        position=position_n,
        opportunity_context=opportunity_context,
    )
    regime_set, regimes_certified = _validate_regimes(regimes)

    rng = random.Random(seed)
    scores: list[float] = []
    component_totals = {name: 0.0 for name in COMPONENTS}
    regime_counts: dict[str, int] = {str(r["name"]): 0 for r in regime_set}

    for _ in range(simulation_count):
        residual = residual_library[rng.randrange(len(residual_library))]
        regime = _pick_weighted(rng, regime_set)
        regime_counts[str(regime["name"])] += 1
        multipliers = regime.get("component_multipliers") or {}
        components: dict[str, float] = {}
        for name in COMPONENTS:
            value = means[name] + _f(residual.get(name, 0.0))
            value *= _f(multipliers.get(name, 1.0))
            value = max(0.0, value)
            if name in COUNT_COMPONENTS:
                value = float(max(0, int(round(value))))
            components[name] = value
            component_totals[name] += value
        scores.append(score_nfl_fantasy_components(components, scoring_profile))

    scores_sorted = sorted(scores)
    more = sum(score > line for score in scores) / simulation_count
    less = sum(score < line for score in scores) / simulation_count
    push = max(0.0, 1.0 - more - less)
    requested_raw = more if side_n == "MORE" else less

    blockers: list[str] = []
    if not full_context_ready:
        blockers.append("NFL_FULL_MODEL_CONTEXT_NOT_CERTIFIED")
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
        "market_family": "NFL_DFS_FANTASY_SCORE",
        "controlling_specialist": CONTROLLING_SPECIALIST,
        "model_version": artifact.model_version,
        "simulation_count": simulation_count,
        "seed": seed,
        "exact_line": line,
        "side": side_n,
        "raw_candidate_probability": requested_raw,
        "P(MORE)": more,
        "P(LESS)": less,
        "P(PUSH)": push,
        "calibrated_probability": None,
        "calibrated_lower_bound": None,
        "calibrated_upper_bound": None,
        "fantasy_score_mean": mean(scores),
        "fantasy_score_median": median(scores),
        "fantasy_score_std": pstdev(scores),
        "fantasy_score_p10": _quantile(scores_sorted, 0.10),
        "fantasy_score_p25": _quantile(scores_sorted, 0.25),
        "fantasy_score_p75": _quantile(scores_sorted, 0.75),
        "fantasy_score_p90": _quantile(scores_sorted, 0.90),
        "component_means": {name: component_totals[name] / simulation_count for name in COMPONENTS},
        "regime_frequencies": {name: count / simulation_count for name, count in regime_counts.items()},
        "terminal_status": terminal_status,
        "blockers": blockers,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": CAN_EXECUTE,
    }
