"""Immutable Weather V17 prediction/outcome ledger.

Uses the existing DATABASE_URL. Prediction rows are append-only; settlement is
stored in a separate outcome table so pregame/intraday probability artifacts are
never rewritten after the fact.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any

PREDICTION_DDL = """
CREATE TABLE IF NOT EXISTS kalshi_weather_v17_predictions (
    prediction_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_key TEXT NOT NULL,
    market_ticker TEXT,
    series TEXT,
    city_code TEXT,
    station_id TEXT NOT NULL,
    settlement_date DATE,
    contract_json JSONB NOT NULL,
    model_version TEXT NOT NULL,
    registry_version TEXT,
    evidence_digest TEXT NOT NULL,
    raw_probability NUMERIC NOT NULL,
    calibrated_probability NUMERIC,
    calibrated_lower_bound NUMERIC,
    calibrated_upper_bound NUMERIC,
    final_high_pmf JSONB NOT NULL,
    regime_probabilities JSONB NOT NULL,
    component_models JSONB NOT NULL,
    maximum_observed_so_far_f NUMERIC,
    calibration_status TEXT,
    probability_status TEXT NOT NULL,
    package_json JSONB NOT NULL,
    can_execute BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS weather_v17_event_idx ON kalshi_weather_v17_predictions(event_key);
CREATE INDEX IF NOT EXISTS weather_v17_station_date_idx ON kalshi_weather_v17_predictions(station_id, settlement_date);
"""

OUTCOME_DDL = """
CREATE TABLE IF NOT EXISTS kalshi_weather_v17_outcomes (
    prediction_id TEXT PRIMARY KEY REFERENCES kalshi_weather_v17_predictions(prediction_id),
    settled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    official_final_high_f NUMERIC NOT NULL,
    contract_result TEXT,
    settlement_source TEXT NOT NULL,
    settlement_digest TEXT NOT NULL,
    observed_path JSONB,
    brier_score NUMERIC,
    log_loss NUMERIC,
    process_classification TEXT
);
"""


def _conn():
    import psycopg2
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(url, connect_timeout=10)


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(_canon(value).encode("utf-8")).hexdigest()


def ensure_tables() -> None:
    conn = _conn(); cur = conn.cursor()
    try:
        cur.execute(PREDICTION_DDL); cur.execute(OUTCOME_DDL); conn.commit()
    finally:
        cur.close(); conn.close()


def append_prediction(row: dict[str, Any]) -> dict[str, Any]:
    """Append exactly one immutable probability package; duplicate ID is rejected."""
    package = dict(row.get("package") or row)
    if package.get("probability_status") != "COMPLETED":
        raise ValueError("WEATHER_PREDICTION_NOT_COMPLETED")
    if package.get("can_execute") is not False:
        raise ValueError("WEATHER_EXECUTION_CONTRACT_VIOLATION")
    prediction_id = str(row.get("prediction_id") or "").strip()
    event_key = str(row.get("event_key") or "").strip()
    if not prediction_id or not event_key:
        raise ValueError("WEATHER_LEDGER_IDENTITY_MISSING")
    evidence = row.get("evidence") or {"component_models": package.get("component_models"), "scored_at": package.get("scored_at")}
    ensure_tables(); conn = _conn(); cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO kalshi_weather_v17_predictions (
                prediction_id,event_key,market_ticker,series,city_code,station_id,settlement_date,
                contract_json,model_version,registry_version,evidence_digest,raw_probability,
                calibrated_probability,calibrated_lower_bound,calibrated_upper_bound,final_high_pmf,
                regime_probabilities,component_models,maximum_observed_so_far_f,calibration_status,
                probability_status,package_json,can_execute
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s::jsonb,FALSE)
            ON CONFLICT (prediction_id) DO NOTHING RETURNING prediction_id
        """, (
            prediction_id,event_key,row.get("market_ticker"),row.get("series"),row.get("city_code"),package.get("station_id"),row.get("settlement_date"),
            _canon(package.get("contract") or {}),row.get("model_version","WOW_KALSHI_WEATHER_V17"),row.get("registry_version"),digest(evidence),package.get("raw_probability"),
            package.get("calibrated_probability"),package.get("calibrated_lower_bound"),package.get("calibrated_upper_bound"),_canon(package.get("final_high_pmf") or {}),
            _canon(package.get("regime_probabilities") or {}),_canon(package.get("component_models") or []),package.get("observed_maximum_so_far_f"),package.get("calibration_status"),
            package.get("probability_status"),_canon(package),
        ))
        inserted = cur.fetchone(); conn.commit()
        if not inserted: raise ValueError("WEATHER_PREDICTION_ID_ALREADY_EXISTS")
        return {"ok": True, "prediction_id": prediction_id, "evidence_digest": digest(evidence)}
    finally:
        cur.close(); conn.close()


def append_outcome(row: dict[str, Any]) -> dict[str, Any]:
    prediction_id = str(row.get("prediction_id") or "").strip()
    source = str(row.get("settlement_source") or "").strip()
    if not prediction_id or not source or row.get("official_final_high_f") is None:
        raise ValueError("WEATHER_OUTCOME_REQUIRED_FIELDS_MISSING")
    settlement_evidence = row.get("settlement_evidence") or {"official_final_high_f": row["official_final_high_f"], "settlement_source": source}
    ensure_tables(); conn = _conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT raw_probability, calibrated_probability FROM kalshi_weather_v17_predictions WHERE prediction_id=%s", (prediction_id,))
        pred = cur.fetchone()
        if not pred: raise ValueError("WEATHER_PREDICTION_NOT_FOUND")
        result = str(row.get("contract_result") or "").upper()
        y = 1 if result in {"YES","WIN"} else 0 if result in {"NO","LOSS"} else None
        p = float(pred[1] if pred[1] is not None else pred[0])
        brier = (p-y)**2 if y is not None else None
        import math
        clipped=max(1e-9,min(1-1e-9,p)); log_loss=-(y*math.log(clipped)+(1-y)*math.log(1-clipped)) if y is not None else None
        cur.execute("""
            INSERT INTO kalshi_weather_v17_outcomes (
                prediction_id,official_final_high_f,contract_result,settlement_source,settlement_digest,
                observed_path,brier_score,log_loss,process_classification
            ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
            ON CONFLICT (prediction_id) DO NOTHING RETURNING prediction_id
        """, (prediction_id,row["official_final_high_f"],result or None,source,digest(settlement_evidence),_canon(row.get("observed_path") or []),brier,log_loss,row.get("process_classification")))
        inserted=cur.fetchone(); conn.commit()
        if not inserted: raise ValueError("WEATHER_OUTCOME_ALREADY_EXISTS")
        return {"ok":True,"prediction_id":prediction_id,"brier_score":brier,"log_loss":log_loss,"settlement_digest":digest(settlement_evidence)}
    finally:
        cur.close(); conn.close()
