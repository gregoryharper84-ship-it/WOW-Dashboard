"""Season-aware continuous-improvement and readiness control plane for WOW V17.

This module is engineering metadata only. It does not score sporting events,
produce probabilities, change calibration, publish picks, or execute wagers.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

REGISTRY_VERSION = "1.0"
CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

BOUNDED_PHASES = {
    "OFFSEASON",
    "PRESEASON",
    "OPENING_RAMP",
    "REGULAR_SEASON",
    "STRETCH_RUN",
    "POSTSEASON_PREP",
    "POSTSEASON",
    "CHAMPIONSHIP",
    "TRANSITION",
    "CALENDAR_REFRESH_REQUIRED",
}
OTHER_PHASES = {"COMPETITION_DRIVEN"}
ALL_PHASES = BOUNDED_PHASES | OTHER_PHASES

BASE_WEEK1_GATES = (
    "calendar_verified",
    "specialist_registry",
    "fitted_artifact",
    "calibration_certification",
    "source_hydration",
    "identity_roster",
    "injury_status",
    "market_settlement",
    "freshness_rules",
    "final_refresh",
    "persistence_receipts",
    "typed_failures",
    "production_acceptance",
    "observability",
)

POSTSEASON_GATES = (
    "postseason_rules",
    "postseason_source_readiness",
    "postseason_workload_context",
    "postseason_distribution_validation",
    "postseason_acceptance",
)

DEFAULT_MILESTONES = (90, 60, 30, 14, 7, 3, 1)
ALLOWED_GATE_STATUS = {"PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE", "BLOCKED"}

PHASE_FOCUS = {
    "OFFSEASON": (
        "challenger_development",
        "historical_replay",
        "counterexample_review",
        "source_tooling_upgrades",
        "coverage_gap_closure",
        "next_season_week1_readiness",
    ),
    "PRESEASON": (
        "week1_readiness",
        "roster_identity_refresh",
        "rule_change_review",
        "live_source_validation",
        "current_season_hydration_replay",
        "production_dress_rehearsal",
    ),
    "OPENING_RAMP": (
        "week1_heightened_observability",
        "identity_and_roster_drift",
        "source_freshness",
        "typed_failure_monitoring",
        "production_acceptance",
    ),
    "REGULAR_SEASON": (
        "calibration_monitoring",
        "miss_pattern_analysis",
        "feature_quality",
        "model_disagreement",
        "source_freshness",
        "reliability_and_latency",
    ),
    "STRETCH_RUN": (
        "playoff_readiness",
        "clinching_and_elimination_context",
        "rest_and_rotation_changes",
        "workload_and_minutes_changes",
        "injury_management",
        "distribution_shift_validation",
    ),
    "POSTSEASON_PREP": (
        "playoff_readiness",
        "postseason_rule_validation",
        "rotation_and_workload_compression",
        "opponent_quality_shift",
        "series_and_bracket_context",
        "postseason_source_acceptance",
    ),
    "POSTSEASON": (
        "postseason_calibration_monitoring",
        "matchup_repetition",
        "series_state_and_elimination_context",
        "rest_travel_and_rotation_compression",
        "workload_shift_monitoring",
        "counterexample_capture",
    ),
    "CHAMPIONSHIP": (
        "championship_context_validation",
        "small_sample_guardrails",
        "rest_and_workload_monitoring",
        "freshness_and_final_refresh",
        "postseason_acceptance",
    ),
    "TRANSITION": (
        "season_postmortem",
        "preserve_demonstrated_strengths",
        "recurring_miss_diagnosis",
        "challenger_backlog",
        "next_season_calendar_refresh",
    ),
    "CALENDAR_REFRESH_REQUIRED": (
        "refresh_primary_source_calendar",
        "block_stale_phase_assumptions",
        "recompute_readiness_milestones",
    ),
    "COMPETITION_DRIVEN": (
        "competition_calendar_adapter",
        "event_level_readiness",
        "competition_specific_specialists",
        "source_and_rule_refresh",
        "next_event_acceptance",
    ),
}


def _parse_date(value: str | None) -> date | None:
    if value in (None, ""):
        return None
    return date.fromisoformat(str(value))


def _days(a: date, b: date) -> int:
    return (b - a).days


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def validate_registry(registry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if registry.get("version") != REGISTRY_VERSION:
        errors.append(f"registry version must be {REGISTRY_VERSION}")

    sports = registry.get("sports")
    if not isinstance(sports, dict) or not sports:
        return errors + ["sports must be a non-empty object"]

    for sport, spec in sorted(sports.items()):
        prefix = f"sports.{sport}"
        if not isinstance(spec, dict):
            errors.append(f"{prefix} must be an object")
            continue

        kind = spec.get("calendar_kind")
        if kind not in {"bounded", "competition_driven"}:
            errors.append(f"{prefix}.calendar_kind invalid")
            continue

        if not str(spec.get("engine") or "").strip():
            errors.append(f"{prefix}.engine is required")

        sources = spec.get("calendar_sources")
        if not isinstance(sources, list) or not sources:
            errors.append(f"{prefix}.calendar_sources must be a non-empty list")
        else:
            for index, item in enumerate(sources):
                if not isinstance(item, dict):
                    errors.append(f"{prefix}.calendar_sources[{index}] must be an object")
                    continue
                if not str(item.get("url") or "").startswith("https://"):
                    errors.append(f"{prefix}.calendar_sources[{index}].url must be https")
                try:
                    _parse_date(item.get("verified_at"))
                except ValueError:
                    errors.append(f"{prefix}.calendar_sources[{index}].verified_at invalid")

        if kind == "competition_driven":
            if not str(spec.get("adapter") or "").strip():
                errors.append(f"{prefix}.adapter is required for competition_driven calendars")
            continue

        try:
            regular_start = _parse_date(spec.get("regular_season_start"))
            regular_end = _parse_date(spec.get("regular_season_end"))
            postseason_start = _parse_date(spec.get("postseason_start"))
            championship_start = _parse_date(spec.get("championship_start"))
            postseason_end = _parse_date(spec.get("postseason_end"))
            valid_through = _parse_date(spec.get("calendar_valid_through"))
        except ValueError as exc:
            errors.append(f"{prefix} has invalid ISO date: {exc}")
            continue

        if regular_start is None or regular_end is None:
            errors.append(f"{prefix} regular_season_start and regular_season_end are required")
            continue
        if regular_start > regular_end:
            errors.append(f"{prefix} regular_season_start must not exceed regular_season_end")
        if postseason_start and postseason_start < regular_end:
            errors.append(f"{prefix} postseason_start must not precede regular_season_end")
        if championship_start and postseason_start and championship_start < postseason_start:
            errors.append(f"{prefix} championship_start must not precede postseason_start")
        if postseason_end and championship_start and postseason_end < championship_start:
            errors.append(f"{prefix} postseason_end must not precede championship_start")
        if postseason_end and postseason_start and postseason_end < postseason_start:
            errors.append(f"{prefix} postseason_end must not precede postseason_start")
        if valid_through and valid_through < regular_end:
            errors.append(f"{prefix} calendar_valid_through must not precede regular_season_end")

        milestones = spec.get("readiness_milestones_days", list(DEFAULT_MILESTONES))
        if not isinstance(milestones, list) or not milestones:
            errors.append(f"{prefix}.readiness_milestones_days must be a non-empty list")
        elif any(not isinstance(x, int) or x <= 0 for x in milestones):
            errors.append(f"{prefix}.readiness_milestones_days must contain positive integers")

    return errors


def phase_for(spec: dict[str, Any], on_date: date) -> str:
    if spec["calendar_kind"] == "competition_driven":
        return "COMPETITION_DRIVEN"

    regular_start = _parse_date(spec["regular_season_start"])
    regular_end = _parse_date(spec["regular_season_end"])
    postseason_start = _parse_date(spec.get("postseason_start"))
    championship_start = _parse_date(spec.get("championship_start"))
    postseason_end = _parse_date(spec.get("postseason_end"))
    valid_through = _parse_date(spec.get("calendar_valid_through"))
    assert regular_start and regular_end

    if valid_through and on_date > valid_through:
        return "CALENDAR_REFRESH_REQUIRED"
    if postseason_end and on_date > postseason_end:
        return "TRANSITION"
    if championship_start and postseason_end and championship_start <= on_date <= postseason_end:
        return "CHAMPIONSHIP"
    if postseason_start and on_date >= postseason_start:
        return "POSTSEASON"
    if on_date > regular_end:
        return "POSTSEASON_PREP" if postseason_start else "CALENDAR_REFRESH_REQUIRED"
    if on_date < regular_start:
        days_to_start = _days(on_date, regular_start)
        preseason_days = int(spec.get("preseason_days", 30))
        return "PRESEASON" if days_to_start <= preseason_days else "OFFSEASON"

    days_since_start = _days(regular_start, on_date)
    opening_ramp_days = int(spec.get("opening_ramp_days", 14))
    if days_since_start <= opening_ramp_days:
        return "OPENING_RAMP"

    days_to_end = _days(on_date, regular_end)
    postseason_prep_days = int(spec.get("postseason_prep_days", 14))
    stretch_run_days = int(spec.get("stretch_run_days", 35))
    if days_to_end <= postseason_prep_days:
        return "POSTSEASON_PREP"
    if days_to_end <= stretch_run_days:
        return "STRETCH_RUN"
    return "REGULAR_SEASON"


def readiness_checkpoint(spec: dict[str, Any], on_date: date) -> dict[str, Any]:
    if spec["calendar_kind"] == "competition_driven":
        return {
            "days_to_regular_season_start": None,
            "active_readiness_milestone": None,
            "milestones_days": list(spec.get("readiness_milestones_days", DEFAULT_MILESTONES)),
        }

    regular_start = _parse_date(spec["regular_season_start"])
    assert regular_start
    days_to_start = _days(on_date, regular_start)
    milestones = sorted(
        set(int(x) for x in spec.get("readiness_milestones_days", DEFAULT_MILESTONES)),
        reverse=True,
    )
    active = None
    if days_to_start >= 0:
        crossed = [m for m in milestones if days_to_start <= m]
        if crossed:
            active = min(crossed)
    return {
        "days_to_regular_season_start": days_to_start,
        "active_readiness_milestone": f"T-{active}" if active is not None else None,
        "milestones_days": milestones,
    }


def required_gates_for_phase(phase: str) -> list[str]:
    gates = list(BASE_WEEK1_GATES)
    if phase in {"STRETCH_RUN", "POSTSEASON_PREP", "POSTSEASON", "CHAMPIONSHIP"}:
        gates.extend(POSTSEASON_GATES)
    if phase == "COMPETITION_DRIVEN":
        gates.extend(("competition_calendar_adapter", "event_acceptance"))
    return gates


def _gate_summary(sport: str, phase: str, state: dict[str, Any]) -> dict[str, Any]:
    sport_state = state.get("sports", {}).get(sport, {})
    gates = sport_state.get("gates", {}) if isinstance(sport_state, dict) else {}
    required = required_gates_for_phase(phase)

    normalized: dict[str, str] = {}
    for gate in required:
        status = str(gates.get(gate, "UNKNOWN")).upper()
        if status not in ALLOWED_GATE_STATUS:
            status = "UNKNOWN"
        normalized[gate] = status

    blockers = [gate for gate, status in normalized.items() if status in {"FAIL", "BLOCKED"}]
    unverified = [gate for gate, status in normalized.items() if status == "UNKNOWN"]
    passed = [gate for gate, status in normalized.items() if status in {"PASS", "NOT_APPLICABLE"}]
    ready = not blockers and not unverified and len(passed) == len(required)

    return {
        "required": required,
        "statuses": normalized,
        "blocking_gates": blockers,
        "unverified_gates": unverified,
        "ready": ready,
    }


def _priority(phase: str, checkpoint: dict[str, Any], gates: dict[str, Any]) -> str:
    if phase == "CALENDAR_REFRESH_REQUIRED":
        return "SEASON_GATE_CRITICAL"
    if phase in {"POSTSEASON_PREP", "POSTSEASON", "CHAMPIONSHIP"} and not gates["ready"]:
        return "SEASON_GATE_CRITICAL"
    if phase == "PRESEASON":
        days_to_start = checkpoint.get("days_to_regular_season_start")
        if isinstance(days_to_start, int) and days_to_start <= 14 and not gates["ready"]:
            return "SEASON_GATE_CRITICAL"
        if not gates["ready"]:
            return "SEASON_GATE_HIGH"
    if phase == "OFFSEASON":
        days_to_start = checkpoint.get("days_to_regular_season_start")
        if isinstance(days_to_start, int) and days_to_start <= 90 and not gates["ready"]:
            return "SEASON_GATE_HIGH"
        return "MODEL_FACTORY"
    if phase in {"OPENING_RAMP", "STRETCH_RUN"} and not gates["ready"]:
        return "SEASON_GATE_HIGH"
    if phase == "COMPETITION_DRIVEN" and not gates["ready"]:
        return "SEASON_GATE_HIGH"
    return "SEASON_MONITOR"


def build_board(
    registry: dict[str, Any],
    state: dict[str, Any] | None = None,
    *,
    on_date: date | None = None,
) -> dict[str, Any]:
    errors = validate_registry(registry)
    if errors:
        raise ValueError("invalid season readiness registry: " + "; ".join(errors))

    current = on_date or date.today()
    state = state or {}
    rows: list[dict[str, Any]] = []
    for sport, spec in sorted(registry["sports"].items()):
        phase = phase_for(spec, current)
        checkpoint = readiness_checkpoint(spec, current)
        gates = _gate_summary(sport, phase, state)
        rows.append(
            {
                "sport": sport,
                "engine": spec["engine"],
                "season": spec.get("season"),
                "calendar_kind": spec["calendar_kind"],
                "phase": phase,
                "priority": _priority(phase, checkpoint, gates),
                "days_to_regular_season_start": checkpoint["days_to_regular_season_start"],
                "active_readiness_milestone": checkpoint["active_readiness_milestone"],
                "focus": list(PHASE_FOCUS[phase]),
                "readiness": gates,
                "calendar_valid_through": spec.get("calendar_valid_through"),
                "calendar_sources": spec["calendar_sources"],
                "adapter": spec.get("adapter"),
            }
        )

    priority_order = {
        "SEASON_GATE_CRITICAL": 0,
        "SEASON_GATE_HIGH": 1,
        "SEASON_MONITOR": 2,
        "MODEL_FACTORY": 3,
    }
    rows.sort(key=lambda row: (priority_order[row["priority"]], row["sport"]))

    return {
        "as_of": current.isoformat(),
        "registry_version": registry["version"],
        "can_execute": CAN_EXECUTE,
        "terminal_authority": TERMINAL_AUTHORITY,
        "rows": rows,
    }


def render_markdown(board: dict[str, Any]) -> str:
    lines = [
        "# WOW V17 Season Readiness Board",
        "",
        f"- As of: {board['as_of']}",
        f"- can_execute: {str(board['can_execute']).lower()}",
        f"- terminal_authority: {board['terminal_authority']}",
        "",
        "| Sport | Phase | Priority | Start delta | Milestone | Ready | Blocking | Unverified |",
        "|---|---|---|---:|---|---|---|---|",
    ]
    for row in board["rows"]:
        readiness = row["readiness"]
        start_delta = row["days_to_regular_season_start"]
        lines.append(
            "| {sport} | {phase} | {priority} | {delta} | {milestone} | {ready} | {blocking} | {unverified} |".format(
                sport=row["sport"],
                phase=row["phase"],
                priority=row["priority"],
                delta="" if start_delta is None else start_delta,
                milestone=row["active_readiness_milestone"] or "",
                ready="YES" if readiness["ready"] else "NO",
                blocking=", ".join(readiness["blocking_gates"]) or "",
                unverified=", ".join(readiness["unverified_gates"]) or "",
            )
        )

    lines.extend(["", "## Continuous-improvement focus", ""])
    for row in board["rows"]:
        lines.append(f"- **{row['sport']} / {row['phase']}**: " + ", ".join(row["focus"]))
    lines.extend(
        [
            "",
            "> This board controls engineering priority/readiness only. It never produces or modifies sporting probabilities.",
            "",
        ]
    )
    return "\n".join(lines)


def _default_registry_path() -> Path:
    return Path(__file__).with_name("season-readiness-registry.json")


def _default_state_path() -> Path:
    return Path(__file__).with_name("season-readiness-state.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_cmd = subparsers.add_parser("validate")
    validate_cmd.add_argument("--registry", default=str(_default_registry_path()))

    board_cmd = subparsers.add_parser("board")
    board_cmd.add_argument("--registry", default=str(_default_registry_path()))
    board_cmd.add_argument("--state", default=str(_default_state_path()))
    board_cmd.add_argument("--date", dest="on_date")
    board_cmd.add_argument("--json-out")
    board_cmd.add_argument("--markdown-out")

    args = parser.parse_args(argv)
    registry = load_json(args.registry)
    errors = validate_registry(registry)
    if errors:
        for error in errors:
            print(f"ERROR {error}")
        return 1

    if args.command == "validate":
        print("season readiness registry valid")
        return 0

    state_path = Path(args.state)
    state = load_json(state_path) if state_path.exists() else {}
    on_date = _parse_date(args.on_date) if args.on_date else date.today()
    assert on_date
    board = build_board(registry, state, on_date=on_date)
    markdown = render_markdown(board)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(board, indent=2, sort_keys=True) + "\n")
    else:
        print(json.dumps(board, indent=2, sort_keys=True))

    if args.markdown_out:
        Path(args.markdown_out).write_text(markdown + "\n")
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
