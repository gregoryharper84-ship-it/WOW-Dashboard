import pytest

from v17 import multisport_prop_live_canary as subject


def test_multisport_live_canary_contract_is_four_rows_per_supported_sport():
    assert set(subject.CONFIGS) == {"NFL", "MLB", "WNBA"}
    for sport, rows in subject.CONFIGS.items():
        assert len(rows) == 4
        assert len({row["row_key"] for row in rows}) == 4
        assert all(row["sport"] == sport for row in rows)
        assert all(row["direction"] in {"MORE", "LESS"} for row in rows)
        assert all(row["platform"] == "WOW_PRODUCTION_CANARY" for row in rows)
        assert all(row["source_type"] == "NORMALIZED" for row in rows)


def test_multisport_live_canary_preserves_nonexecution():
    assert subject.ORIGIN.startswith("https://")
    assert subject.MAX_INTERACTIVE_SECONDS == 90.0
    assert subject.CONFIGS["MLB"][0]["event_id"].startswith("MLB:")
    wnba_event_id = subject.CONFIGS["WNBA"][0]["event_id"]
    league, separator, game_id = wnba_event_id.partition(":")
    assert (league, separator) == ("WNBA", ":")
    assert len(game_id) == 10 and game_id.isdigit()


def _typed_hold_payload(*, elapsed=10.0):
    return {
        "request_id": "latency-hold",
        "reconciliation_pass": True,
        "can_execute": False,
        "_canary_elapsed_seconds": elapsed,
        "rows": [
            {
                "row_key": f"r{index}",
                "terminal_status": "HELD",
                "code": "MODEL_UNAVAILABLE",
                "model_evaluated": False,
                "probability_publishable": False,
                "can_execute": False,
            }
            for index in range(1, 5)
        ],
    }


def test_typed_model_holds_can_pass_latency_acceptance_without_probability_substitution():
    result = subject._validate(
        _typed_hold_payload(),
        sport="NFL",
        require_publishable=False,
    )
    assert result["elapsed_seconds"] == 10.0
    assert result["publishable_rows"] == 0
    assert result["can_execute"] is False


def test_publishable_package_is_required_only_for_certified_golden_lane():
    with pytest.raises(AssertionError, match="no_publishable_governed_probability_package"):
        subject._validate(
            _typed_hold_payload(),
            sport="MLB",
            require_publishable=True,
        )


def test_interactive_latency_budget_fails_before_historical_transport_ceiling():
    with pytest.raises(AssertionError, match="interactive_latency_seconds"):
        subject._validate(
            _typed_hold_payload(elapsed=subject.MAX_INTERACTIVE_SECONDS + 0.001),
            sport="NFL",
        )


def test_publishable_golden_lane_requires_numeric_governed_probability_package():
    payload = _typed_hold_payload()
    payload["request_id"] = "golden-publishable"
    payload["rows"][0] = {
        "row_key": "r1",
        "terminal_status": "COMPLETED",
        "code": "MODEL_QUALIFIED",
        "model_evaluated": True,
        "probability_publishable": True,
        "model_probability": 0.72,
        "calibrated_probability": 0.69,
        "calibrated_probability_lower_bound": 0.63,
        "can_execute": False,
    }
    result = subject._validate(
        payload,
        sport="MLB",
        require_publishable=True,
    )
    assert result["publishable_rows"] == 1
    assert result["can_execute"] is False


def test_publishable_row_without_numeric_probability_is_rejected():
    payload = _typed_hold_payload()
    payload["rows"][0] = {
        "row_key": "r1",
        "terminal_status": "COMPLETED",
        "code": "MODEL_QUALIFIED",
        "model_evaluated": True,
        "probability_publishable": True,
        "model_probability": None,
        "calibrated_probability": 0.69,
        "calibrated_probability_lower_bound": 0.63,
        "can_execute": False,
    }
    with pytest.raises(AssertionError, match="raw=None"):
        subject._validate(
            payload,
            sport="MLB",
            require_publishable=True,
        )
