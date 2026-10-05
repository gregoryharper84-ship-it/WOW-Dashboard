"""V17 research-only JS Style Intelligence overlay.

Research/discovery only. This module never creates, blends, or modifies sporting
probability, calibration, lower bounds, terminal status, or execution authority.
Live/postgame facts are barred from current-selection scoring.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
PROBABILITY_AUTHORITY = "CONTROLLING_SPECIALIST_ONLY"
VERSION = "WOW_JS_STYLE_INTELLIGENCE_V17_RESEARCH_V1"

LESS = "LESS"
MORE = "MORE"

ARCH_OPPORTUNITY_LESS = "JS_OPPORTUNITY_CEILING_LESS"
ARCH_ROLE_LESS = "JS_ROLE_CEILING_LESS"
ARCH_COMPOSITE_LESS = "JS_COMPOSITE_CEILING_LESS"
ARCH_MATCHUP_LESS = "JS_MATCHUP_SUPPRESSION_LESS"
ARCH_WINDOW_LESS = "JS_WINDOW_LESS"
ARCH_GAME_SCRIPT_LESS = "JS_GAME_SCRIPT_LESS"
ARCH_DEFENSIVE_VOLUME_LESS = "JS_DEFENSIVE_VOLUME_LESS"
ARCH_FLOOR_MORE = "JS_FLOOR_MORE"
ARCH_PROMO_ANCHOR = "JS_PROMO_ANCHOR"

POSTGAME_FIELDS = {
    "actual_result", "actual_stat", "official_result", "settled_value",
    "final_value", "observed_outcome", "live_current_value",
    "current_value", "in_game_value",
}
COMPOSITE_TOKENS = (
    "PRA", "PTS+REBS+ASTS", "POINTS+REBOUNDS+ASSISTS",
    "REB+AST", "REBOUNDS+ASSISTS", "FANTASY SCORE", "FANTASY POINTS",
)
DEFENSIVE_VOLUME_TOKENS = (
    "GOALIE SAVES", "GOALKEEPER SAVES", "SAVES", "CLEARANCES",
    "TACKLES", "PASSES ATTEMPTED", "PASS ATTEMPTS", "TIME ON ICE", "TOI",
)
WINDOW_TOKENS = (
    "1H", "2H", "1ST HALF", "2ND HALF", "FIRST HALF", "SECOND HALF",
    "P1", "P2", "P3",
)
WEIGHTS = {
    "threshold_asymmetry_score": 25.0,
    "opportunity_ceiling_score": 20.0,
    "matchup_suppression_score": 15.0,
    "distribution_support_score": 15.0,
    "stat_path_robustness_score": 10.0,
    "window_fit_score": 5.0,
    "game_thesis_coherence_score": 10.0,
}


class JSStyleIntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class JSStyleAnnotation:
    js_candidate: bool
    js_archetypes: tuple[str, ...]
    js_research_priority: float
    component_scores: dict[str, float]
    threshold_burden_z: float | None
    governed_probability: None = None
    calibrated_probability: None = None
    calibrated_lower_bound: None = None
    js_probability_authority: bool = False
    probability_authority: str = PROBABILITY_AUTHORITY
    terminal_authority: str = TERMINAL_AUTHORITY
    can_execute: bool = CAN_EXECUTE
    intelligence_version: str = VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "js_candidate": self.js_candidate,
            "js_archetypes": list(self.js_archetypes),
            "js_research_priority": self.js_research_priority,
            "component_scores": dict(self.component_scores),
            "threshold_burden_z": self.threshold_burden_z,
            "governed_probability": None,
            "calibrated_probability": None,
            "calibrated_lower_bound": None,
            "js_probability_authority": False,
            "probability_authority": PROBABILITY_AUTHORITY,
            "terminal_authority": TERMINAL_AUTHORITY,
            "can_execute": False,
            "intelligence_version": VERSION,
        }


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().upper().replace("_", " ").split())


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _score01(value: Any) -> float:
    number = _num(value)
    return 0.0 if number is None else max(0.0, min(1.0, number))


def _require_pregame(row: Mapping[str, Any]) -> None:
    status = _norm(row.get("event_status") or row.get("board_status"))
    if status in {"LIVE", "STARTED", "FINAL", "FINISHED", "SETTLED"}:
        raise JSStyleIntegrityError("JS_STYLE_CURRENT_SELECTION_REQUIRES_PREGAME:" + status)
    contaminated = sorted(k for k in POSTGAME_FIELDS if row.get(k) not in (None, ""))
    if contaminated:
        raise JSStyleIntegrityError(
            "JS_STYLE_POSTGAME_OR_LIVE_FEATURE_LEAK:" + ",".join(contaminated)
        )


def threshold_burden_z(row: Mapping[str, Any]) -> float | None:
    line = _num(row.get("exact_line", row.get("line")))
    median = _num(row.get("role_adjusted_median"))
    dispersion = _num(row.get("robust_dispersion"))
    side = _norm(row.get("direction") or row.get("side"))
    if line is None or median is None or dispersion is None or dispersion <= 0:
        return None
    if side == LESS:
        return (line - median) / dispersion
    if side == MORE:
        return (median - line) / dispersion
    return None


def _threshold_score(row: Mapping[str, Any]) -> tuple[float, float | None]:
    burden = threshold_burden_z(row)
    explicit = row.get("threshold_asymmetry_score")
    if explicit not in (None, ""):
        return _score01(explicit), burden
    if burden is None:
        return 0.0, None
    return _score01(0.5 + 0.5 * burden), burden


def _archetypes(row: Mapping[str, Any], scores: Mapping[str, float]) -> tuple[str, ...]:
    side = _norm(row.get("direction") or row.get("side"))
    stat = _norm(row.get("stat_type") or row.get("prop_type") or row.get("market"))
    period = _norm(row.get("period"))
    tags: list[str] = []

    if side == LESS:
        if scores["opportunity_ceiling_score"] >= 0.60:
            tags.append(ARCH_OPPORTUNITY_LESS)
        if _score01(row.get("role_ceiling_score")) >= 0.60:
            tags.append(ARCH_ROLE_LESS)
        if any(t in stat for t in COMPOSITE_TOKENS) and (
            scores["threshold_asymmetry_score"] >= 0.50
            or scores["opportunity_ceiling_score"] >= 0.50
        ):
            tags.append(ARCH_COMPOSITE_LESS)
        if scores["matchup_suppression_score"] >= 0.60:
            tags.append(ARCH_MATCHUP_LESS)
        if any(t == period or t in period for t in WINDOW_TOKENS) and scores["window_fit_score"] >= 0.50:
            tags.append(ARCH_WINDOW_LESS)
        if _score01(row.get("game_script_dependency_score")) >= 0.60:
            tags.append(ARCH_GAME_SCRIPT_LESS)
        if any(t in stat for t in DEFENSIVE_VOLUME_TOKENS) and (
            scores["opportunity_ceiling_score"] >= 0.50
            or scores["threshold_asymmetry_score"] >= 0.50
        ):
            tags.append(ARCH_DEFENSIVE_VOLUME_LESS)
    elif side == MORE and (
        scores["threshold_asymmetry_score"] >= 0.65
        and scores["stat_path_robustness_score"] >= 0.60
    ):
        tags.append(ARCH_FLOOR_MORE)

    offer = _norm(row.get("offer_type"))
    promo_reduction = _score01(row.get("promo_line_reduction_pct"))
    if offer in {"PROMO", "DISCOUNT", "GOBLIN"} or promo_reduction >= 0.40:
        tags.append(ARCH_PROMO_ANCHOR)

    return tuple(dict.fromkeys(tags))


def classify_candidate(row: Mapping[str, Any]) -> JSStyleAnnotation:
    """Annotate one pregame prop. Output is research priority, never probability."""
    _require_pregame(row)
    threshold_score, burden = _threshold_score(row)
    scores = {
        "threshold_asymmetry_score": threshold_score,
        "opportunity_ceiling_score": _score01(row.get("opportunity_ceiling_score")),
        "matchup_suppression_score": _score01(row.get("matchup_suppression_score")),
        "distribution_support_score": _score01(row.get("distribution_support_score")),
        "stat_path_robustness_score": _score01(row.get("stat_path_robustness_score")),
        "window_fit_score": _score01(row.get("window_fit_score")),
        "game_thesis_coherence_score": _score01(row.get("game_thesis_coherence_score")),
    }
    priority = round(sum(WEIGHTS[k] * scores[k] for k in WEIGHTS), 2)
    tags = _archetypes(row, scores)
    threshold = _num(row.get("js_candidate_threshold"))
    threshold = 55.0 if threshold is None else threshold
    return JSStyleAnnotation(
        js_candidate=bool(tags) and priority >= threshold,
        js_archetypes=tags,
        js_research_priority=priority,
        component_scores=scores,
        threshold_burden_z=round(burden, 4) if burden is not None else None,
    )


def annotate_candidates(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        copy = dict(row)
        copy["js_style"] = classify_candidate(row).as_dict()
        out.append(copy)
    return out


def cluster_candidates(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Type shared game theses. No joint probability is created."""
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        event_key = str(
            row.get("canonical_event_id") or row.get("event_id") or row.get("event_key") or ""
        ).strip()
        if event_key:
            grouped.setdefault(event_key, []).append(row)

    clusters: list[dict[str, Any]] = []
    for event_key, event_rows in grouped.items():
        if len(event_rows) < 2:
            continue
        drivers = {
            str(row.get("shared_driver") or "").strip()
            for row in event_rows
            if str(row.get("shared_driver") or "").strip()
        }
        conflict = any(bool(row.get("thesis_conflict")) for row in event_rows)
        fragility = any(bool(row.get("shared_fragility")) for row in event_rows)
        if conflict:
            dependence = "THESIS_CONFLICTING"
        elif fragility:
            dependence = "SHARED_FRAGILITY"
        elif len(drivers) == 1:
            dependence = "THESIS_COHERENT"
        elif len(drivers) > 1:
            dependence = "UNRESOLVED_DEPENDENCE"
        else:
            dependence = "THESIS_NEUTRAL"

        raw = json.dumps([event_key, sorted(drivers)], separators=(",", ":")).encode()
        clusters.append({
            "cluster_thesis_id": "jscluster_" + hashlib.sha256(raw).hexdigest()[:20],
            "event_key": event_key,
            "candidate_count": len(event_rows),
            "shared_drivers": sorted(drivers),
            "dependence_type": dependence,
            "joint_probability": None,
            "independence_product_allowed": dependence == "THESIS_NEUTRAL",
            "probability_authority": PROBABILITY_AUTHORITY,
            "terminal_authority": TERMINAL_AUTHORITY,
            "can_execute": False,
        })
    return clusters


