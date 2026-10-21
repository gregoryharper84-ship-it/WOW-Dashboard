"""Pure research-evidence gate for WOW backlog #1933.

No database I/O, model scoring, source-rights elevation or wagering capability.
A passing result means only that the read-only 2025 audit is coherent enough
to document a *candidate* staging design. It is not a storage/training grant.
"""
from __future__ import annotations

from typing import Any, Mapping

EXPECTED_PLAYER_SHA256 = "e5e0615b3d96a3eaebfaee91e55afb4a4e7fe0caf057454177bcd7d6ad4bcfc2"
EXPECTED_SCHEDULE_SHA256 = "6011ae7b41fe3dcea1572b6e508fc8af3b8e03cf063df2681eb9e9a8c19a4332"
EXPECTED_SCHEDULE_COMMIT = "a2c9f13593a311b09bfd2d00851e55dbdafb3c8d"
EXPECTED_SCHEDULE_URL = (
    "https://raw.githubusercontent.com/nflverse/nfldata/"
    + EXPECTED_SCHEDULE_COMMIT + "/data/games.csv"
)
EXPECTED_PLAYER_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "stats_player/stats_player_week_2025.csv"
)
ROUTES = (
    "PASSING_YARDS", "RUSHING_YARDS", "RECEIVING_YARDS",
    "PASSING_TDS", "RUSHING_TDS", "RECEIVING_TDS",
)
ALLOWED_RESEARCH_BLOCKERS = {"NFL_PILOT_IDENTITY_ROWS_QUARANTINED"}


