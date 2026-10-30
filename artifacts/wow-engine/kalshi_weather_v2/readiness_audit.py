"""Read-only Kalshi Weather empirical-readiness preflight.

No API credentials, database drivers, writes, probability model, or trade actions.
The provided SQL supplies the authoritative datastore readback; a caller supplies
its JSON output. Weather outcomes are never inferred from non-settlement sources.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "KALSHI_WEATHER_READINESS_AUDIT_V1"
MAX_FUTURE_SKEW_SECONDS = 300
# One of three targets every ~15m => ~45m cadence. 120m is an alert,
# not permission to publish or an evidence-substitution mechanism.
DEFAULT_CAPTURE_STALE_MINUTES = 120


@dataclass(frozen=True)
class Gate:
    code: str
    state: str
    evidence: str


class InvalidSnapshot(ValueError):
    pass


def _timestamp(value: Any, field: str, as_of: datetime | None = None) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise InvalidSnapshot(f"{field}: expected offset-aware ISO timestamp or null")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidSnapshot(f"{field}: invalid timestamp") from exc
    if dt.tzinfo is None:
        raise InvalidSnapshot(f"{field}: timezone missing")
    dt = dt.astimezone(timezone.utc)
    if as_of is not None and (dt - as_of).total_seconds() > MAX_FUTURE_SKEW_SECONDS:
        raise InvalidSnapshot(f"{field}: future evidence unavailable as of decision time")
    return dt


def _count(counts: Mapping[str, Any], key: str) -> int:
    v = counts.get(key)
    if type(v) is not int or v < 0:
        raise InvalidSnapshot(f"counts.{key}: expected nonnegative integer")
    return v


def _age_minutes(now: datetime, dt: datetime | None) -> float | None:
    return None if dt is None else round(max(0.0, (now - dt).total_seconds() / 60), 2)


def evaluate(snapshot: Mapping[str, Any], *, stale_minutes: int = DEFAULT_CAPTURE_STALE_MINUTES) -> dict[str, Any]:
    """Evaluate immutable evidence freshness and empirical blockers; always fail closed.

    This is a diagnostic preflight, not calibration certification or a V17
    authorization decision. Consumers must not derive probability authority
    or enable execution from the returned `capture_health` field.
    """
    if not isinstance(snapshot, Mapping):
        raise InvalidSnapshot("snapshot: expected JSON object")
    if type(stale_minutes) is not int or not 15 <= stale_minutes <= 10080:
        raise InvalidSnapshot("stale_minutes: expected integer in [15, 10080]")
    as_of = _timestamp(snapshot.get("as_of"), "as_of")
    if as_of is None:
        raise InvalidSnapshot("as_of: required (database server time)")
    counts = snapshot.get("counts")
    latest = snapshot.get("latest")
    if not isinstance(counts, Mapping) or not isinstance(latest, Mapping):
        raise InvalidSnapshot("counts and latest: required objects")

    count_keys = (
        "rules", "rules_with_settlement_source", "sources", "predictions",
        "numeric_probability_pairs", "publishable_predictions", "calibration_profiles",
        "certified_calibration_profiles", "outcomes", "scored_outcomes",
        "market_snapshots", "friction_verified_snapshots",
    )
    c = {name: _count(counts, name) for name in count_keys}
    for subset, superset in (
        ("rules_with_settlement_source", "rules"),
        ("numeric_probability_pairs", "predictions"),
        ("publishable_predictions", "predictions"),
        ("certified_calibration_profiles", "calibration_profiles"),
        ("scored_outcomes", "outcomes"),
        ("friction_verified_snapshots", "market_snapshots"),
    ):
        if c[subset] > c[superset]:
            raise InvalidSnapshot(f"counts.{subset} exceeds counts.{superset}")

    timestamp_keys = ("rules", "sources", "predictions", "outcomes", "market_snapshots")
    times = {key: _timestamp(latest.get(key), f"latest.{key}", as_of) for key in timestamp_keys}
    for key in timestamp_keys:
        if c[key] == 0 and times[key] is not None:
            raise InvalidSnapshot(f"latest.{key} cannot exist without rows")
        if c[key] > 0 and times[key] is None:
            raise InvalidSnapshot(f"latest.{key} missing despite rows")

    ages = {key: _age_minutes(as_of, times[key]) for key in timestamp_keys}
    gates: list[Gate] = []
    for key in ("rules", "sources", "predictions", "market_snapshots"):
        age = ages[key]
        if age is None:
            gates.append(Gate(f"{key.upper()}_ABSENT", "FAIL", "No captured rows"))
        elif age > (1440 if key == "rules" else stale_minutes):
            limit = 1440 if key == "rules" else stale_minutes
            gates.append(Gate(f"{key.upper()}_STALE", "FAIL", f"Latest age {age} min exceeds {limit} min"))
        else:
            gates.append(Gate(f"{key.upper()}_RECENT", "PASS", f"Latest age {age} min"))

    if c["rules"] == 0 or c["rules_with_settlement_source"] != c["rules"]:
        gates.append(Gate("SETTLEMENT_SOURCE_COVERAGE_INCOMPLETE", "FAIL", f"{c['rules_with_settlement_source']}/{c['rules']} rules include named settlement source"))
    else:
        # A source name in every rule is a necessary but NOT sufficient check
        # for exact station/date/rounding settlement identity.
        gates.append(Gate("SETTLEMENT_SOURCE_NAMES_PRESENT", "INFORMATIONAL", f"{c['rules']} named; individual rules still require exact-source verification"))

    if c["outcomes"] > 0 and ages["outcomes"] is not None and ages["outcomes"] > 7 * 1440:
        gates.append(Gate("SETTLEMENT_RECONCILIATION_OLD_REVIEW", "REVIEW", "Last persisted settlement older than 7 days; confirm eligible unresolved contracts before diagnosing missing grading"))
    if c["outcomes"] == 0:
        gates.append(Gate("NO_SETTLED_OUTCOMES", "FAIL", "Exact-source outcome ledger empty"))
    if c["scored_outcomes"] == 0:
        gates.append(Gate("NO_SCORED_OUTCOMES", "FAIL", "No outcome records with Brier score"))
    if c["certified_calibration_profiles"] == 0:
        gates.append(Gate("NO_CERTIFIED_CALIBRATION", "FAIL", "No certified station/lane/lead-time profile"))
    if c["numeric_probability_pairs"] == 0:
        gates.append(Gate("NO_INDEPENDENT_NUMERIC_PROBABILITIES", "FAIL", "No complete numeric P(YES)/P(NO) records"))
    if c["friction_verified_snapshots"] == 0:
        gates.append(Gate("NO_VERIFIED_MARKET_FRICTION", "FAIL", "No market snapshots with validated fees/friction"))
    if c["publishable_predictions"] == 0:
        gates.append(Gate("NO_PUBLISHABLE_PREDICTIONS", "FAIL", "V17 weather publication evidence absent"))
    else:
        # Do NOT certify on the strength of database flags alone.
        gates.append(Gate("PUBLISHABLE_FLAG_PRESENT_REQUIRES_V17_PROOF", "INFORMATIONAL", "Separate immutable exact-head certification and V17 terminal verification required"))

    capture_ok = (ages["rules"] is not None and ages["rules"] <= 1440 and
                  all(ages[key] is not None and ages[key] <= stale_minutes for key in ("sources", "predictions", "market_snapshots")))
    return {
        "schema": SCHEMA,
        "as_of": as_of.isoformat(),
        "capture_health": "RECENT_SHADOW_EVIDENCE" if capture_ok else "CAPTURE_STALE_OR_ABSENT",
        "empirical_readiness": "UNVERIFIED_NO_PLAY",
        "capability_status": "NOT_ATTESTED_BY_THIS_TOOL",
        "probability_publishable": False,
        "edge_publishable": False,
        "can_execute": False,
        "ages_minutes": ages,
        "counts": c,
        "gates": [asdict(gate) for gate in gates],
        "limits": [
            "Diagnostic only: no source authority, exact station, timezone, rounding or contract rule was independently verified here",
            "Fresh source rows do not guarantee fresh observations; use issued_at/evidence_time when validating forecasts",
            "Historical outcomes, calibration certification, and V17 publication require independent authoritative verification",
            "No publication or trading authorization can be derived from this report",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail-closed read-only Kalshi Weather empirical preflight")
    parser.add_argument("--input", required=True, help="JSON export of read-only weather_readiness_snapshot.sql result")
    parser.add_argument("--stale-minutes", type=int, default=DEFAULT_CAPTURE_STALE_MINUTES)
    args = parser.parse_args(argv)
    try:
        snapshot = json.loads(Path(args.input).read_text(encoding="utf-8"))
        if isinstance(snapshot, list):
            if len(snapshot) != 1:
                raise InvalidSnapshot("expected a single SQL result row")
            snapshot = snapshot[0]
        if isinstance(snapshot, dict) and "report" in snapshot:
            snapshot = snapshot["report"]
        report = evaluate(snapshot, stale_minutes=args.stale_minutes)
    except (ValueError, OSError, TypeError) as exc:
        print(json.dumps({"schema": SCHEMA, "error": str(exc), "capture_health": "UNVERIFIED", "probability_publishable": False, "can_execute": False},sort_keys=True))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["capture_health"] == "RECENT_SHADOW_EVIDENCE" else 1


if __name__ == "__main__":
    sys.exit(main())
