"""Cross-sport moneyline rows must use the canonical official-publication guard.

Discovery completes a row when a scorer mapping reports both publication flags.
The canonical MLB lane already clears those flags unless
``evaluate_team_event_official_publication`` accepts the package. These tests
pin the same gate on the base Daily callback and the installed parity wrapper.
They do not change fitted probabilities, calibration floors, or execution.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from v17 import daily_snapshot_runtime as runtime
from v17.team_event_sport_parity import install_cross_sport_discovery_evidence_handoff

FUTURE_DT = datetime.now(timezone.utc) + timedelta(hours=30)
SLATE_DATE = FUTURE_DT.astimezone(ZoneInfo("America/Chicago")).date().isoformat()
COMMENCE = FUTURE_DT.replace(microsecond=0).isoformat().replace("+00:00", "Z")
EVENT_ID = "2026_07_KC_BUF"


def _proven_package():
    return {
        "code": "FINAL_APPROVED",
        "terminal_label": "FINAL_APPROVED",
        "probability_publishable": True,
        "rank_eligible": True,
        "can_execute": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "llp_probability_audit_result": "PASS_PROBABILITY_AUDIT",
        "event_mutex_status": "PASS",
        "calibrated_probability": 0.62,
        "calibrated_lower_bound": 0.58,
        "llp_governance": {
            "probability_publishable": True,
            "rank_eligible": True,
            "global_terminal_reducer": "V17_TERMINAL_REDUCER",
            "can_execute": False,
            "probability_audit_result": "PASS_PROBABILITY_AUDIT",
            "event_mutex_status": "PASS",
            "postmodel_gates_status": "PASS",
            "final_gates_status": "PASS",
            "terminal_label": "FINAL_APPROVED",
        },
    }


def _incomplete_claim():
    return {
        "code": "GOVERNED_PROBABILITY_PUBLISHED",
        "model_status": "GOVERNED_PROBABILITY_PUBLISHED",
        "probability_publishable": True,
        "rank_eligible": True,
        "can_execute": False,
        "calibrated_probability": 0.61,
        "calibrated_lower_bound": 0.57,
    }


def _feed():
    served: set[str] = set()

    def fetch(family, _target=None):
        if family != "NFL" or family in served:
            return []
        served.add(family)
        return [
            {
                "id": EVENT_ID,
                "home_team": "Buffalo Bills",
                "away_team": "Kansas City Chiefs",
                "commence_time": COMMENCE,
                "status": "SCHEDULED",
            }
        ]

    return fetch


def _request():
    return SimpleNamespace(
        requested_slate_date=SLATE_DATE,
        requested_timezone="America/Chicago",
        max_team_events=6,
    )


@pytest.fixture
def isolated_bridges():
    bridges = runtime.bridge_runtime.TEAM_EVENT_BRIDGES
    original = dict(bridges)
    bridges.clear()
    bridges["NFL"] = SimpleNamespace(supported_regimes=("REGULAR_SEASON",))
    try:
        yield
    finally:
        bridges.clear()
        bridges.update(original)


@pytest.fixture
def restore_cross_sport_entrypoint():
    saved = runtime._cross_sport_moneyline_rows
    saved_flag = getattr(runtime, "_v17_cross_sport_discovery_evidence_handoff_installed", False)
    try:
        yield
    finally:
        runtime._cross_sport_moneyline_rows = saved
        runtime._v17_cross_sport_discovery_evidence_handoff_installed = saved_flag


def _score_nfl(scorer):
    original = runtime.score_team_event_request
    runtime.score_team_event_request = scorer
    try:
        rows, scan = runtime._cross_sport_moneyline_rows(
            _request(),
            run_id="v17-daily-publication-guard",
            event_api=SimpleNamespace(),
            covered_event_ids=set(),
            fetch_sport_events=_feed(),
        )
    finally:
        runtime.score_team_event_request = original
    assert rows is not None
    assert len(rows) == 1
    assert scan["reconciliation"]["row_reconciliation"] == "PASS"
    assert scan["can_execute"] is False
    return rows[0]


def _use_base_entrypoint():
    importlib.reload(runtime)
    assert runtime._cross_sport_moneyline_rows.__name__ == "_cross_sport_moneyline_rows"


def _use_installed_entrypoint():
    runtime._v17_cross_sport_discovery_evidence_handoff_installed = False
    assert install_cross_sport_discovery_evidence_handoff() is True
    assert runtime._cross_sport_moneyline_rows.__name__ == "repaired_cross_sport_moneyline_rows"


def _assert_depublished(row, *, probability, lower):
    assert row["row_status"] == "HELD"
    assert row["probability_publishable"] is False
    assert row["can_execute"] is False
    result = row["result"]
    assert result["bucket"] != "MODEL_COMPLETED"
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False
    detail = result["detail"]
    assert detail["probability_publishable"] is False
    assert detail["rank_eligible"] is False
    assert detail["can_execute"] is False
    assert detail["calibrated_probability"] == probability
    assert detail["calibrated_lower_bound"] == lower
    assert detail["official_publication_guard"]["official_publication_allowed"] is False
    assert detail["prepublication_claim"] == {
        "probability_publishable": True,
        "rank_eligible": True,
    }


def test_base_cross_sport_incomplete_claim_is_held_without_erasing_probability(
    isolated_bridges, restore_cross_sport_entrypoint
):
    _use_base_entrypoint()
    row = _score_nfl(lambda *_args, **_kwargs: _incomplete_claim())
    _assert_depublished(row, probability=0.61, lower=0.57)
    assert row["result"]["bucket"] == "OTHER_GOVERNED_HOLD"
    assert row["identity"]["official_event_id"] == EVENT_ID


def test_base_cross_sport_proven_package_stays_completed(
    isolated_bridges, restore_cross_sport_entrypoint
):
    _use_base_entrypoint()
    row = _score_nfl(lambda *_args, **_kwargs: _proven_package())
    assert row["row_status"] == "COMPLETED"
    assert row["probability_publishable"] is True
    assert row["can_execute"] is False
    detail = row["result"]["detail"]
    assert row["result"]["bucket"] == "MODEL_COMPLETED"
    assert detail["official_publication_guard"]["status"] == "PASS"
    assert detail["calibrated_probability"] == 0.62
    assert detail["calibrated_lower_bound"] == 0.58
    assert detail["can_execute"] is False


def test_base_scorer_exception_stays_scorer_failed(
    isolated_bridges, restore_cross_sport_entrypoint
):
    _use_base_entrypoint()

    def scorer(*_args, **_kwargs):
        raise RuntimeError("scorer boom")

    row = _score_nfl(scorer)
    assert row["row_status"] == "HELD"
    assert row["result"]["bucket"] == "MODEL_SCORER_FAILED"
    assert row["result"]["bucket"] != "MODEL_UNAVAILABLE"
    detail = row["result"]["detail"]
    assert detail["code"] == "MODEL_SCORER_FAILED"
    assert detail["model_invoked"] is True
    assert detail["probability_publishable"] is False
    assert "calibrated_probability" not in detail
    assert row["can_execute"] is False


def test_base_non_dict_score_is_output_invalid(
    isolated_bridges, restore_cross_sport_entrypoint
):
    _use_base_entrypoint()
    row = _score_nfl(lambda *_args, **_kwargs: ["not-a-package"])
    assert row["row_status"] == "HELD"
    assert row["result"]["bucket"] == "MODEL_OUTPUT_INVALID"
    detail = row["result"]["detail"]
    assert detail["code"] == "MODEL_OUTPUT_INVALID"
    assert detail["probability_publishable"] is False
    assert detail["rank_eligible"] is False
    assert "calibrated_probability" not in detail
    assert row["can_execute"] is False


def test_base_http_inputs_failure_is_not_relabeled_unavailable(
    isolated_bridges, restore_cross_sport_entrypoint
):
    _use_base_entrypoint()

    def scorer(*_args, **_kwargs):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "MODEL_INPUTS_INSUFFICIENT",
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            },
        )

    row = _score_nfl(scorer)
    assert row["row_status"] == "HELD"
    assert row["result"]["bucket"] == "MODEL_INPUTS_INSUFFICIENT"
    assert row["result"]["detail"]["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert row["result"]["probability_publishable"] is False
    assert row["can_execute"] is False


def test_base_http_false_publication_claim_is_depublished(
    isolated_bridges, restore_cross_sport_entrypoint
):
    _use_base_entrypoint()

    def scorer(*_args, **_kwargs):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "GOVERNED_PROBABILITY_PUBLISHED",
                "probability_publishable": True,
                "rank_eligible": True,
                "can_execute": False,
                "calibrated_probability": 0.64,
                "calibrated_lower_bound": 0.60,
            },
        )

    row = _score_nfl(scorer)
    _assert_depublished(row, probability=0.64, lower=0.60)
    assert row["result"]["detail"]["code"] == "GOVERNED_PROBABILITY_PUBLISHED"


def test_installed_parity_incomplete_claim_is_held(
    isolated_bridges, restore_cross_sport_entrypoint
):
    _use_installed_entrypoint()
    row = _score_nfl(lambda *_args, **_kwargs: _incomplete_claim())
    _assert_depublished(row, probability=0.61, lower=0.57)
    assert row["identity"]["sport"] == "NFL"


def test_installed_parity_proven_package_stays_completed(
    isolated_bridges, restore_cross_sport_entrypoint
):
    _use_installed_entrypoint()
    row = _score_nfl(lambda *_args, **_kwargs: _proven_package())
    assert row["row_status"] == "COMPLETED"
    assert row["result"]["bucket"] == "MODEL_COMPLETED"
    detail = row["result"]["detail"]
    assert detail["calibrated_probability"] == 0.62
    assert detail["calibrated_lower_bound"] == 0.58
    assert detail["official_publication_guard"]["official_publication_allowed"] is True
    assert detail["can_execute"] is False
    assert row["can_execute"] is False