def _mapping(parent: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = parent.get(key)
    return value if isinstance(value, dict) else {}


def review_research_evidence(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a claimed run receipt; never authenticate or authorize a write.

    Raw-byte hashes and CI provenance must be checked independently by the
    acquisition runner; untrusted callers cannot establish an attestation by
    presenting a JSON object with the expected fields.
    """
    issues: list[str] = []
    if receipt.get("schema") != "WOW_NFL_BACKLOG_CANARY_RECEIPT_V1" or receipt.get("season") != 2025:
        issues.append("RESEARCH_RECEIPT_WRONG_CONTRACT")
    if receipt.get("pilot_status") != "RESEARCH_ONLY" or receipt.get("source_rights_state") != "LICENSE_REVIEW_REQUIRED":
        issues.append("RESEARCH_RIGHTS_OR_STATUS_CONFLICT")
    if receipt.get("can_execute") is not False or receipt.get("probability_publishable") is not False or receipt.get("training_materialized") is not False:
        issues.append("RESEARCH_MODE_CONTRACT_CONFLICT")
    sources = _mapping(receipt, "source_manifest")
    player = _mapping(sources, "nflverse_stats_player")
    schedule = _mapping(sources, "nflverse_schedule")
    reference = _mapping(receipt, "reference")
    if (player.get("sha256") != EXPECTED_PLAYER_SHA256
            or player.get("rows") != 19422
            or player.get("url") != EXPECTED_PLAYER_URL
            or reference.get("matches_frozen_source") is not True
            or reference.get("expected_sha256") != EXPECTED_PLAYER_SHA256):
        issues.append("RESEARCH_PLAYER_SOURCE_PIN_MISMATCH")
    if (schedule.get("sha256") != EXPECTED_SCHEDULE_SHA256
            or schedule.get("url") != EXPECTED_SCHEDULE_URL
            or schedule.get("rows") != 7548
            or receipt.get("pinned_schedule_expected_sha256") != EXPECTED_SCHEDULE_SHA256):
        issues.append("RESEARCH_SCHEDULE_SOURCE_PIN_MISMATCH")
    counts = _mapping(receipt, "counts")
    quarantine = counts.get("quarantined_identity_rows")
    in_scope = counts.get("player_rows_in_season")
    joined = counts.get("joined_player_rows")
    unjoined = counts.get("held_unsettled_or_unjoined")
    if (type(quarantine) is not int or type(in_scope) is not int
            or type(joined) is not int or type(unjoined) is not int
            or quarantine != 18 or joined != 18522 or unjoined != 0
            or in_scope != joined + quarantine + unjoined
            or counts.get("finalized_games_in_scope") != 272
            or counts.get("quarantine_missing_player_id") != quarantine
            or counts.get("quarantine_missing_game_id") != 0):
        issues.append("RESEARCH_ROW_RECONCILIATION_CONFLICT")
    if (counts.get("quarantined_nonzero_stat_rows") != 0
            or counts.get("quarantined_zero_stat_rows") != 18
            or counts.get("quarantined_invalid_or_missing_stat_rows") != 0):
        issues.append("RESEARCH_OUTCOME_QUARANTINE_MATERIALITY")
    opportunities = _mapping(counts, "quarantine_opportunity_audit")
    if (opportunities.get("status") != "ZERO_OPPORTUNITY_AUDITED"
            or opportunities.get("missing_columns") != []
            or opportunities.get("nonzero_rows") != 0
            or opportunities.get("zero_rows") != 18
            or opportunities.get("invalid_or_missing_rows") != 0):
        issues.append("RESEARCH_OPPORTUNITY_QUARANTINE_MATERIALITY")
    by_route = _mapping(counts, "normalized_stat_observations")
    if any(by_route.get(route) != joined for route in ROUTES):
        issues.append("RESEARCH_STAT_COMPLETENESS_CONFLICT")
    espn = _mapping(receipt, "independent_espn_final_score_parity")
    if (espn.get("status") != "MATCHED"
            or espn.get("date") != "2025-09-07"
            or espn.get("games_in_scope") != 13
            or espn.get("matched_final_games") != 13
            or espn.get("score_conflict_games") != 0
            or espn.get("missing_or_unfinalized_games") != 0
            or espn.get("full_season_parity_established") is not False
            or espn.get("source") != "ESPN_NFL_SCOREBOARD_INDEPENDENT"
            or len(str(espn.get("source_sha256") or "")) != 64):
        issues.append("RESEARCH_INDEPENDENT_ESPN_PARITY_MISSING")
    # Only the three explicitly approved game dates count as independent
    # publisher evidence. A structurally coherent supplied JSON is not a
    # signed attestation, so these checks remain research-only.
    expected_extra = ("2025-09-14", "2025-09-21")
    extras = receipt.get("independent_espn_additional_date_parity")
    coverage = _mapping(receipt, "independent_espn_coverage")
    valid_extras = isinstance(extras, list) and len(extras) == len(expected_extra)
    total_verified = 13
    if valid_extras:
        for date, item in zip(expected_extra, extras):
            if not isinstance(item, dict):
                valid_extras = False
                break
            count = item.get("games_in_scope")
            if (item.get("date") != date or item.get("status") != "MATCHED"
                    or item.get("source") != "ESPN_NFL_SCOREBOARD_INDEPENDENT"
                    or item.get("source_url") != (
                        "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates="
                        + date.replace("-", "")
                    ) or type(count) is not int or count < 1
                    or type(item.get("matched_final_games")) is not int
                    or item.get("matched_final_games") != count
                    or item.get("missing_or_unfinalized_games") != 0
                    or item.get("score_conflict_games") != 0
                    or item.get("full_season_parity_established") is not False
                    or len(str(item.get("source_sha256") or "")) != 64):
                valid_extras = False
                break
            total_verified += count
    if not valid_extras or (
        coverage.get("status") != "MATCHED"
        or coverage.get("dates_requested") != ["2025-09-07", *expected_extra]
        or coverage.get("dates_matched") != ["2025-09-07", *expected_extra]
        or coverage.get("games_verified") != total_verified
        or coverage.get("full_season_parity_established") is not False
    ):
        issues.append("RESEARCH_MULTI_DATE_ESPN_PARITY_MISSING")
    blockers = receipt.get("blockers")
    if (not isinstance(blockers, list) or set(blockers) != ALLOWED_RESEARCH_BLOCKERS
            or receipt.get("status") != "SOURCE_VALIDATION_HOLD"):
        issues.append("RESEARCH_TERMINAL_HOLD_NOT_PRESERVED")

    # Candidate is deliberately non-authoritative: only a later, independently
    # authenticated staging importer can use verified source bytes.
    return {
        "schema": "WOW_NFL_RESEARCH_EVIDENCE_GATE_V1",
        "status": "RESEARCH_EVIDENCE_AUDIT_CANDIDATE" if not issues else "RESEARCH_EVIDENCE_INCOMPLETE",
        "issues": sorted(set(issues)),
        "audit_coherent": not issues,
        "source_scope": "2025_272_REGULAR_SEASON_GAMES",
        "independent_parity_scope": "2025-09-07_2025-09-14_2025-09-21_APPROVED_DATES_ONLY",
        "storage_write_authorized": False,
        "training_ready": False,
        "model_certified": False,
        "probability_publishable": False,
        "can_execute": False,
        "requires_independent_source_and_rights_review": True,
        "attestation_verified": False,
    }
