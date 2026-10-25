"""Authenticated, research-only V17 web evidence fallback endpoint."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict, Field

from v17.web_intelligence_fallback import research_fallback

CAN_EXECUTE = False


class ResearchEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    factor: Literal["TEAM_STRENGTH", "INJURIES_LINEUPS", "OPPONENT_MATCHUP", "PACE_GAME_SCRIPT", "VENUE_FATIGUE"]
    favorability: float = Field(ge=0, le=1, allow_inf_nan=False)
    source_group: str = Field(min_length=1, max_length=128)
    source_class: str = Field(min_length=1, max_length=40)
    url: str = Field(min_length=8, max_length=2048)
    claim: str = Field(min_length=1, max_length=1000)
    observed_at: str
    confirmed: bool = False
    evidence_type: str | None = None


class ResearchMarketQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1)
    market_type: Literal["MONEYLINE", "SPREAD", "TOTAL"]
    selection: str = Field(min_length=1)
    line: float | None = None
    american_odds: float = Field(allow_inf_nan=False)
    observed_at: str
    book: str | None = None


class WebIntelligenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sport: str
    official_event_id: str
    event_start_time_utc: str
    market_type: Literal["MONEYLINE", "SPREAD", "TOTAL"]
    selection: str
    line: float | None = None
    model_status: str
    evidence: list[ResearchEvidence] = Field(default_factory=list, max_length=100)
    market_quotes: list[ResearchMarketQuote] = Field(default_factory=list, max_length=25)


def install_web_intelligence_fallback_routes(
    app: FastAPI, *, auth_dependency: Any
) -> bool:
    if getattr(app.state, "v17_web_intelligence_fallback_installed", False):
        return True
    # Never mount without authentication; no public route even though read-only.
    if not callable(auth_dependency):
        raise ValueError("authenticated dependency required")

    @app.post(
        "/v17/research/web-intelligence-fallback",
        operation_id="scoreV17WebIntelligenceResearchFallback",
        dependencies=[Depends(auth_dependency)],
    )
    def web_intelligence_fallback(request: WebIntelligenceRequest) -> dict[str, Any]:
        return research_fallback(request.model_dump())

    app.state.v17_web_intelligence_fallback_installed = True
    return True


__all__ = ["WebIntelligenceRequest", "install_web_intelligence_fallback_routes", "CAN_EXECUTE"]