def build_historical_observation(
    selection: Mapping[str, Any],
    outcome: Mapping[str, Any] | None = None,
    source_ref: str | None = None,
) -> dict[str, Any]:
    """Create an append-only learning record with pregame/outcome separation."""
    snapshot = selection.get("pregame_feature_snapshot")
    has_snapshot = isinstance(snapshot, Mapping) and bool(snapshot)
    record = {
        "schema_version": "wow.v17.js-style-learning-observation.v1",
        "identity": {
            "sport": selection.get("sport"),
            "league": selection.get("league"),
            "event_id": selection.get("event_id"),
            "player": selection.get("player"),
            "team": selection.get("team"),
            "opponent": selection.get("opponent"),
            "stat_type": selection.get("stat_type") or selection.get("prop_type"),
            "period": selection.get("period"),
            "exact_line": selection.get("exact_line", selection.get("line")),
            "direction": selection.get("direction") or selection.get("side"),
            "offer_type": selection.get("offer_type"),
            "entry_timestamp": selection.get("entry_timestamp"),
        },
        "selected_by_js": True,
        "source_ref": source_ref,
        "pregame_feature_snapshot": dict(snapshot) if has_snapshot else None,
        "pregame_snapshot_available": has_snapshot,
        "feature_replay_required": not has_snapshot,
        "outcome": dict(outcome or {}),
        "hindsight_guard": "POSTGAME_FIELDS_NEVER_FEED_SELECTION_SCORE",
        "probability_authority": PROBABILITY_AUTHORITY,
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }
    raw = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str).encode()
    record["example_id"] = "jsex_" + hashlib.sha256(raw).hexdigest()[:24]
    return record
