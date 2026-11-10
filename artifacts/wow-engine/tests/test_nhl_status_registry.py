"""The NHL status companion registry stays tied to its emitting modules."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS_DOC = ROOT / "docs" / "nhl_status_registry.md"
FAILURE_DOC = ROOT / "docs" / "failure_codes.md"

EXPECTED_EMITTERS = {
    "NHL_CANDIDATE_PREFLIGHT_READY": "v17/nhl_candidate_preflight.py",
    "NHL_GOALIE_STATUS_UNCHANGED": "v17/nhl_candidate_preflight.py",
    "NHL_STARTER_GOALIE_INDEPENDENTLY_CONFIRMED": "v17/nhl_starter_goalie_confirmation.py",
    "NHL_PREGAME_GATE_INPUTS_COMPLETE": "v17/nhl_pregame_orchestration.py",
    "NHL_PLAYER_BOX_OUTCOME_RESEARCH_ONLY": "v17/nhl_player_box_outcome_research.py",
    "NHL_TOTALS_OFFICIAL_HISTORICAL_PROJECTION_VERIFIED": "v17/nhl_total_goals_official_fixture.py",
    "NHL_TOTALS_REAL_SOURCE_RESEARCH_FEATURES_READY": "v17/nhl_total_goals_official_fixture.py",
    "NHL_TOTALS_REGIME_FIT_INPUTS_RECONCILED_RESEARCH_ONLY": "v17/nhl_total_goals_model_bridge.py",
    "NHL_TOTALS_RETROSPECTIVE_SOURCE_LABELS_RECONCILED": "v17/nhl_total_goals_regime_source.py",
    "NHL_TOTALS_OFFICIAL_REGIME_COHORT_RECONCILED": "v17/nhl_total_goals_regime_source.py",
    "NHL_TOTALS_OFFICIAL_REGIME_PARTIAL_RESEARCH_ONLY": "v17/nhl_total_goals_regime_source.py",
    "NHL_TOTALS_PARTIAL_RESEARCH_NO_FIT": "v17/nhl_total_goals_offline_replay.py",
    "NHL_TOTALS_REAL_5SEASON_REGIME_REPLAY_RESEARCH_ONLY": "v17/nhl_total_goals_official_fitted_replay.py",
    "NHL_TOTALS_RESEARCH_MODEL_REPLAY_COMPLETED_NOT_CERTIFIED": "v17/nhl_total_goals_offline_replay.py",
    "STARTER_GOALIE_CONFIRMED_RESEARCH_ONLY": "v17/nhl_starter_goalie_confirmation.py",
    "PREGAME_EVIDENCE_COMPLETE_NOT_CERTIFICATION": "v17/nhl_pregame_orchestration.py",
    "INPUT_EVIDENCE_PASS_NOT_CERTIFICATION": "v17/nhl_pregame_evidence_boundary.py",
    "HELD": "v17/nhl_pregame_evidence_boundary.py",
    "BLOCKED": "v17/nhl_model_maintenance.py",
    "BLOCKED_TYPED": "v17/nhl_goalie_shot_runner.py",
    "CANDIDATE_BUILT": "nhl_candidate_pipeline.py",
    "CANDIDATE_EVIDENCE_UPDATED": "v17/nhl_model_maintenance.py",
    "ADVISORY_RESEARCH_ONLY": "v17/nhl_goalie_shot_challenger.py",
    "RESEARCH_REGIME_MIXTURE_FITTED_HOLD": "v17/nhl_total_goals_regime_mixture_research.py",
    "EXPERIMENT_CREATED": "v17/nhl_total_goals_challenger.py",
}


def _rows(text: str) -> dict[str, tuple[str, str, str, str]]:
    found = re.findall(
        r"^\| `([A-Z0-9_]+)` \| (.+) \| `([^`]+)` \| (.+) \| (.+) \|$", text, re.M
    )
    return {
        code: (stage, emitter, meaning, grants)
        for code, stage, emitter, meaning, grants in found
    }


def _failure_bases() -> set[str]:
    text = FAILURE_DOC.read_text()
    rows = re.findall(r"^\| `([A-Za-z0-9_:<>]+)` \|", text, re.M)
    return {row.split(":", 1)[0] for row in rows}


def test_every_disposition_is_documented():
    rows = set(_rows(STATUS_DOC.read_text()))
    missing = sorted(set(EXPECTED_EMITTERS) - rows)
    assert missing == [], missing


def test_documented_emitter_is_the_actual_emitting_module():
    rows = _rows(STATUS_DOC.read_text())
    assert len(rows) >= 25, len(rows)
    for code, expected in EXPECTED_EMITTERS.items():
        stage, emitter, meaning, grants = rows[code]
        assert emitter == expected, (code, emitter, expected)
        module = ROOT / expected
        assert module.is_file(), expected
        assert code in module.read_text(encoding="utf-8"), (code, expected)
        assert stage.strip(), code
        assert meaning.strip(), code
        assert grants.strip() == "None", code


def test_declared_emitters_are_production_modules():
    assert len(EXPECTED_EMITTERS) == len(set(EXPECTED_EMITTERS))
    for code, path in EXPECTED_EMITTERS.items():
        assert "tests" not in Path(path).parts, code
        assert (ROOT / path).is_file(), (code, path)


def test_no_phantom_dispositions():
    source = "\n".join(path.read_text() for path in (ROOT / "v17").glob("*.py"))
    source += (ROOT / "nhl_candidate_pipeline.py").read_text()
    missing = sorted(code for code in EXPECTED_EMITTERS if code not in source)
    assert missing == [], missing


def test_dispositions_and_failure_codes_stay_disjoint():
    overlap = sorted(set(EXPECTED_EMITTERS) & _failure_bases())
    assert overlap == [], overlap
