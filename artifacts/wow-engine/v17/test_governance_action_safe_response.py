import json

from v17.team_event_governance_parity_route import (
    COMPACT_PROFILE,
    DETAIL_PATH,
    _compact_governance_payload,
)


def _bulky_base():
    declared = {f"LANE_{idx}": {"status": "REGISTERED", "metadata": "x" * 200} for idx in range(30)}
    return {
        "governed_probability_capability": "AVAILABLE",
        "governed_probability_status": "READY",
        "probability_publishable": False,
        "can_execute": False,
        "deployment_gates": [
            {"gate_id": "G1", "status": "PASS", "evidence": "x" * 1000},
            {"gate_id": "G2", "status": "FAIL", "evidence": "y" * 1000},
        ],
        "calibration_health": {
            "status": "PASS",
            "forward_shadow_status": "PREDICTIONS_AVAILABLE",
            "large_history": ["z" * 300] * 20,
        },
        "compute_provider": "RENDER",
        "database_provider": "SUPABASE",
        "lane_capabilities": {
            "MLB_EVENT_PROBABILITY": {
                "status": "AVAILABLE",
                "evidence": {"reason": "READY", "payload": "x" * 2500},
                "probability_publishable": False,
                "can_execute": False,
            },
            "PROP_PROBABILITY": {
                "status": "AVAILABLE",
                "evidence": {"reason": "READY", "payload": "x" * 2500},
                "declared_lanes": declared,
                "probability_publishable": False,
                "can_execute": False,
            },
        },
        "routing_contract": {
            "LLP_TEAM_BETTING_MODEL": "/score-event only for governed team/event outright-winner lanes",
            "WOW_BETTING_ENGINE_PLAYER_PROPS": "/score-prop via api_prod_market",
        },
        "arithmetic_audit": {
            "provider": "PYTHON_PRIMARY",
            "status": "READY",
            "external_transport_required": False,
            "blocks_model_probability": False,
            "debug_blob": "x" * 3000,
        },
    }


def test_compact_governance_response_stays_action_safe_without_changing_authority():
    bridge_health = {
        "MLB": {"registered": True, "debug": "x" * 1200},
        "NFL": {"registered": True, "debug": "x" * 1200},
    }
    sport_parity = {
        "MLB": {"bridge_registered": True, "model_capability_ready": True, "debug": "x" * 1200},
        "NFL": {"bridge_registered": True, "model_capability_ready": False, "debug": "x" * 1200},
    }
    prop_parity = {
        "cataloged_sports": 2,
        "sports": {
            "MLB": {"many": "x" * 1500},
            "NFL": {"many": "x" * 1500},
        },
    }

    payload = _compact_governance_payload(
        base=_bulky_base(),
        bridge_health=bridge_health,
        sport_parity=sport_parity,
        prop_parity=prop_parity,
    )

    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    assert len(encoded) < 4096
    assert payload["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert payload["can_execute"] is False
    assert payload["probability_publishable"] is None
    assert payload["probability_publishable_scope"] == "ROW_SCOPED_ONLY"
    assert payload["lane_capabilities"]["PROP_PROBABILITY"]["declared_lane_count"] == 30
    assert payload["deployment_gates"]["failed_gate_ids"] == ["G2"]
    assert payload["team_event_capability_summary"]["model_capability_ready_sport_names"] == ["MLB"]
    assert payload["transport_contract"] == {
        "profile": COMPACT_PROFILE,
        "full_diagnostics_path": DETAIL_PATH,
        "full_sport_payloads_omitted": True,
        "can_execute": False,
    }
    assert "team_event_sports" not in payload
    assert "team_event_sport_parity" not in payload
    assert "prop_sport_parity" not in payload


def test_compact_governance_never_promotes_execution_from_input():
    base = _bulky_base()
    base["can_execute"] = True
    base["probability_publishable"] = True
    payload = _compact_governance_payload(
        base=base,
        bridge_health={},
        sport_parity={},
        prop_parity={"cataloged_sports": 0, "sports": {}},
    )
    assert payload["can_execute"] is False
    assert payload["probability_publishable"] is None
    assert payload["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
