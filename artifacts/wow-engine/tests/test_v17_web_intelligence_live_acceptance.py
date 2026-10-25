"""Authenticated production-style ASGI acceptance without secrets or external spend."""
from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import FastAPI, Header, HTTPException

from v17.web_intelligence_fallback_routes import install_web_intelligence_fallback_routes
from v17.web_intelligence_live_acceptance import (
    make_synthetic_request, is_nonpredictive_hold, run_web_research_self_acceptance,
)
from v17.web_intelligence_sport_profiles import WEIGHT_PROFILES


def test_live_contract_smoke_routes_and_all_sport_holds():
    app = FastAPI()
    def auth(authorization: str | None = Header(default=None)):
        if authorization != "Bearer local-fixture-not-a-real-secret":
            raise HTTPException(status_code=401)
    install_web_intelligence_fallback_routes(app, auth_dependency=auth)
    async def check():
        result = await run_web_research_self_acceptance(
            logging.getLogger("test.web-research-acceptance"),
            base_url="http://local-canary.invalid",
            api_key="local-fixture-not-a-real-secret",
            delay_seconds=0,
            transport=httpx.ASGITransport(app=app),
        )
        assert result["status"] == "PASS"
        assert result["synthetic_contract_probes"] == len(WEIGHT_PROFILES) + 1
        assert result["production_model_acceptance"] is False
        assert result["can_execute"] is False
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://local-canary.invalid",
        ) as client:
            response = await client.post(
                "/v17/research/web-intelligence-fallback",
                json=make_synthetic_request("NHL"),
            )
            assert response.status_code == 401
    asyncio.run(check())


def test_missing_secret_does_not_forge_auth_or_green_canary():
    async def check():
        response = await run_web_research_self_acceptance(
            logging.getLogger("test.web-research-key"), api_key="",
            delay_seconds=0,
        )
        assert response["status"] == "BLOCKED"
        assert response["can_execute"] is False
    asyncio.run(check())


def test_valid_synthetic_hold_no_false_probability():
    request = make_synthetic_request("NBA")
    assert request["evidence"] == []
    assert request["market_quotes"] == []
    assert request["official_event_id"].startswith("WOW-SYNTHETIC-")
    assert not is_nonpredictive_hold({"status": "RESEARCH_INTEREST", "research_score": 77})
    assert not is_nonpredictive_hold({"status": "INSUFFICIENT_RESEARCH_EVIDENCE", "calibrated_probability": .51})
