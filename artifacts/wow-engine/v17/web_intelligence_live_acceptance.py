"""Once-per-deploy authenticated HTTP smoke for WOW V17 research fallback.

Only synthetic contract probes; never proof of real-source sporting intelligence.
Reads the existing WOW_ACTION_API_KEY from process env, never logs it or a body.
No new creds, paid source call, model inference, persistence, or wagers.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from v17.web_intelligence_sport_profiles import WEIGHT_PROFILES, MARKETS

CAN_EXECUTE = False
_PROBE_CODES = frozenset({"INSUFFICIENT_RESEARCH_EVIDENCE"})
DEFAULT_SERVICE_URL = "https://wow-governed-probability-engine.onrender.com"
ENDPOINT = "/v17/research/web-intelligence-fallback"
BRIDGE_ENDPOINT = ENDPOINT + "/from-scout"


def make_synthetic_request(sport: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Event is fictitious and uniquely named; zero evidence prevents a score."""
    if sport not in WEIGHT_PROFILES:
        raise ValueError("SPORT_PROFILE_UNSUPPORTED")
    now = now or datetime.now(timezone.utc)
    return {
        "sport": sport,
        "official_event_id": "WOW-SYNTHETIC-RESEARCH-SMOKE-NONSPORTING",
        "event_start_time_utc": (now + timedelta(days=1)).isoformat(),
        "market_type": next((name for name in ("MONEYLINE", "MATCH_WINNER", "THREE_WAY_1X2", "OUTRIGHT") if name in MARKETS[sport]), "TOTAL"),
        "selection": "SYNTHETIC_SIDE",
        "line": None,
        "model_status": "MODEL_UNAVAILABLE",
        "evidence": [],
        "market_quotes": [],
    }


def is_nonpredictive_hold(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return (
        payload.get("status") in _PROBE_CODES
        and payload.get("research_score") is None
        and payload.get("model_probability") is None
        and payload.get("raw_model_probability") is None
        and payload.get("calibrated_probability") is None
        and payload.get("calibrated_lower_bound") is None
        and payload.get("terminal_label") is None
        and payload.get("probability_publishable") is False
        and payload.get("rank_eligible") is False
        and payload.get("can_execute") is False
    )


async def run_web_research_self_acceptance(
    logger: logging.Logger,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    delay_seconds: float = 10,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    await asyncio.sleep(max(0, min(float(delay_seconds), 60)))
    secret = (api_key if api_key is not None else os.getenv("WOW_ACTION_API_KEY", "")).strip()
    if not secret:
        logger.warning("WOW_WEB_RESEARCH_LIVE_CANARY status=BLOCKED code=ACTION_CREDENTIAL_NOT_CONFIGURED can_execute=false")
        return {"status": "BLOCKED", "code": "ACTION_CREDENTIAL_NOT_CONFIGURED", "can_execute": False}
    url = (base_url or os.getenv("RENDER_EXTERNAL_URL") or DEFAULT_SERVICE_URL).strip().rstrip("/")
    probes: list[tuple[str, dict[str, Any]]] = []
    for sport in WEIGHT_PROFILES:
        probes.append((ENDPOINT, make_synthetic_request(sport)))
    probes.append((BRIDGE_ENDPOINT, {
        "candidate": {
            "sport_key": "basketball_nba",
            "official_event_id": "WOW-SYNTHETIC-RESEARCH-SMOKE-NONSPORTING",
            "commence_time": (datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),
            "research_domain_evidence": {},
        },
        "model_status": "MODEL_UNAVAILABLE",
        "market_type": "MONEYLINE",
        "selection": "SYNTHETIC_SIDE",
    }))
    passed = 0
    code = "PASS"
    try:
        async with httpx.AsyncClient(
            timeout=15.0, transport=transport, follow_redirects=False,
        ) as client:
            for path, body in probes:
                response = await client.post(
                    url + path, headers={"Authorization": "Bearer " + secret},
                    json=body,
                )
                if response.status_code != 200:
                    code = "HTTP_" + str(response.status_code)
                    break
                if not is_nonpredictive_hold(response.json()):
                    code = "RESEARCH_NONPREDICTIVE_CONTRACT_FAILED"
                    break
                passed += 1
    except (httpx.HTTPError, ValueError) as exc:
        code = "HTTP_ACCEPTANCE_" + type(exc).__name__.upper()
    status = "PASS" if passed == len(probes) and code == "PASS" else "FAIL"
    logger.warning(
        "WOW_WEB_RESEARCH_LIVE_CANARY status=%s code=%s synthetic_contract_probes=%s/%s "
        "sport_profiles=%s production_model_acceptance=false can_execute=false",
        status, code, passed, len(probes), len(WEIGHT_PROFILES),
    )
    return {
        "status": status,
        "code": code,
        "synthetic_contract_probes": passed,
        "expected_probes": len(probes),
        "sport_profiles": len(WEIGHT_PROFILES),
        "production_model_acceptance": False,
        "can_execute": False,
    }
