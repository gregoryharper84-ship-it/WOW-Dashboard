from v17.scout_brain_persistence import normalize_status, priority_score, stable_candidate_id


def sample():
    return {
        "sport_key": "americanfootball_ncaaf",
        "official_event_id": "evt-123",
        "route": "LLP_TEAM_BETTING_ENGINE",
        "sport_scout_team": "CFB_SCOUT_TEAM",
        "edge_classes": ["MATCHUP_EDGE", "PERSONNEL_EDGE"],
        "required_red_team_checks": ["SMALL_SAMPLE", "STALE_MARKET"],
        "market_evidence": {
            "bookmaker": "book-a",
            "market_key": "h2h",
            "outcome_name": "Example State",
            "price": -120,
        },
    }


def test_candidate_identity_is_stable():
    row = sample()
    assert stable_candidate_id(row) == stable_candidate_id(dict(row))
    assert stable_candidate_id(row).startswith("scout_")


def test_status_fails_to_watch_if_unknown():
    row = sample()
    row["research_status"] = "MODEL_QUALIFIED"
    assert normalize_status(row) == "WATCH"


def test_priority_is_research_score_not_probability():
    score = priority_score(sample())
    assert 0 <= score <= 100
    assert score != 0.5


def test_probability_never_participates_in_identity_or_priority():
    row = sample()
    cid = stable_candidate_id(row)
    score = priority_score(row)
    row["probability"] = 0.99
    row["calibrated_probability"] = 0.99
    assert stable_candidate_id(row) == cid
    assert priority_score(row) == score
