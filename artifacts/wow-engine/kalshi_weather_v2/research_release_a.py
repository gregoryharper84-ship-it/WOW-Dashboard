"""Release A: authenticated, read-only Miami hourly weather research.

This is not a trade recommender. All research output is held as NO_PLAY until
separate model calibration, settlement acceptance and execution gates certify it.
No provider calls or database writes occur in this module.
"""
from __future__ import annotations

import html
import math
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials


STATION = "KALSHI_WEATHER_INDEX:MIAMI"
LANE = "HOURLY_TEMPERATURE"
ROW_LIMIT = 500
PREDICTION_FRESH_MINUTES = 90
QUOTE_FRESH_MINUTES = 10
RESEARCH_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
}
_basic = HTTPBasic(auto_error=False)


class ResearchReadError(RuntimeError):
    pass


def require_browser_key(credentials: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    """Use the isolated read-only research credential, not the weather writer key."""
    configured = os.getenv("WOW_KALSHI_WEATHER_RESEARCH_KEY", "")
    if not configured:
        raise HTTPException(status_code=503, detail="RESEARCH_AUTH_NOT_CONFIGURED")
    supplied = credentials.password if credentials else ""
    username = credentials.username if credentials else ""
    if not (secrets.compare_digest(username, "weather") and
            secrets.compare_digest(supplied, configured)):
        raise HTTPException(
            status_code=401,
            detail="RESEARCH_AUTH_REQUIRED",
            headers={"WWW-Authenticate": 'Basic realm="Kalshi Weather Research", charset="UTF-8"'},
        )


def _records(client: Any, table: str, columns: str, time_field: str) -> list[Mapping[str, Any]]:
    try:
        response = (
            client.table(table).select(columns)
            .order(time_field, desc=True).limit(ROW_LIMIT).execute()
        )
        records = response.data
        if not isinstance(records, list):
            raise ValueError("INVALID_RESEARCH_LEDGER_RESPONSE")
        return [row for row in records if isinstance(row, Mapping)]
    except Exception as exc:
        raise ResearchReadError("RESEARCH_LEDGER_UNAVAILABLE") from exc


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _age_minutes(value: Any, now: datetime) -> float | None:
    stamp = _timestamp(value)
    if stamp is None:
        return None
    age = (now - stamp).total_seconds() / 60.0
    return round(age, 1) if age >= 0 else None


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def build_research_snapshot(*, client: Any, now: datetime | None = None) -> dict[str, Any]:
    """Read immutable evidence only, with explicit quality/age qualifications."""
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ValueError("NOW_MUST_BE_TIMEZONE_AWARE")
    clock = clock.astimezone(timezone.utc)

    predictions = _records(
        client, "wow_kalshi_weather_predictions",
        "prediction_id,rule_snapshot_id,ticker,decision_time,central_estimate_f,probability_status,blockers,model_payload,model_version",
        "decision_time",
    )
    outcomes = _records(
        client, "wow_kalshi_weather_outcomes",
        "prediction_id,settled_at,settled_value,settlement_source_name,yes_outcome",
        "settled_at",
    )
    rules = _records(
        client, "wow_kalshi_weather_contract_rules",
        "rule_snapshot_id,settlement_source_name,settlement_source_url", "created_at",
    )
    quotes = _records(
        client, "wow_kalshi_weather_market_microstructure_snapshots",
        "ticker,retrieved_at,market_status,yes_best_ask,no_best_ask", "retrieved_at",
    )
    outcome_by_id = {str(row.get("prediction_id")): row for row in outcomes}
    rule_by_id = {str(row.get("rule_snapshot_id")): row for row in rules}
    quote_by_ticker: dict[str, Mapping[str, Any]] = {}
    for quote in quotes:
        ticker = str(quote.get("ticker") or "")
        if ticker and ticker not in quote_by_ticker:
            quote_by_ticker[ticker] = quote

    rows: list[dict[str, Any]] = []
    unique_target_residuals: dict[tuple[str, str, str], tuple[datetime, float]] = {}
    settled_count = 0
    newest_analysis_time = None
    for prediction in predictions:
        payload = prediction.get("model_payload")
        payload = payload if isinstance(payload, Mapping) else {}
        contract = payload.get("contract")
        contract = contract if isinstance(contract, Mapping) else {}
        if (contract.get("settlement_location_code") != STATION or
                contract.get("lane") != LANE):
            continue
        ticker = str(prediction.get("ticker") or "")
        if not ticker:
            continue
        decision_time = prediction.get("decision_time")
        if newest_analysis_time is None:
            newest_analysis_time = decision_time
        outcome = outcome_by_id.get(str(prediction.get("prediction_id") or ""), {})
        rule = rule_by_id.get(str(prediction.get("rule_snapshot_id") or ""), {})
        quote = quote_by_ticker.get(ticker, {})
        forecast = _finite_float(prediction.get("central_estimate_f"))
        actual = _finite_float(outcome.get("settled_value"))
        analysis_age = _age_minutes(decision_time, clock)
        quote_age = _age_minutes(quote.get("retrieved_at"), clock)
        model_notes = payload.get("forecast")
        model_notes = model_notes if isinstance(model_notes, Mapping) else {}
        estimates = model_notes.get("estimates")
        estimates = estimates if isinstance(estimates, list) else []
        components = [
            {"model": str(e.get("model") or "")[:80], "temperature_f": _finite_float(e.get("temperature_f"))}
            for e in estimates if isinstance(e, Mapping) and _finite_float(e.get("temperature_f")) is not None
        ]
        if outcome:
            settled_count += 1
        # One independent error per index location, observation hour and lead
        # bucket, even across model versions or threshold siblings. Use the
        # earliest decision timestamp, never a later best-looking revision.
        sample_key = (STATION, str(contract.get("observation_window") or ""),
                      str(payload.get("lead_time_bucket") or ""))
        decision_stamp = _timestamp(decision_time)
        settlement_stamp = _timestamp(outcome.get("settled_at"))
        if (outcome and forecast is not None and actual is not None and
                decision_stamp and settlement_stamp and
                decision_stamp < settlement_stamp and
                sample_key[1].startswith("POINT_IN_TIME:")):
            earlier = unique_target_residuals.get(sample_key)
            if earlier is None or decision_stamp < earlier[0]:
                unique_target_residuals[sample_key] = (decision_stamp, forecast - actual)

        blockers = prediction.get("blockers")
        blockers = blockers if isinstance(blockers, list) else []
        rows.append({
            "ticker": ticker,
            "market_title": str(contract.get("market_title") or ticker)[:300],
            "yes_condition": contract.get("yes_condition"),
            "no_condition": contract.get("no_condition"),
            "target_window": contract.get("observation_window"),
            "timezone": contract.get("timezone"),
            "settlement_location_code": STATION,
            "settlement_station_id": contract.get("settlement_station_id"),
            "settlement_source": rule.get("settlement_source_name") or contract.get("settlement_source"),
            "settlement_source_url": rule.get("settlement_source_url"),
            "rounding_rule": contract.get("rounding_convention"),
            "forecast_f": forecast,
            # Decision time is not the underlying provider's forecast-issue time.
            # Never certify source freshness from analysis recency.
            "forecast_as_of": None,
            "forecast_age_minutes": None,
            "forecast_fresh": False,
            "source_forecast_freshness": "UNVERIFIED",
            "analysis_as_of": decision_time,
            "analysis_age_minutes": analysis_age,
            "analysis_recent": analysis_age is not None and analysis_age <= PREDICTION_FRESH_MINUTES,
            "forecast_components": components,
            "lead_bucket": payload.get("lead_time_bucket"),
            "settled_value_f": actual,
            "settled_at": outcome.get("settled_at"),
            "settlement_yes": outcome.get("yes_outcome") if outcome else None,
            "last_recorded_yes_ask": _finite_float(quote.get("yes_best_ask")),
            "last_recorded_no_ask": _finite_float(quote.get("no_best_ask")),
            "last_quote_at": quote.get("retrieved_at"),
            "quote_age_minutes": quote_age,
            "quote_fresh": quote_age is not None and quote_age <= QUOTE_FRESH_MINUTES,
            "quote_market_status_at_capture": quote.get("market_status"),
            "model_p_yes": None,
            "model_p_no": None,
            "raw_edge": None,
            "uncertainty_adjusted_edge": None,
            "status": "NO_PLAY_DATA_INSUFFICIENT",
            "rank_eligible": False,
            "blockers": [str(item)[:110] for item in blockers[:12]] or ["UNCERTIFIED_WEATHER_PROBABILITY"],
            "can_execute": False,
        })

    residuals = [value for _, value in unique_target_residuals.values()]
    sample_n = len(residuals)
    # Candidate support is independently counted per lead bucket. Pooling H0
    # and H1 can yield a misleading apparent candidate after only 15 + 15.
    per_bucket: dict[str, list[float]] = {}
    for (_, _, bucket), (_, residual) in unique_target_residuals.items():
        per_bucket.setdefault(bucket, []).append(residual)
    bucket_progress = {
        bucket: {
            "eligible_unique_settled_samples": len(values),
            "minimum_candidate_samples": 30,
            "remaining_to_candidate": max(0, 30 - len(values)),
            "historical_mae_f": round(sum(abs(x) for x in values) / len(values), 3),
            "historical_signed_bias_f": round(sum(values) / len(values), 3),
            "candidate_sample_count_met": len(values) >= 30,
        }
        for bucket, values in sorted(per_bucket.items())
    }
    analysis_age = _age_minutes(newest_analysis_time, clock)
    return {
        "release": "A_RESEARCH_ONLY",
        "status": "RESEARCH_AVAILABLE" if rows else "NO_PLAY_DATA_INSUFFICIENT",
        "as_of": clock.isoformat().replace("+00:00", "Z"),
        "station_scope": STATION,
        "metric": LANE,
        "settlement_identity_note": "Kalshi Miami weather index location code. Not an assertion that an individual physical station is specified.",
        # Kept as null/false for fail-closed backward compatibility: no
        # provider-issue timestamp was independently established.
        "latest_forecast_as_of": None,
        "latest_forecast_age_minutes": None,
        "collection_fresh": False,
        "source_forecast_freshness": "UNVERIFIED",
        "latest_analysis_as_of": newest_analysis_time,
        "latest_analysis_age_minutes": analysis_age,
        "analysis_recent": analysis_age is not None and analysis_age <= PREDICTION_FRESH_MINUTES,
        "loaded_forecasts": len(rows),
        "loaded_settled_forecasts": settled_count,
        "calibration": {
            "eligible_unique_settled_samples": sample_n,
            "minimum_candidate_samples_per_lead_bucket": 30,
            "minimum_forward_validation_samples_per_lead_bucket": 40,
            "remaining_to_candidate": None,  # A pooled target count is not candidate readiness.
            "by_lead_bucket": bucket_progress,
            "readiness_scope": "PER_LEAD_BUCKET_NOT_POOLED",
            "pooled_error_scope": "RESEARCH_DIAGNOSTIC_ONLY_NOT_CANDIDATE_READINESS",
            "historical_mae_f": round(sum(abs(x) for x in residuals) / sample_n, 3) if sample_n else None,
            "historical_signed_bias_f": round(sum(residuals) / sample_n, 3) if sample_n else None,
            "status": "RESEARCH_ONLY_NOT_CERTIFIED",
        },
        "predictions": rows[:20],
        "safety": {
            "model_probability_available": False,
            "edge_available": False,
            "trade_recommendations": False,
            "can_execute": False,
            "market_prices_are_last_recorded_not_live": True,
        },
    }


def _h(value: Any) -> str:
    return html.escape("—" if value is None else str(value), quote=True)


def _degrees(value: Any) -> str:
    number = _finite_float(value)
    return "—" if number is None else f"{number:.1f}°F"


def render_research_html(snapshot: Mapping[str, Any]) -> str:
    calibration = snapshot["calibration"]
    cards = []
    for entry in snapshot["predictions"]:
        components = ", ".join(
            f"{_h(item['model'])} {_degrees(item['temperature_f'])}"
            for item in entry["forecast_components"]
        ) or "No model breakdown available"
        quote = (
            f"YES {_h(entry['last_recorded_yes_ask'])} / NO {_h(entry['last_recorded_no_ask'])}"
            if entry.get("last_quote_at") else "No recorded quote"
        )
        settled = (
            f"{_degrees(entry['settled_value_f'])} ({_h(entry['settlement_source'])})"
            if entry["settled_value_f"] is not None else "Pending official outcome"
        )
        freshness = "Provider forecast issue time unknown; source freshness unverified"
        market_age = (
            f"{_h(entry['quote_age_minutes'])} min old"
            if entry["quote_age_minutes"] is not None else "age unknown"
        )
        cards.append(
            '<article class="card">'
            f'<div class="eyebrow">{_h(entry["ticker"])} · {_h(entry["lead_bucket"])}</div>'
            f'<h3>{_h(entry["market_title"])}</h3>'
            f'<p><strong>YES:</strong> {_h(entry["yes_condition"])}</p>'
            f'<p><strong>NO:</strong> {_h(entry["no_condition"])}</p>'
            f'<p><strong>Observation:</strong> {_h(entry["target_window"])} '
            f'({_h(entry["timezone"])})</p>'
            f'<p><strong>Forecast:</strong> {_degrees(entry["forecast_f"])} '
            f'· analysis recorded {_h(entry["analysis_as_of"])} · {freshness}</p>'
            f'<p><strong>Components:</strong> {components}</p>'
            f'<p><strong>Official index:</strong> {settled}</p>'
            f'<p><strong>Last observed asks:</strong> {quote}; {market_age}. '
            'These are historical recorded quotes, not executable offers.</p>'
            f'<p><strong>Source/location:</strong> {_h(entry["settlement_source"])} '
            f'/ {_h(entry["settlement_location_code"])}</p>'
            f'<p><strong>Rounding:</strong> {_h(entry["rounding_rule"])}</p>'
            '<div class="hold">NO_PLAY_DATA_INSUFFICIENT · No independent calibrated probability</div>'
            '</article>'
        )
    content = "".join(cards) if cards else '<p>No validated Miami research records available.</p>'
    latest = snapshot.get("latest_analysis_as_of")
    bucket_progress = " · ".join(
        f"{_h(bucket)}: {_h(progress['eligible_unique_settled_samples'])}/30 independent settled targets "
        f"({_h(progress['remaining_to_candidate'])} more for sample minimum; "
        f"MAE {_h(progress['historical_mae_f'])}°F; signed bias {_h(progress['historical_signed_bias_f'])}°F)"
        for bucket, progress in sorted(calibration["by_lead_bucket"].items())
    ) or "No verified lead-bucket samples"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kalshi Weather · Miami Research</title>
<style>
:root{{color-scheme:dark;font-family:system-ui,-apple-system,sans-serif;background:#101827;color:#e8edf7}}
body{{max-width:1050px;margin:auto;padding:30px 18px 75px}}
h1{{font-size:clamp(1.7rem,4vw,2.6rem);margin:.4em 0}}
p{{line-height:1.55;color:#c7d2e1}}
small,.eyebrow{{color:#94a3b8;font-size:.85rem;overflow-wrap:anywhere}}
.top{{border-bottom:1px solid #344157;padding-bottom:22px}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:12px;margin:24px 0}}
.metric,.card{{border:1px solid #334155;border-radius:14px;background:#182337;padding:20px}}
.metric b{{font-size:1.6rem;display:block;margin-top:8px}}
.card{{margin:14px 0}}.card h3{{font-size:1.15rem;overflow-wrap:anywhere}}
.card p{{font-size:.92rem;margin:10px 0}}
.hold{{background:#3a2930;color:#ffd2c7;border-radius:8px;padding:11px;font-size:.85rem;font-weight:700;margin-top:15px}}
.notice{{border-left:4px solid #f59e0b;padding:10px 15px;background:#282939;margin-top:12px}}
a{{color:#93c5fd}}
</style></head><body>
<div class="top"><small>RELEASE A · READ-ONLY RESEARCH · AUTHENTICATED</small>
<h1>Kalshi Weather · Miami hourly</h1>
<p>Contract-first forecast evidence and official index settlements. This is a research dashboard, not a list of recommended bets.</p>
<div class="notice"><strong>No live trading recommendations.</strong> Independent P(YES)/P(NO), calibration, and fees-adjusted edge are not certified. All entries are NO_PLAY.</div></div>
<div class="metrics">
<div class="metric">Forecasts loaded<b>{_h(snapshot["loaded_forecasts"])}</b></div>
<div class="metric">Unique settled targets (all buckets)<b>{_h(calibration["eligible_unique_settled_samples"])}</b></div>
<div class="metric">Pooled MAE (research only)<b>{_degrees(calibration["historical_mae_f"])}</b></div>
<div class="metric">Provider freshness verified<b>No</b></div>
</div>
<p><strong>Latest analysis time:</strong> {_h(latest)} · Provider forecast issue age: unknown · Research as of {_h(snapshot["as_of"])}</p>
<p><strong>Candidate sample progress by lead bucket:</strong> {bucket_progress}. Counts from different buckets are never pooled for candidate readiness.</p>
<p><strong>Settlement identity:</strong> {_h(snapshot["settlement_identity_note"])}</p>
<h2>Recorded contracts</h2>{content}
<small>No external scripts, orders, model probability substitution, or market-price-based predictions. Quote freshness threshold: 10 minutes. Analysis recency threshold: 90 minutes; it does not establish weather-source freshness.</small>
</body></html>"""


def install_release_a_research(app: FastAPI, db_client_fn: Callable[[], Any]) -> None:
    @app.get("/research", include_in_schema=False, dependencies=[Depends(require_browser_key)])
    def research_page():
        try:
            data = build_research_snapshot(client=db_client_fn())
        except Exception:
            raise HTTPException(status_code=503, detail="RESEARCH_LEDGER_UNAVAILABLE")
        return HTMLResponse(render_research_html(data), headers=RESEARCH_HEADERS)

    @app.get("/research.json", include_in_schema=False, dependencies=[Depends(require_browser_key)])
    def research_json():
        try:
            data = build_research_snapshot(client=db_client_fn())
        except Exception:
            raise HTTPException(status_code=503, detail="RESEARCH_LEDGER_UNAVAILABLE")
        return JSONResponse(data, headers=RESEARCH_HEADERS)
