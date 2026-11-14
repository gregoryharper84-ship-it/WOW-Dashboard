"""Exact-event NFL Anytime-TD source registry and authorization-held adapter.

Historical backfill only for two preapproved 2026 Week 6 games. Public source
fetch and scoring proposal allowed; NEVER invoke settlement persistence from
this adapter without separate certified QA/release authority. Can execute false.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping

import httpx
from v17.nfl_atd_official_settlement_proposals import propose_official_nfl_atd

CAN_EXECUTE = False
MAX_PDF_BYTES = 3_000_000
SOURCE_REGISTRY = {
    "2026_06_CAR_PHI":"https://www.nflgsis.com/2026/REG/06/60264/Gamebook.pdf",
    "2026_06_CHI_ATL":"https://static.www.nfl.com/image/upload/v1792409927/gamecenter/aab577eb-4feb-11f1-abca-2c54536568a9.pdf",
}

def prepare_exact_nfl_td_official_outcome(
    prediction: Mapping[str, Any], snapshot: Mapping[str, Any], *,
    http_get: Callable[..., Any] = httpx.get,
    now: datetime,
) -> dict[str, Any]:
    event_id = str(prediction.get("event_id") or "")
    url=SOURCE_REGISTRY.get(event_id)
    if not url:
        return {"status":"HELD","blocker":"PINNED_OFFICIAL_NFL_GAMEBOOK_NOT_REGISTERED","can_execute":False}
    try:
        response=http_get(url,timeout=15,follow_redirects=False)
        if int(getattr(response,"status_code",0))!=200:
            raise RuntimeError("OFFICIAL_PDF_HTTP_NOT_200")
        if str(getattr(response,"url",""))!=url:
            raise RuntimeError("OFFICIAL_PDF_REDIRECT_OR_SOURCE_MISMATCH")
        document=response.content
        if not isinstance(document,bytes) or not document.startswith(b"%PDF-") or len(document)>MAX_PDF_BYTES:
            raise ValueError("OFFICIAL_PDF_INVALID")
    except Exception as exc:
        return {"status":"HELD","blocker":"OFFICIAL_PDF_FETCH_OR_PROVENANCE_FAILED",
                "error_type":type(exc).__name__,"can_execute":False}
    result=propose_official_nfl_atd(
        prediction,snapshot,gamebook_url=url,pdf_bytes=document,now=now)
    if result["status"]!="PROPOSAL_REQUIRES_INDEPENDENT_SETTLEMENT_ACCEPTANCE":
        return result
    return {
        "status":"HOLD_PENDING_PROTECTED_SETTLEMENT_ACCEPTANCE",
        "candidate_official_outcome":result,
        "settlement_write_authorized":False,
        "requires_independent_game_final_stat_and_reboot_policy_acceptance":True,
        "can_execute":False,
    }
