from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / "artifacts/wow-engine/v17/skills/WOW_V17_PICK_EM_SKILL.md"
AGENT_SKILL = ROOT / ".agents/skills/wow-v17-pick-em-skill/SKILL.md"
MANIFEST = ROOT / "artifacts/wow-engine/v17/skills/skill_manifest.json"
DAILY = ROOT / "artifacts/wow-engine/v17/skills/WOW_V17_DAILY_PICKS_SKILL_PACK.md"


def test_pick_em_skill_files_and_manifest_entry_exist() -> None:
    assert SKILL.is_file()
    assert AGENT_SKILL.is_file()

    manifest = json.loads(MANIFEST.read_text())
    entry = next(item for item in manifest["skills"] if item["skill_id"] == "WOW_V17_PICK_EM")
    assert entry["file"] == "WOW_V17_PICK_EM_SKILL.md"
    assert entry["owner"] == "WOW_BETTING_ENGINE"
    assert "Run Pick Em Skill" in entry["triggers"]
    assert "WOW_V17_PICK_EM" in manifest["skills"][0]["composes"]


def test_pick_em_skill_uses_durable_governed_runtime_only() -> None:
    text = SKILL.read_text()

    for token in (
        "submitWowV17NFLPickemBoard",
        "getWowV17NFLPickemRun",
        "PICKEM_BOARD_READY",
        "full_sheet_submission_ready=true",
        "blocked_event_count == 0",
        "wow.nfl-game-win-probability-expert",
        "wow.nfl-total-points-tiebreaker-specialist",
        "V17_TERMINAL_REDUCER",
        "can_execute=false",
    ):
        assert token in text

    assert "Do not use or recreate the obsolete synchronous full-board Action" in text
    assert "runWowV17NFLPickemBoard" in text
    assert "No manual pick flip." in text
    assert "No synthetic probability." in text


def test_pick_em_skill_preserves_typed_failures_and_shadow_boundary() -> None:
    text = SKILL.read_text()

    for token in (
        "ACTION_TRANSPORT_FAILURE",
        "PICKEM_TEAM_EVENT_TRANSPORT_FAILURE",
        "MODEL_SCORER_FAILED",
        "NFL_FEATURE_ASSEMBLY_FAILED",
        "MODEL_INPUTS_INSUFFICIENT",
        "MODEL_OUTPUT_INVALID",
        "MODEL_UNAVAILABLE",
        "MAX_EXPECTED_CORRECT",
        "shadow",
    ):
        assert token in text

    assert "pool popularity" in text.lower()
    assert "cannot change the production pick or probability" in text


def test_pick_em_skill_form_rendering_contract_is_exact() -> None:
    text = SKILL.read_text()

    for token in (
        "semi-transparent green highlighter",
        "write `GH` on the Name line",
        "write the governed integer tiebreaker",
        "`Total Correct` must remain blank",
        "number of highlighted selections == `expected_game_count`",
        "every highlighted team matches the terminal Pick'em card",
        "no source text, time, date, team, or row order changed",
    ):
        assert token in text


def test_daily_skill_routes_pick_em_requests_to_pick_em_skill() -> None:
    text = DAILY.read_text()
    assert "WOW_V17_PICK_EM_SKILL.md" in text
    assert "Mode F — NFL_PICK_EM" in text


def test_manifest_pick_em_invariants_are_fail_closed() -> None:
    manifest = json.loads(MANIFEST.read_text())
    invariants = manifest["invariants"]

    assert invariants["pickem_full_sheet_requires_zero_blocked_rows"] is True
    assert invariants["pickem_expected_game_count_comes_from_supplied_sheet"] is True
    assert invariants["pickem_transport_uses_durable_submit_poll"] is True
    assert invariants["pickem_form_edit_preserves_original_format"] is True
    assert invariants["pickem_image_annotation_must_reconcile_to_final_card"] is True
    assert invariants["pickem_shadow_strategy_cannot_mutate_production_pick"] is True
