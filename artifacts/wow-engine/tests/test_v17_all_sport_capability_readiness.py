from pathlib import Path

from v17.all_sport_capability_readiness import _readiness
from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS


REQUEST_DEPENDENT = {"WNBA", "NHL", "SOCCER", "TENNIS", "MMA"}


def _health(*, registered=True, scorer=True, artifact=True, certification="CERTIFIED"):
    return {
        "registered": registered,
        "scorer_resolvable": scorer,
        "model_artifact_present": artifact,
        "certification_status": certification,
    }


def test_mlb_autonomous_readiness_requires_all_core_dependencies():
    row = _readiness("MLB", _health())
    assert row["model_capability_ready"] is True
    assert row["request_scoring_path_ready"] is True
    assert row["readiness_blockers"] == []
    assert row["calibration_mode"] == "BRIDGE_OWNED"
    assert row["calibration_dependency_satisfied"] is True
    assert row["hydration_dependency_satisfied"] is True
    assert row["can_execute"] is False


def test_multisport_certification_does_not_overstate_autonomous_readiness():
    for sport in REQUEST_DEPENDENT:
        row = _readiness(sport, _health())
        assert row["request_scoring_path_ready"] is True
        assert row["model_capability_ready"] is False
        assert row["operational_readiness_status"] == "CERTIFIED_REQUEST_DEPENDENT"
        assert "SERVER_CALIBRATION_ARTIFACT_NOT_BOUND" in row["readiness_blockers"]
        assert "BACKEND_SPORT_EVIDENCE_HYDRATOR_NOT_BOUND" in row["readiness_blockers"]
        assert row["calibration_dependency_satisfied"] is False
        assert row["hydration_dependency_satisfied"] is False
        assert row["request_evidence_injection_supported"] is True
        assert row["market_probability_substitution_allowed"] is False
        assert row["generic_reasoning_substitution_allowed"] is False
        assert row["can_execute"] is False


def test_unregistered_sports_are_truthfully_unavailable_not_fake_ready():
    for sport in {"NBA", "NCAAF", "NCAAB", "PGA", "BOXING"}:
        row = _readiness(
            sport,
            _health(
                registered=False,
                scorer=False,
                artifact=False,
                certification="NOT_CERTIFIED",
            ),
        )
        assert row["model_capability_ready"] is False
        assert row["request_scoring_path_ready"] is False
        assert row["operational_readiness_status"] == "UNAVAILABLE"
        assert "TEAM_EVENT_BRIDGE_NOT_REGISTERED" in row["readiness_blockers"]
        assert "TEAM_EVENT_CERTIFICATION_NOT_ACTIVE" in row["readiness_blockers"]
        assert row["can_execute"] is False


def test_nfl_stays_fail_closed_until_certification_is_active():
    row = _readiness(
        "NFL",
        _health(certification="CANDIDATE_REGISTERED_UNCERTIFIED"),
    )
    assert row["model_capability_ready"] is False
    assert row["request_scoring_path_ready"] is False
    assert "TEAM_EVENT_CERTIFICATION_NOT_ACTIVE" in row["readiness_blockers"]
    assert row["calibration_mode"] == "BRIDGE_OWNED"
    assert row["can_execute"] is False


def test_all_cataloged_sports_have_readiness_contract_and_never_execute():
    shapes = set()
    for sport in EXPECTED_TEAM_EVENT_SPORTS:
        row = _readiness(
            sport,
            _health(
                registered=(sport in {"MLB", "NFL", *REQUEST_DEPENDENT}),
                scorer=(sport in {"MLB", "NFL", *REQUEST_DEPENDENT}),
                artifact=(sport in {"MLB", "NFL", *REQUEST_DEPENDENT}),
                certification=(
                    "CERTIFIED"
                    if sport in {"MLB", *REQUEST_DEPENDENT}
                    else "CANDIDATE_REGISTERED_UNCERTIFIED"
                    if sport == "NFL"
                    else "NOT_CERTIFIED"
                ),
            ),
        )
        shapes.add(tuple(sorted(row)))
        assert row["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
        assert row["can_execute"] is False
        if row["readiness_blockers"]:
            assert row["model_capability_ready"] is False
    assert len(shapes) == 1


def test_canonical_action_contract_exposes_sport_specific_evidence():
    root = Path(__file__).resolve().parents[1]
    schema = (root / "v17" / "openapi.wow-betting-engine.v17.yaml").read_text()
    team_event = schema.split("    TeamEventRequest:", 1)[1]
    assert "sport_specific_evidence:" in team_event
    assert "additionalProperties: true" in team_event
