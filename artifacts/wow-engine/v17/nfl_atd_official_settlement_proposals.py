"""Research-only NFL Anytime-TD official statistical outcome proposals.

No writes, wager execution, calibration, or model publishing. Proposals are not
terminal betting settlements; independent certified consumer must verify DNP,
Reboot, official game-final corrections and offer terms before persistence.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import io
import re
from typing import Any, Mapping

from v17.nfl_atd_gamebook_evidence import _abbrev, _hold, verify_anytime_td_gamebook

CAN_EXECUTE = False
SCHEMA_VERSION = "NFL_ATD_OFFICIAL_SCORING_CANDIDATE_V1"


def _participation_from_final_stats(pdf_bytes: bytes, player: str) -> tuple[bool, str]:
    """Require numeric actual participation on the official final-stat page.

    A roster listing or pregame ACTIVE flag is not proof of an in-game snap.
    Unobserved offensive/return participation always holds, never presumes DNP.
    """
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    if len(reader.pages) < 2:
        return False, "FINAL_PLAYER_STATS_PAGE_MISSING"
    page = reader.pages[1].extract_text(extraction_mode="layout") or ""
    if "Final Individual Statistics" not in " ".join(page.split()):
        return False, "FINAL_PLAYER_STATS_HEADER_MISSING"
    nickname = _abbrev(player)
    if nickname is None:
        return False, "PLAYER_NAME_UNPARSEABLE"
    matches = []
    expression = re.compile(
        rf"(?<![\w.]){re.escape(nickname)}\s+(\d+)\s+(\d+)(?:\s|$)"
    )
    for line in page.splitlines():
        for match in expression.finditer(line):
            first, second = int(match.group(1)), int(match.group(2))
            if first > 0 or second > 0:
                matches.append(line)
    if not matches:
        return False, "PLAYER_IN_GAME_PARTICIPATION_NOT_PROVEN"
    return True, "POSITIVE_OFFICIAL_FINAL_GAME_STAT"


def propose_official_nfl_atd(
    prediction: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    gamebook_url: str,
    pdf_bytes: bytes,
    now: datetime,
) -> dict[str, Any]:
    """Advisory proof of the sporting score, never a live market payout."""
    proposal = verify_anytime_td_gamebook(
        prediction, snapshot, gamebook_url=gamebook_url,
        pdf_bytes=pdf_bytes, now=now,
    )
    if proposal["status"] not in ("CANDIDATE_ONLY", "HELD"):
        return _hold("OFFICIAL_SOURCE_VERIFICATION_UNEXPECTED")
    if proposal["status"] == "HELD" and proposal.get("blocker") != "PLAYER_PARTICIPATION_AND_FINAL_STATS_REQUIRED":
        return proposal
    if "candidate_touchdowns" not in proposal:
        return _hold("OFFICIAL_SCORING_EVENT_COUNT_MISSING")

    played, reason = _participation_from_final_stats(pdf_bytes, str(prediction.get("player") or ""))
    if not played:
        return _hold(reason)

    try:
        line = Decimal(str(prediction.get("line")))
        actual = Decimal(int(proposal["candidate_touchdowns"]))
    except (ValueError, InvalidOperation, TypeError):
        return _hold("EXACT_LINE_OR_TOUCHDOWN_COUNT_INVALID")
    if line != Decimal("0.5"):
        return _hold("ATD_EXACT_LINE_UNSUPPORTED")
    direction = str(prediction.get("direction") or "").upper()
    if direction not in {"MORE", "LESS"}:
        return _hold("ATD_DIRECTION_UNSUPPORTED")

    hit = bool(actual > line) if direction == "MORE" else bool(actual < line)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "PROPOSAL_REQUIRES_INDEPENDENT_SETTLEMENT_ACCEPTANCE",
        "prediction_id": str(prediction.get("prediction_id") or ""),
        "event_id": str(prediction.get("event_id") or ""),
        "player": str(prediction.get("player") or ""),
        "line": str(line),
        "direction": direction,
        "actual_stat": int(actual),
        "hypothetical_official_result": "HIT" if hit else "MISS",
        "source": "NFL_OFFICIAL_GSIS_GAMEBOOK",
        "source_url": gamebook_url,
        "source_sha256": sha256(pdf_bytes).hexdigest(),
        "participation_evidence": reason,
        "snapshot_id": str(snapshot.get("source_snapshot_id") or ""),
        "calibration_performed": False,
        "settlement_persisted": False,
        "reboot_and_dnp_contract_verified": False,
        "correction_window_certified": False,
        "can_execute": False,
    }
