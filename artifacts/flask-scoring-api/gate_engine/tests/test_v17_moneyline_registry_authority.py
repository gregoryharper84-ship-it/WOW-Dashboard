from gate_engine.moneyline_probability import ModelStatus, get_model_for_sport


def test_legacy_registry_cannot_create_v17_capability():
    mlb = get_model_for_sport("MLB")
    assert mlb["status"] == ModelStatus.ACTIVE
    assert mlb["model_id"] == "mlb-moneyline-logit-v1"
    assert mlb["certification_status"] == "CERTIFIED"

    uncertified = (
        "NBA", "WNBA", "ATP", "WTA", "TENNIS", "MMA", "UFC",
        "NFL", "NHL", "SOCCER", "EPL", "MLS",
    )
    for sport in uncertified:
        model = get_model_for_sport(sport)
        assert model["status"] == ModelStatus.UNAVAILABLE, sport
        assert model["model_id"] is None, sport
        assert model["declared_model_id"], sport
        assert model["certification_status"] == "UNCERTIFIED_DECLARATION_ONLY"
        assert model["authority_source"] == "V17_CERTIFIED_TEAM_EVENT_SPORTS"


def test_soccer_declaration_semantics_survive_fail_closed_authority():
    soccer = get_model_for_sport("SOCCER")
    assert soccer["status"] == ModelStatus.UNAVAILABLE
    assert soccer["output_type"] == "three_state"
    assert soccer["model_id"] is None
    assert soccer["declared_model_id"] == "soccer-1x2-multinomial-v1"


def test_unknown_sport_remains_model_unavailable():
    unknown = get_model_for_sport("UNKNOWN_SPORT")
    assert unknown["status"] == ModelStatus.UNAVAILABLE
    assert unknown["model_id"] is None
    assert unknown["certification_status"] == "NO_DECLARED_MODEL"
