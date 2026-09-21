"""WOW V17 compounding intelligence primitives.

This module adds market memory, signal memory, specialist scorecards, and the
champion/challenger review lab above the immutable Core Intelligence ledger.
Everything is advisory: no function mutates a prediction, certifies a model,
promotes a challenger, publishes a probability, or executes a wager.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import log
from typing import Any, Iterable, Mapping, Sequence
from uuid import NAMESPACE_URL, uuid5

from v17.core_intelligence import AUTHORITY, CAN_EXECUTE, LearningHypothesis

COMPOUNDING_SCHEMA_VERSION = "WOW17_COMPOUNDING_INTELLIGENCE_V1"


def _text(value: Any) -> str | None:
    value = str(value or "").strip()
    return value or None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _prob(value: Any) -> float | None:
    value = _number(value)
    if value is None or not 0.0 < value < 1.0:
        return None
    return value


def _metric(probability: float | None, target: int | None) -> tuple[float | None, float | None]:
    if probability is None or target not in (0, 1):
        return None, None
    residual = float(target) - probability
    brier = residual * residual
    clipped = min(max(probability, 1e-12), 1.0 - 1e-12)
    loss = -(target * log(clipped) + (1 - target) * log(1.0 - clipped))
    return brier, loss


def _numeric_bucket(value: float) -> str:
    if 0.0 <= value <= 1.0:
        low = min(0.9, int(value * 10) / 10)
        return f"{low:.1f}-{min(1.0, low + 0.1):.1f}"
    if value <= -10:
        return "<=-10"
    if value <= -3:
        return "-10:-3"
    if value <= -1:
        return "-3:-1"
    if value < 0:
        return "-1:0"
    if value == 0:
        return "0"
    if value < 1:
        return "0:1"
    if value < 3:
        return "1:3"
    if value < 10:
        return "3:10"
    return ">=10"


def _safe_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _identity(*parts: Any) -> str:
    return str(uuid5(NAMESPACE_URL, ":".join(str(part) for part in parts)))


@dataclass(frozen=True)
class MarketMemoryObservation:
    market_memory_id: str
    observation_id: str
    source_prediction_kind: str
    source_prediction_id: str
    sport: str | None
    market_family: str | None
    stat_type: str | None
    specialist_id: str | None
    model_family: str | None
    model_artifact_version: str | None
    model_probability: float | None
    opening_market_probability: float | None
    closing_market_probability: float | None
    model_minus_opening: float | None
    model_minus_closing: float | None
    market_move: float | None
    outcome_target: int | None
    model_brier: float | None
    closing_market_brier: float | None
    brier_advantage_vs_close: float | None
    model_log_loss: float | None
    closing_market_log_loss: float | None
    log_loss_advantage_vs_close: float | None
    model_outperformed_close: bool | None
    schema_version: str = COMPOUNDING_SCHEMA_VERSION
    authority: str = AUTHORITY
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SignalMemoryObservation:
    signal_memory_id: str
    observation_id: str
    source_prediction_kind: str
    source_prediction_id: str
    sport: str | None
    market_family: str | None
    stat_type: str | None
    specialist_id: str | None
    model_family: str | None
    signal_namespace: str
    signal_name: str
    signal_value_text: str | None
    signal_value_numeric: float | None
    signal_bucket: str
    model_probability: float | None
    outcome_target: int | None
    residual: float | None
    brier_score: float | None
    schema_version: str = COMPOUNDING_SCHEMA_VERSION
    authority: str = AUTHORITY
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SignalScorecard:
    snapshot_id: str
    context_key: str
    signal_namespace: str
    signal_name: str
    signal_bucket: str
    sample_n: int
    scored_n: int
    observed_rate: float | None
    mean_model_probability: float | None
    calibration_bias: float | None
    mean_residual: float | None
    mean_brier_score: float | None
    baseline_observed_rate: float | None
    observed_lift_vs_context: float | None
    ready_for_review: bool
    min_samples: int
    schema_version: str = COMPOUNDING_SCHEMA_VERSION
    authority: str = AUTHORITY
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketScorecard:
    snapshot_id: str
    context_key: str
    sample_n: int
    comparable_n: int
    mean_model_minus_opening: float | None
    mean_model_minus_closing: float | None
    mean_market_move: float | None
    mean_brier_advantage_vs_close: float | None
    mean_log_loss_advantage_vs_close: float | None
    beat_close_n: int
    beat_close_rate: float | None
    ready_for_review: bool
    min_samples: int
    schema_version: str = COMPOUNDING_SCHEMA_VERSION
    authority: str = AUTHORITY
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SpecialistScorecard:
    snapshot_id: str
    specialist_key: str
    specialist_id: str
    source_prediction_kind: str
    sport: str | None
    market_family: str | None
    stat_type: str | None
    sample_n: int
    scored_n: int
    wins: int
    losses: int
    mean_probability: float | None
    observed_rate: float | None
    calibration_bias: float | None
    mean_brier_score: float | None
    mean_log_loss: float | None
    market_comparable_n: int
    mean_brier_advantage_vs_close: float | None
    beat_close_rate: float | None
    model_artifact_versions: tuple[str, ...]
    ready_for_review: bool
    min_samples: int
    schema_version: str = COMPOUNDING_SCHEMA_VERSION
    authority: str = AUTHORITY
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["model_artifact_versions"] = list(self.model_artifact_versions)
        return row


@dataclass(frozen=True)
class ChallengerProposal:
    proposal_id: str
    proposal_type: str
    trigger_source: str
    trigger_id: str
    target_key: str
    specialist_id: str | None
    sport: str | None
    market_family: str | None
    stat_type: str | None
    evidence_n: int
    recipe: Mapping[str, Any]
    requested_lifecycle_state: str = "CANDIDATE"
    shadow_required: bool = True
    automatic_certification: bool = False
    automatic_promotion: bool = False
    probability_publishable: bool = False
    authority: str = AUTHORITY
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["recipe"] = dict(self.recipe)
        return row


@dataclass(frozen=True)
class PromotionReview:
    review_id: str
    challenger_id: str
    target_key: str
    holdout_n: int
    champion_brier: float | None
    challenger_brier: float | None
    brier_improvement: float | None
    champion_log_loss: float | None
    challenger_log_loss: float | None
    log_loss_improvement: float | None
    calibration_not_worse: bool | None
    eligible_for_governed_review: bool
    status: str
    blockers: tuple[str, ...]
    automatic_promotion: bool = False
    probability_publishable: bool = False
    authority: str = AUTHORITY
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["blockers"] = list(self.blockers)
        return row


def build_market_memory(
    observation: Mapping[str, Any],
    *,
    opening_market_probability: Any = None,
    closing_market_probability: Any = None,
    specialist_id: Any = None,
) -> MarketMemoryObservation:
    model_probability = _prob(observation.get("probability"))
    opening = _prob(opening_market_probability)
    closing = _prob(closing_market_probability)
    target = observation.get("outcome_target") if observation.get("outcome_target") in (0, 1) else None
    model_brier, model_log = _metric(model_probability, target)
    close_brier, close_log = _metric(closing, target)
    brier_advantage = None if model_brier is None or close_brier is None else close_brier - model_brier
    log_advantage = None if model_log is None or close_log is None else close_log - model_log
    source_id = str(observation.get("source_prediction_id") or "").strip()
    observation_id = str(observation.get("observation_id") or "").strip()
    if not source_id or not observation_id:
        raise ValueError("MARKET_MEMORY_IDENTITY_REQUIRED")
    return MarketMemoryObservation(
        market_memory_id=_identity(COMPOUNDING_SCHEMA_VERSION, "MARKET", observation_id),
        observation_id=observation_id,
        source_prediction_kind=str(observation.get("source_prediction_kind") or "").upper(),
        source_prediction_id=source_id,
        sport=_text(observation.get("sport")),
        market_family=_text(observation.get("market_family")),
        stat_type=_text(observation.get("stat_type")),
        specialist_id=_text(specialist_id),
        model_family=_text(observation.get("model_family")),
        model_artifact_version=_text(observation.get("model_artifact_version")),
        model_probability=model_probability,
        opening_market_probability=opening,
        closing_market_probability=closing,
        model_minus_opening=None if model_probability is None or opening is None else model_probability - opening,
        model_minus_closing=None if model_probability is None or closing is None else model_probability - closing,
        market_move=None if opening is None or closing is None else closing - opening,
        outcome_target=target,
        model_brier=model_brier,
        closing_market_brier=close_brier,
        brier_advantage_vs_close=brier_advantage,
        model_log_loss=model_log,
        closing_market_log_loss=close_log,
        log_loss_advantage_vs_close=log_advantage,
        model_outperformed_close=None if brier_advantage is None else brier_advantage > 0,
        can_execute=False,
    )


def _signal(
    *, observation: Mapping[str, Any], specialist_id: Any, namespace: str,
    name: str, value: Any,
) -> SignalMemoryObservation | None:
    if value is None:
        return None
    numeric = _number(value)
    text = None if numeric is not None else _text(value)
    if numeric is None and text is None:
        return None
    bucket = _numeric_bucket(numeric) if numeric is not None else text.upper()[:120]
    observation_id = str(observation.get("observation_id") or "").strip()
    source_id = str(observation.get("source_prediction_id") or "").strip()
    if not observation_id or not source_id:
        raise ValueError("SIGNAL_MEMORY_IDENTITY_REQUIRED")
    probability = _prob(observation.get("probability"))
    target = observation.get("outcome_target") if observation.get("outcome_target") in (0, 1) else None
    residual = None if probability is None or target is None else float(target) - probability
    brier, _ = _metric(probability, target)
    return SignalMemoryObservation(
        signal_memory_id=_identity(COMPOUNDING_SCHEMA_VERSION, "SIGNAL", observation_id, namespace, name, bucket),
        observation_id=observation_id,
        source_prediction_kind=str(observation.get("source_prediction_kind") or "").upper(),
        source_prediction_id=source_id,
        sport=_text(observation.get("sport")),
        market_family=_text(observation.get("market_family")),
        stat_type=_text(observation.get("stat_type")),
        specialist_id=_text(specialist_id),
        model_family=_text(observation.get("model_family")),
        signal_namespace=namespace,
        signal_name=name,
        signal_value_text=text,
        signal_value_numeric=numeric,
        signal_bucket=bucket,
        model_probability=probability,
        outcome_target=target,
        residual=residual,
        brier_score=brier,
        can_execute=False,
    )


def extract_signal_memories(
    observation: Mapping[str, Any],
    prediction: Mapping[str, Any],
    *,
    specialist_id: Any = None,
) -> tuple[SignalMemoryObservation, ...]:
    """Extract only pregame/source-receipt signals; never postgame failure labels."""
    kind = str(observation.get("source_prediction_kind") or "").upper()
    specs: list[tuple[str, str, Any]] = []
    if kind == "PROP":
        for field in (
            "primary_failure_path", "market_prior_quality", "market_prior_weight",
            "effective_sample_size", "regime_probability_sum",
        ):
            specs.append(("PROP_BASE", field.upper(), prediction.get(field)))
        raw = _prob(prediction.get("raw_model_probability"))
        independent = _prob(prediction.get("independent_model_probability"))
        prior = _prob(prediction.get("market_prior_probability")) or _prob(prediction.get("reference_market_probability_raw"))
        calibrated = _prob(prediction.get("calibrated_probability"))
        if raw is not None and independent is not None:
            specs.append(("PROP_DERIVED", "INDEPENDENT_MINUS_RAW", independent - raw))
        if raw is not None and calibrated is not None:
            specs.append(("PROP_DERIVED", "CALIBRATED_MINUS_RAW", calibrated - raw))
        if raw is not None and prior is not None:
            specs.append(("PROP_DERIVED", "RAW_MINUS_MARKET_PRIOR", raw - prior))
        for key, value in _safe_mapping(prediction.get("regime_probabilities_json")).items():
            specs.append(("PROP_REGIME", f"REGIME_{str(key).upper()}", value))
        tags = prediction.get("failure_cause_tags") or ()
        if isinstance(tags, str):
            tags = (tags,)
        for tag in tags:
            if _text(tag):
                specs.append(("PROP_PREGAME_TAG", f"TAG_{str(tag).upper()}", "PRESENT"))
    elif kind == "EVENT":
        for field in (
            "favorite_side", "largest_favorite_loss_path", "favorite_failure_path_probability",
            "tie_after_9_probability", "home_wins_extras_given_tie", "away_wins_extras_given_tie",
            "market_prior_weight", "home_starter_status", "away_starter_status",
            "home_lineup_status", "away_lineup_status",
        ):
            specs.append(("EVENT_BASE", field.upper(), prediction.get(field)))
        home_runs = _number(prediction.get("projected_runs_home"))
        away_runs = _number(prediction.get("projected_runs_away"))
        if home_runs is not None and away_runs is not None:
            specs.append(("EVENT_DERIVED", "PROJECTED_RUN_DIFF_HOME", home_runs - away_runs))
        for namespace, field in (
            ("EVENT_FAVORITE_FAILURE", "favorite_failure_paths_json"),
            ("EVENT_UNDERDOG_PATH", "underdog_upset_path_json"),
        ):
            for key, value in _safe_mapping(prediction.get(field)).items():
                if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                    specs.append((namespace, str(key).upper(), value))
    else:
        raise ValueError("SIGNAL_SOURCE_KIND_UNSUPPORTED")

    memories = []
    for namespace, name, value in specs:
        row = _signal(
            observation=observation,
            specialist_id=specialist_id,
            namespace=namespace,
            name=name,
            value=value,
        )
        if row is not None:
            memories.append(row)
    return tuple(memories)


def _signal_context(row: SignalMemoryObservation) -> str:
    return "|".join((
        row.source_prediction_kind,
        row.sport or "UNKNOWN_SPORT",
        row.market_family or "UNKNOWN_MARKET",
        row.stat_type or "UNKNOWN_STAT",
        row.specialist_id or row.model_family or "UNKNOWN_SPECIALIST",
        row.signal_namespace,
        row.signal_name,
    ))


def summarize_signals(
    memories: Iterable[SignalMemoryObservation],
    *,
    min_samples: int = 20,
) -> tuple[SignalScorecard, ...]:
    rows = list(memories)
    grouped: dict[tuple[str, str], list[SignalMemoryObservation]] = {}
    baseline: dict[str, list[SignalMemoryObservation]] = {}
    for row in rows:
        context = _signal_context(row)
        grouped.setdefault((context, row.signal_bucket), []).append(row)
        baseline.setdefault(context, []).append(row)
    scorecards: list[SignalScorecard] = []
    for (context, bucket), bucket_rows in sorted(grouped.items()):
        scored = [r for r in bucket_rows if r.outcome_target in (0, 1) and r.model_probability is not None]
        base_scored = [r for r in baseline[context] if r.outcome_target in (0, 1)]
        observed = sum(r.outcome_target for r in scored) / len(scored) if scored else None
        mean_prob = sum(r.model_probability for r in scored if r.model_probability is not None) / len(scored) if scored else None
        base_rate = sum(r.outcome_target for r in base_scored) / len(base_scored) if base_scored else None
        first = bucket_rows[0]
        mean_residual = sum(r.residual for r in scored if r.residual is not None) / len(scored) if scored else None
        briers = [r.brier_score for r in scored if r.brier_score is not None]
        scorecards.append(SignalScorecard(
            snapshot_id=_identity(COMPOUNDING_SCHEMA_VERSION, "SIGNAL_SCORECARD", context, bucket, len(scored), observed),
            context_key=context,
            signal_namespace=first.signal_namespace,
            signal_name=first.signal_name,
            signal_bucket=bucket,
            sample_n=len(bucket_rows),
            scored_n=len(scored),
            observed_rate=observed,
            mean_model_probability=mean_prob,
            calibration_bias=None if observed is None or mean_prob is None else observed - mean_prob,
            mean_residual=mean_residual,
            mean_brier_score=sum(briers) / len(briers) if briers else None,
            baseline_observed_rate=base_rate,
            observed_lift_vs_context=None if observed is None or base_rate is None else observed - base_rate,
            ready_for_review=len(scored) >= min_samples,
            min_samples=min_samples,
            can_execute=False,
        ))
    return tuple(scorecards)


def _market_context(row: MarketMemoryObservation) -> str:
    return "|".join((
        row.source_prediction_kind,
        row.sport or "UNKNOWN_SPORT",
        row.market_family or "UNKNOWN_MARKET",
        row.stat_type or "UNKNOWN_STAT",
        row.specialist_id or row.model_family or "UNKNOWN_SPECIALIST",
    ))


def summarize_markets(
    rows: Iterable[MarketMemoryObservation],
    *, min_samples: int = 30,
) -> tuple[MarketScorecard, ...]:
    grouped: dict[str, list[MarketMemoryObservation]] = {}
    for row in rows:
        grouped.setdefault(_market_context(row), []).append(row)
    out: list[MarketScorecard] = []
    for context, items in sorted(grouped.items()):
        comparable = [r for r in items if r.brier_advantage_vs_close is not None]
        opening_edges = [r.model_minus_opening for r in items if r.model_minus_opening is not None]
        closing_edges = [r.model_minus_closing for r in items if r.model_minus_closing is not None]
        moves = [r.market_move for r in items if r.market_move is not None]
        brier_adv = [r.brier_advantage_vs_close for r in comparable if r.brier_advantage_vs_close is not None]
        log_adv = [r.log_loss_advantage_vs_close for r in comparable if r.log_loss_advantage_vs_close is not None]
        beats = sum(1 for r in comparable if r.model_outperformed_close is True)
        out.append(MarketScorecard(
            snapshot_id=_identity(COMPOUNDING_SCHEMA_VERSION, "MARKET_SCORECARD", context, len(comparable), sum(brier_adv) if brier_adv else 0),
            context_key=context,
            sample_n=len(items),
            comparable_n=len(comparable),
            mean_model_minus_opening=sum(opening_edges)/len(opening_edges) if opening_edges else None,
            mean_model_minus_closing=sum(closing_edges)/len(closing_edges) if closing_edges else None,
            mean_market_move=sum(moves)/len(moves) if moves else None,
            mean_brier_advantage_vs_close=sum(brier_adv)/len(brier_adv) if brier_adv else None,
            mean_log_loss_advantage_vs_close=sum(log_adv)/len(log_adv) if log_adv else None,
            beat_close_n=beats,
            beat_close_rate=beats/len(comparable) if comparable else None,
            ready_for_review=len(comparable) >= min_samples,
            min_samples=min_samples,
            can_execute=False,
        ))
    return tuple(out)


def summarize_specialists(
    observations: Sequence[Mapping[str, Any]],
    market_rows: Sequence[MarketMemoryObservation] = (),
    *,
    min_samples: int = 30,
) -> tuple[SpecialistScorecard, ...]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in observations:
        specialist = _text(row.get("specialist_id")) or _text(row.get("model_family")) or "UNKNOWN_SPECIALIST"
        key = "|".join((
            str(row.get("source_prediction_kind") or "").upper(),
            str(row.get("sport") or "UNKNOWN_SPORT"),
            str(row.get("market_family") or "UNKNOWN_MARKET"),
            str(row.get("stat_type") or "UNKNOWN_STAT"),
            specialist,
        ))
        grouped.setdefault(key, []).append(row)
    market_by_context: dict[str, list[MarketMemoryObservation]] = {}
    for row in market_rows:
        market_by_context.setdefault(_market_context(row), []).append(row)
    out: list[SpecialistScorecard] = []
    for key, rows in sorted(grouped.items()):
        scored = [r for r in rows if r.get("outcome_target") in (0, 1) and _prob(r.get("probability")) is not None]
        first = rows[0]
        probs = [_prob(r.get("probability")) for r in scored]
        targets = [int(r.get("outcome_target")) for r in scored]
        briers = [_number(r.get("brier_score")) for r in scored]
        losses = [_number(r.get("log_loss")) for r in scored]
        probs = [p for p in probs if p is not None]
        briers = [b for b in briers if b is not None]
        losses = [v for v in losses if v is not None]
        mean_prob = sum(probs)/len(probs) if probs else None
        observed = sum(targets)/len(targets) if targets else None
        specialist = _text(first.get("specialist_id")) or _text(first.get("model_family")) or "UNKNOWN_SPECIALIST"
        context = "|".join((
            str(first.get("source_prediction_kind") or "").upper(),
            str(first.get("sport") or "UNKNOWN_SPORT"),
            str(first.get("market_family") or "UNKNOWN_MARKET"),
            str(first.get("stat_type") or "UNKNOWN_STAT"),
            specialist,
        ))
        market = market_by_context.get(context, [])
        comparable = [m for m in market if m.brier_advantage_vs_close is not None]
        adv = [m.brier_advantage_vs_close for m in comparable if m.brier_advantage_vs_close is not None]
        versions = tuple(sorted({str(r.get("model_artifact_version")) for r in rows if _text(r.get("model_artifact_version"))}))
        out.append(SpecialistScorecard(
            snapshot_id=_identity(COMPOUNDING_SCHEMA_VERSION, "SPECIALIST", key, len(scored), sum(briers) if briers else 0),
            specialist_key=key,
            specialist_id=specialist,
            source_prediction_kind=str(first.get("source_prediction_kind") or "").upper(),
            sport=_text(first.get("sport")),
            market_family=_text(first.get("market_family")),
            stat_type=_text(first.get("stat_type")),
            sample_n=len(rows),
            scored_n=len(scored),
            wins=sum(targets),
            losses=len(targets)-sum(targets),
            mean_probability=mean_prob,
            observed_rate=observed,
            calibration_bias=None if mean_prob is None or observed is None else observed - mean_prob,
            mean_brier_score=sum(briers)/len(briers) if briers else None,
            mean_log_loss=sum(losses)/len(losses) if losses else None,
            market_comparable_n=len(comparable),
            mean_brier_advantage_vs_close=sum(adv)/len(adv) if adv else None,
            beat_close_rate=(sum(1 for m in comparable if m.model_outperformed_close is True)/len(comparable)) if comparable else None,
            model_artifact_versions=versions,
            ready_for_review=len(scored) >= min_samples,
            min_samples=min_samples,
            can_execute=False,
        ))
    return tuple(out)


def build_challenger_proposals(
    *,
    hypotheses: Sequence[LearningHypothesis] = (),
    signal_scorecards: Sequence[SignalScorecard] = (),
    market_scorecards: Sequence[MarketScorecard] = (),
    specialist_scorecards: Sequence[SpecialistScorecard] = (),
    signal_lift_threshold: float = 0.10,
    specialist_brier_threshold: float = 0.25,
    market_brier_disadvantage_threshold: float = -0.01,
) -> tuple[ChallengerProposal, ...]:
    proposals: dict[str, ChallengerProposal] = {}

    for h in hypotheses:
        proposal_type = "RECALIBRATION_CHALLENGER" if h.hypothesis_type == "CALIBRATION_BIAS" else "MODEL_FEATURE_CHALLENGER"
        pid = _identity(COMPOUNDING_SCHEMA_VERSION, "PROPOSAL", h.hypothesis_id, proposal_type)
        proposals[pid] = ChallengerProposal(
            proposal_id=pid,
            proposal_type=proposal_type,
            trigger_source="LEARNING_HYPOTHESIS",
            trigger_id=h.hypothesis_id,
            target_key=h.cohort_key,
            specialist_id=None,
            sport=None,
            market_family=None,
            stat_type=None,
            evidence_n=h.evidence_n,
            recipe={
                "metric_name": h.metric_name,
                "metric_value": h.metric_value,
                "threshold": h.threshold,
                "direction": h.direction,
                "required_validation": ["TIME_SPLIT_HOLDOUT", "BRIER", "LOG_LOSS", "CALIBRATION"],
            },
        )

    for s in signal_scorecards:
        if not s.ready_for_review or s.observed_lift_vs_context is None or abs(s.observed_lift_vs_context) < signal_lift_threshold:
            continue
        pid = _identity(COMPOUNDING_SCHEMA_VERSION, "PROPOSAL", s.snapshot_id, "SIGNAL")
        proposals[pid] = ChallengerProposal(
            proposal_id=pid,
            proposal_type="SIGNAL_REWEIGHT_OR_ABLATION_CHALLENGER",
            trigger_source="SIGNAL_SCORECARD",
            trigger_id=s.snapshot_id,
            target_key=s.context_key,
            specialist_id=None,
            sport=None,
            market_family=None,
            stat_type=None,
            evidence_n=s.scored_n,
            recipe={
                "signal_namespace": s.signal_namespace,
                "signal_name": s.signal_name,
                "signal_bucket": s.signal_bucket,
                "observed_lift_vs_context": s.observed_lift_vs_context,
                "instruction": "test reweight and ablation variants; do not alter champion in place",
                "required_validation": ["PAIRED_HOLDOUT", "ABLATION", "BRIER", "LOG_LOSS"],
            },
        )

    for m in market_scorecards:
        if not m.ready_for_review or m.mean_brier_advantage_vs_close is None or m.mean_brier_advantage_vs_close > market_brier_disadvantage_threshold:
            continue
        pid = _identity(COMPOUNDING_SCHEMA_VERSION, "PROPOSAL", m.snapshot_id, "MARKET")
        proposals[pid] = ChallengerProposal(
            proposal_id=pid,
            proposal_type="MARKET_PRIOR_OR_TIMING_CHALLENGER",
            trigger_source="MARKET_SCORECARD",
            trigger_id=m.snapshot_id,
            target_key=m.context_key,
            specialist_id=None,
            sport=None,
            market_family=None,
            stat_type=None,
            evidence_n=m.comparable_n,
            recipe={
                "mean_brier_advantage_vs_close": m.mean_brier_advantage_vs_close,
                "beat_close_rate": m.beat_close_rate,
                "instruction": "test market-prior weight/timing variants in shadow only",
                "required_validation": ["PAIRED_CLOSE_COMPARISON", "TIME_SPLIT_HOLDOUT", "BRIER", "LOG_LOSS"],
            },
        )

    for s in specialist_scorecards:
        if not s.ready_for_review or s.mean_brier_score is None:
            continue
        calibration_problem = s.calibration_bias is not None and abs(s.calibration_bias) >= 0.075
        accuracy_problem = s.mean_brier_score >= specialist_brier_threshold
        market_problem = s.mean_brier_advantage_vs_close is not None and s.mean_brier_advantage_vs_close <= market_brier_disadvantage_threshold
        if not (calibration_problem or accuracy_problem or market_problem):
            continue
        pid = _identity(COMPOUNDING_SCHEMA_VERSION, "PROPOSAL", s.snapshot_id, "SPECIALIST")
        proposals[pid] = ChallengerProposal(
            proposal_id=pid,
            proposal_type="SPECIALIST_CHALLENGER",
            trigger_source="SPECIALIST_SCORECARD",
            trigger_id=s.snapshot_id,
            target_key=s.specialist_key,
            specialist_id=s.specialist_id,
            sport=s.sport,
            market_family=s.market_family,
            stat_type=s.stat_type,
            evidence_n=s.scored_n,
            recipe={
                "mean_brier_score": s.mean_brier_score,
                "calibration_bias": s.calibration_bias,
                "mean_brier_advantage_vs_close": s.mean_brier_advantage_vs_close,
                "artifact_versions_observed": list(s.model_artifact_versions),
                "instruction": "train/register a new D1 candidate or recalibration candidate; champion remains active",
                "candidate_registry": "wow_d1_candidate_artifacts",
                "required_validation": ["SOURCE_REVIEW", "TIME_SPLIT_HOLDOUT", "SHADOW_REPLAY", "CERTIFICATION_REPLAY"],
            },
        )

    return tuple(proposals[key] for key in sorted(proposals))


def evaluate_promotion_review(
    *,
    challenger_id: str,
    target_key: str,
    holdout_n: int,
    champion_brier: Any,
    challenger_brier: Any,
    champion_log_loss: Any = None,
    challenger_log_loss: Any = None,
    champion_calibration_error: Any = None,
    challenger_calibration_error: Any = None,
    source_review_pass: bool = False,
    certification_replay_pass: bool = False,
    min_holdout_n: int = 100,
    min_brier_improvement: float = 0.01,
) -> PromotionReview:
    cb = _number(champion_brier)
    xb = _number(challenger_brier)
    cl = _number(champion_log_loss)
    xl = _number(challenger_log_loss)
    cc = _number(champion_calibration_error)
    xc = _number(challenger_calibration_error)
    blockers: list[str] = []
    if holdout_n < min_holdout_n:
        blockers.append("HOLDOUT_TOO_SMALL")
    if cb is None or xb is None:
        blockers.append("BRIER_COMPARISON_MISSING")
    brier_improvement = None if cb is None or xb is None else cb - xb
    if brier_improvement is not None and brier_improvement < min_brier_improvement:
        blockers.append("BRIER_IMPROVEMENT_INSUFFICIENT")
    log_improvement = None if cl is None or xl is None else cl - xl
    if log_improvement is not None and log_improvement < 0:
        blockers.append("LOG_LOSS_WORSE")
    calibration_not_worse = None if cc is None or xc is None else xc <= cc
    if calibration_not_worse is False:
        blockers.append("CALIBRATION_WORSE")
    if not source_review_pass:
        blockers.append("SOURCE_REVIEW_REQUIRED")
    if not certification_replay_pass:
        blockers.append("CERTIFICATION_REPLAY_REQUIRED")
    eligible = not blockers
    review_id = _identity(
        COMPOUNDING_SCHEMA_VERSION, "PROMOTION_REVIEW", challenger_id, target_key,
        holdout_n, cb, xb, cl, xl, cc, xc, source_review_pass, certification_replay_pass,
    )
    return PromotionReview(
        review_id=review_id,
        challenger_id=str(challenger_id),
        target_key=str(target_key),
        holdout_n=int(holdout_n),
        champion_brier=cb,
        challenger_brier=xb,
        brier_improvement=brier_improvement,
        champion_log_loss=cl,
        challenger_log_loss=xl,
        log_loss_improvement=log_improvement,
        calibration_not_worse=calibration_not_worse,
        eligible_for_governed_review=eligible,
        status="ELIGIBLE_FOR_GOVERNED_REVIEW" if eligible else "SHADOW_OR_REVIEW_BLOCKED",
        blockers=tuple(blockers),
        automatic_promotion=False,
        probability_publishable=False,
        can_execute=False,
    )


__all__ = [
    "COMPOUNDING_SCHEMA_VERSION",
    "MarketMemoryObservation",
    "MarketScorecard",
    "SignalMemoryObservation",
    "SignalScorecard",
    "SpecialistScorecard",
    "ChallengerProposal",
    "PromotionReview",
    "build_market_memory",
    "extract_signal_memories",
    "summarize_signals",
    "summarize_markets",
    "summarize_specialists",
    "build_challenger_proposals",
    "evaluate_promotion_review",
]
