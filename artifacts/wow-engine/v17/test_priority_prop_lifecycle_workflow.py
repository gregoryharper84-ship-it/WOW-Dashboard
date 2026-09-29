from pathlib import Path


def test_priority_prop_lifecycle_is_bounded_fail_closed_and_sport_scoped():
    repo_root = Path(__file__).resolve().parents[3]
    text = (repo_root / ".github" / "workflows" / "wow-v17-priority-prop-lifecycle.yml").read_text()

    assert 'cron: "7 * * * *"' in text
    assert 'push:' in text
    assert 'max-parallel: 1' in text
    assert 'id-token: write' in text
    assert '/v17/prop-lifecycle-autopilot-run' in text
    assert '/internal/v17/nfl-prop-forward-evidence/acquire' in text
    assert '/internal/v17/mlb-prop-forward-evidence/acquire' in text
    assert '/internal/v17/wnba-prop-forward-evidence/acquire' in text
    assert text.count('max_candidates: 8') == 3
    assert '--max-time 120' in text
    assert '--max-time 210' in text
    assert 'seed_date "${today}"' in text
    assert 'seed_date "${tomorrow}"' in text
    assert 'candidate_offset":0' in text
    assert 'seed_failures=0' in text
    assert 'lifecycle will still consume any durable rows' in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
    assert "automatic_certification" in text
    assert "automatic_promotion" in text
    assert "probability_publishable" in text
    assert "artifact_isolation_enforced" in text
    assert "improperly_promoted_route_n" in text
    assert '"NFL:PASSING_YARDS"' in text
    assert '"NFL:RUSHING_YARDS"' in text
    assert '"NFL:RECEIVING_YARDS"' in text
    assert '"NFL:ANYTIME_TD"' in text
    assert '"MLB:PITCHER_STRIKEOUTS"' in text
    assert '"MLB:PITCHING_OUTS"' in text
    assert '"MLB:STRIKES_THROWN"' in text
    assert '"MLB:BALLS_THROWN"' in text
    assert '"WNBA:POINTS"' in text
    assert '"WNBA:REBOUNDS"' in text
    assert '"WNBA:ASSISTS"' in text
    assert '"WNBA:THREE_POINTERS_MADE"' in text


def test_priority_workflow_cold_start_health_preflight_is_bounded_and_retryable():
    repo_root = Path(__file__).resolve().parents[3]
    text = (repo_root / ".github" / "workflows" / "wow-v17-priority-prop-lifecycle.yml").read_text()

    assert "wait_for_live()" in text
    assert "for attempt in 1 2 3" in text
    assert '--max-time 20' in text
    assert 'sleep "$((attempt * 5))"' in text
    assert "WOW_RUNTIME_HEALTH_UNAVAILABLE_AFTER_BOUNDED_COLD_START_RETRIES" in text
    assert text.index("wait_for_live()") < text.index("seed_date()")
    assert text.index("wait_for_live\n") < text.index('seed_date "${today}"')


def test_priority_workflow_reaches_lifecycle_even_when_seed_transport_is_ambiguous():
    repo_root = Path(__file__).resolve().parents[3]
    priority = (repo_root / ".github" / "workflows" / "wow-v17-priority-prop-lifecycle.yml").read_text()
    universal = (repo_root / ".github" / "workflows" / "wow-v17-prop-lifecycle-autopilot.yml").read_text()

    assert priority.index('seed_failures=0') < priority.index('lifecycle_body=')
    assert priority.index('if ! seed_date "${today}"') < priority.index('lifecycle_body=')
    assert priority.index('if ! seed_date "${tomorrow}"') < priority.index('lifecycle_body=')
    assert priority.index('lifecycle_body=') < priority.index('if [ "${seed_failures}" -ne 0 ]')
    assert 'routes":[]' in universal
    assert 'routes":[]' not in priority
    assert 'name: wow-v17-prop-lifecycle-autopilot' in universal
    assert 'name: wow-v17-priority-prop-lifecycle' in priority
