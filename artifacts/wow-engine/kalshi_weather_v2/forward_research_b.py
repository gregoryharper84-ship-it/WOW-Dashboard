"""Read-only Release B: point-in-time Miami H0 empirical probability challenger.

No credentials, network calls, writes, model promotion, or execution.
The only settlement target is the contract's Synoptic Data Miami weather
index. NWS grid guidance is a PREDICTIVE feature, never settlement authority.
Candidate probabilities stay research-only even after a sample count is met.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import math
from typing import Any, Mapping

STATION = "KALSHI_WEATHER_INDEX:MIAMI"
SETTLEMENT_SOURCE = "Synoptic Data"
ROUNDING = "USE_OFFICIAL_KALSHI_WEATHER_INDEX_AS_PUBLISHED;NO_ENGINE_ROUNDING"
MODEL_VERSION = "MIAMI_H0_NWS_EMPIRICAL_WALKFORWARD_R1"
MIN_PRIOR_SETTLED = 30
MAX_RECORDS = 2000
PAGE_SIZE = 250


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class Case:
    prediction_id: str
    ticker: str
    source: str
    index_id: str
    bucket: str
    target_at: datetime
    decision_at: datetime
    forecast_issued_at: datetime
    forecast_available_at: datetime
    nws_f: float
    threshold_f: float
    inclusive: bool
    settled_at: datetime | None = None
    official_f: float | None = None
    official_yes: bool | None = None

    @property
    def key(self) -> tuple[str, str, datetime, str]:
        return (self.source, self.index_id, self.target_at, self.bucket)

    @property
    def actual_yes(self) -> bool | None:
        if self.official_f is None:
            return None
        return self.official_f >= self.threshold_f if self.inclusive else self.official_f > self.threshold_f


def _source_rows(client: Any, table: str, fields: str, order_by: str) -> list[dict[str, Any]]:
    """Require exact table count and exhaust capped PostgREST pages without gaps.

    A server-side row cap can be smaller than PAGE_SIZE. The next offset is
    based on actual returned rows, not the requested page length. Two stable
    sort keys and unique row IDs protect page boundary consistency.
    """
    key_field = {
        "wow_kalshi_weather_predictions": "prediction_id",
        "wow_kalshi_weather_outcomes": "outcome_id",
        "wow_kalshi_weather_source_snapshots": "source_snapshot_id",
        "wow_kalshi_weather_contract_rules": "rule_snapshot_id",
    }.get(table)
    if not key_field:
        raise RuntimeError("RESEARCH_TABLE_NOT_ALLOWLISTED")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    total: int | None = None
    while total is None or len(result) < total:
        offset = len(result)
        response = (
            client.table(table)
            .select(fields, count="exact")
            .order(order_by, desc=True)
            .order(key_field, desc=True)
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        count = response.count
        rows = response.data
        if (not isinstance(count, int) or isinstance(count, bool)
                or count < 0 or count >= MAX_RECORDS
                or (total is not None and count != total)
                or not isinstance(rows, list)):
            raise RuntimeError("RESEARCH_LEDGER_COUNT_UNVERIFIED")
        total = count
        if len(rows) > PAGE_SIZE or (not rows and len(result) < total):
            raise RuntimeError("RESEARCH_LEDGER_PAGINATION_INCOMPLETE")
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError("RESEARCH_LEDGER_ROW_INVALID")
            row_key = str(row.get(key_field) or "")
            if not row_key or row_key in seen:
                raise RuntimeError("RESEARCH_LEDGER_DUPLICATE_OR_MISSING_ROW_ID")
            seen.add(row_key)
            result.append(row)
        if len(result) > total:
            raise RuntimeError("RESEARCH_LEDGER_COUNT_MISMATCH")
    if len(result) != total:
        raise RuntimeError("RESEARCH_LEDGER_COUNT_MISMATCH")
    return result


def load_cases(client: Any) -> tuple[list[Case], dict[str, int]]:
    """Build independently checkable cases from immutable DB ledgers only.

    No hidden timestamp repair. An unverified provider issue or retrieval
    timestamp excludes the record; a forecast captured after target is not
    recast as a valid ex-ante decision.
    """
    preds = _source_rows(
        client, "wow_kalshi_weather_predictions",
        "prediction_id,rule_snapshot_id,ticker,decision_time,model_payload", "decision_time",
    )
    outcomes = _source_rows(
        client, "wow_kalshi_weather_outcomes",
        "outcome_id,prediction_id,settled_at,settled_value,settlement_source_name,yes_outcome", "settled_at",
    )
    sources = _source_rows(
        client, "wow_kalshi_weather_source_snapshots",
        "source_snapshot_id,provider,source_role,issued_at,retrieved_at", "retrieved_at",
    )
    rules = _source_rows(
        client, "wow_kalshi_weather_contract_rules",
        "rule_snapshot_id,settlement_source_name", "created_at",
    )
    outcomes_by_id = {str(x["prediction_id"]): x for x in outcomes}
    if len(outcomes_by_id) != len(outcomes):
        raise RuntimeError("DUPLICATE_OUTCOME_FOR_PREDICTION")
    sources_by_id = {str(x["source_snapshot_id"]): x for x in sources}
    rules_by_id = {str(x["rule_snapshot_id"]): x for x in rules}
    counters = {
        "examined": 0, "excluded_invalid_or_unverified": 0,
        "excluded_duplicate_target": 0, "excluded_unverified_outcomes": 0,
    }
    parsed: list[Case] = []

    for row in preds:
        counters["examined"] += 1
        try:
            payload = row["model_payload"]
            contract = payload["contract"]
            forecast = payload["forecast"]
            if not isinstance(contract, dict) or not isinstance(forecast, dict):
                raise ValueError("bad payload")
            if (contract.get("lane") != "HOURLY_TEMPERATURE"
                    or contract.get("settlement_location_code") != STATION
                    or payload.get("lead_time_bucket") != "H0"
                    or contract.get("rounding_convention") != ROUNDING
                    or contract.get("settlement_source") != SETTLEMENT_SOURCE):
                raise ValueError("unsupported contract")
            rules_row = rules_by_id.get(str(row["rule_snapshot_id"]))
            if not rules_row or rules_row.get("settlement_source_name") != SETTLEMENT_SOURCE:
                raise ValueError("unverified source")
            window = contract.get("observation_window", "")
            if not isinstance(window, str) or not window.startswith("POINT_IN_TIME:"):
                raise ValueError("unverified window")
            target_at = _time(window.removeprefix("POINT_IN_TIME:"))
            decision_at = _time(row.get("decision_time"))
            threshold_f = _number(contract.get("threshold_lower"))
            if (target_at is None or decision_at is None or threshold_f is None
                    or contract.get("threshold_upper") is not None
                    or contract.get("lower_inclusive") not in (True, False)
                    or not isinstance(contract.get("yes_condition"), str)):
                raise ValueError("unverified threshold")
            inclusive = contract["lower_inclusive"]
            op = ">=" if inclusive else ">"
            expected_yes = f"index {op} {threshold_f:g}°F"
            if (contract["yes_condition"] != expected_yes
                    or contract.get("no_condition") != f"NOT ({expected_yes})"):
                raise ValueError("unverified YES/NO condition")

            estimates = forecast.get("estimates")
            nws = [e for e in estimates if isinstance(e, dict) and e.get("model") == "NWS_HOURLY"] if isinstance(estimates, list) else []
            if len(nws) != 1 or nws[0].get("target_time_utc") is None:
                raise ValueError("missing NWS predictive evidence")
            if _time(nws[0]["target_time_utc"]) != target_at:
                raise ValueError("forecast/contract target mismatch")
            nws_f = _number(nws[0].get("temperature_f"))
            source = sources_by_id.get(str(nws[0].get("source_snapshot_id")))
            if (nws_f is None or not source or source.get("provider") != "NWS"
                    or source.get("source_role") != "PRIMARY_FORECAST"):
                raise ValueError("unverified NWS source")
            issued = _time(source.get("issued_at"))
            available = _time(source.get("retrieved_at"))
            if (issued is None or available is None or not
                    (issued <= available <= decision_at < target_at)):
                raise ValueError("point-in-time source not proven")

            # Select the ex-ante prediction without consulting future settlement.
            # Outcome validation happens only after deterministic sibling dedupe.

            parsed.append(Case(
                prediction_id=str(row["prediction_id"]), ticker=str(row["ticker"]),
                source=SETTLEMENT_SOURCE, index_id=STATION, bucket="H0",
                target_at=target_at, decision_at=decision_at,
                forecast_issued_at=issued, forecast_available_at=available,
                nws_f=nws_f, threshold_f=threshold_f, inclusive=inclusive,
                settled_at=None, official_f=None, official_yes=None,
            ))
        except (KeyError, TypeError, ValueError, AttributeError):
            counters["excluded_invalid_or_unverified"] += 1

    # Multiple sibling thresholds/revisions for one index hour are one target.
    # Take the earliest decision, with stable tie-breaking (not best error).
    by_target: dict[tuple[str, str, datetime, str], Case] = {}
    for case in sorted(parsed, key=lambda c: (c.decision_at, c.prediction_id)):
        if case.key in by_target:
            counters["excluded_duplicate_target"] += 1
        else:
            by_target[case.key] = case
    resolved: list[Case] = []
    for case in sorted(by_target.values(), key=lambda c: (c.decision_at, c.prediction_id)):
        outcome = outcomes_by_id.get(case.prediction_id)
        if outcome is None:
            resolved.append(case)
            continue
        settled_at = _time(outcome.get("settled_at"))
        official_f = _number(outcome.get("settled_value"))
        official_yes = outcome.get("yes_outcome")
        if (outcome.get("settlement_source_name") != SETTLEMENT_SOURCE
                or settled_at is None or settled_at < case.target_at
                or official_f is None or not isinstance(official_yes, bool)
                or (official_f >= case.threshold_f if case.inclusive
                    else official_f > case.threshold_f) != official_yes):
            counters["excluded_unverified_outcomes"] += 1
            # Preserve immutable prediction selection; omit unverifiable grade.
            resolved.append(case)
            continue
        resolved.append(replace(
            case, settled_at=settled_at, official_f=official_f,
            official_yes=official_yes,
        ))
    return resolved, counters


def _wilson(successes: int, count: int) -> tuple[float, float]:
    """Descriptive 95% binomial interval, NOT a calibrated model bound."""
    z = 1.96
    p = successes / count
    denom = 1 + z * z / count
    center = (p + z * z / (2 * count)) / denom
    radius = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / denom
    return max(0.0, center - radius), min(1.0, center + radius)


def evaluate_walk_forward(cases: list[Case], *, min_prior: int = MIN_PRIOR_SETTLED) -> dict[str, Any]:
    """Fit only to outcomes published before each historical decision time.

    Jeffreys-smoothed empirical NWS-to-index residual distribution is a
    research challenger, not an authorized production calibration model.
    """
    if min_prior < MIN_PRIOR_SETTLED:
        raise ValueError("MINIMUM_PRIOR_SAMPLE_GATE_CANNOT_BE_WEAKENED")
    if len({c.key for c in cases}) != len(cases):
        raise ValueError("DUPLICATE_TARGETS_NOT_INDEPENDENT")

    candidates = []
    evaluated = 0
    for case in sorted(cases, key=lambda c: (c.decision_at, c.prediction_id)):
        prior = [
            old for old in cases
            if old.key != case.key
            and old.source == case.source and old.index_id == case.index_id
            and old.bucket == case.bucket
            and old.target_at < case.target_at
            and old.settled_at is not None
            and old.settled_at <= case.decision_at
            and old.official_f is not None
        ]
        if len(prior) < min_prior:
            continue
        successes = sum(
            (case.nws_f + (old.official_f - old.nws_f)) >= case.threshold_f
            if case.inclusive else
            (case.nws_f + (old.official_f - old.nws_f)) > case.threshold_f
            for old in prior
        )
        p_yes = (successes + 0.5) / (len(prior) + 1.0)
        low, high = _wilson(successes, len(prior))
        brier = (p_yes - float(case.actual_yes)) ** 2 if case.actual_yes is not None else None
        log_loss = -math.log(p_yes if case.actual_yes else (1 - p_yes)) if case.actual_yes is not None else None
        if brier is not None:
            evaluated += 1
        candidates.append({
            "ticker": case.ticker, "prediction_id": case.prediction_id,
            "decision_time": case.decision_at.isoformat(),
            "target_time": case.target_at.isoformat(),
            "model_version": MODEL_VERSION, "prior_settled_samples": len(prior),
            "shadow_p_yes": round(p_yes, 6), "shadow_p_no": round(1.0 - p_yes, 6),
            "empirical_wilson_interval_not_certified": [round(low, 6), round(high, 6)],
            "brier": round(brier, 6) if brier is not None else None,
            "log_loss": round(log_loss, 6) if log_loss is not None else None,
            "status": "NO_PLAY_DATA_INSUFFICIENT",
            "candidate_state": "RESEARCH_ONLY_NOT_CERTIFIED",
            "can_execute": False,
        })
    graded = [x for x in candidates if x["brier"] is not None]
    return {
        "model_version": MODEL_VERSION, "settlement_source": SETTLEMENT_SOURCE,
        "index_id": STATION, "lead_bucket": "H0",
        "unique_point_in_time_cases": len(cases),
        "unique_settled_cases": sum(c.actual_yes is not None for c in cases),
        "minimum_prior_settled_samples": min_prior,
        "shadow_candidate_count": len(candidates),
        "graded_shadow_candidate_count": evaluated,
        "mean_brier": round(sum(x["brier"] for x in graded) / len(graded), 6) if graded else None,
        "mean_log_loss": round(sum(x["log_loss"] for x in graded) / len(graded), 6) if graded else None,
        "candidates": candidates,
        "status": "NO_PLAY_DATA_INSUFFICIENT",
        "probability_publishable": False, "can_execute": False,
        "notes": [
            "NWS is predictive evidence, not the official Synoptic Data settlement index.",
            "Historical issue and retrieval availability are mandatory; no price inputs.",
            "30 prior distinct settled H0 targets is a research floor, not validation certification.",
            "40+ untouched forward cases and governed settlement/calibration acceptance remain required.",
        ],
    }


def build_release_b_research_report(client: Any) -> dict[str, Any]:
    cases, excluded = load_cases(client)
    return {"source_qc": excluded, **evaluate_walk_forward(cases)}
