from pathlib import Path


def test_priority_prop_lifecycle_is_bounded_fail_closed_and_sport_scoped():
    repo_root = Path(__file__).resolve().parents[3]
    text = (repo_root / ".github" / "workflows" / "wow-v17-priority-prop-lifecycle.yml").read_text()

    assert 'cron: "7 * * * *"' in text
    assert 'workflow_call:' in text
    assert 'workflows: ["wow-v17-render-production-deploy"]' not in text
    assert 'workflow_run:' not in text
    assert '\n  push:\n' not in text
    assert 'max-parallel: 1' in text
    assert 'id-token: write' in text
    assert '/v17/prop-lifecycle-autopilot-run' not in text
    assert '/v17/prop-priority-settlement-run' in text
    assert '/v17/prop-priority-durable-audit-run' in text
    assert '/internal/v17/nfl-prop-forward-evidence/acquire' in text
    assert '/internal/v17/mlb-prop-forward-evidence/acquire' in text
    assert '/internal/v17/wnba-prop-forward-evidence/acquire' in text
    assert text.count('max_candidates: 8') == 3
    assert 'post_oidc_json' in text
    assert 'seed_date "${today}"' in text
    assert 'seed_date "${tomorrow}"' in text
    assert 'candidate_offset":0' in text
    assert 'seed_failures=0' in text
    assert 'settlement_failures=0' in text
    assert 'persisted_health_n' in text
    assert 'source_diagnostics' in text
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


def test_priority_workflow_release_handoff_health_preflight_is_bounded_and_retryable():
    repo_root = Path(__file__).resolve().parents[3]
    text = (repo_root / ".github" / "workflows" / "wow-v17-priority-prop-lifecycle.yml").read_text()

    assert "wait_for_live()" in text
    assert "for attempt in $(seq 1 10)" in text
    assert '--connect-timeout 3 --max-time 5' in text
    assert 'attempt ${attempt}/10' in text
    assert 'sleep 10' in text
    assert "WOW_RUNTIME_HEALTH_UNAVAILABLE_AFTER_BOUNDED_RELEASE_HANDOFF_RETRIES" in text
    assert text.index("wait_for_live()") < text.index("seed_date()")
    assert text.index("wait_for_live\n") < text.index('if ! seed_date "${today}"')


def test_priority_workflow_consumes_durable_rows_after_ambiguous_seed_or_settlement_transport():
    repo_root = Path(__file__).resolve().parents[3]
    priority = (repo_root / ".github" / "workflows" / "wow-v17-priority-prop-lifecycle.yml").read_text()
    universal = (repo_root / ".github" / "workflows" / "wow-v17-prop-lifecycle-autopilot.yml").read_text()

    seed = priority.index('seed_failures=0')
    settlement = priority.index('settlement_failures=0')
    audit = priority.index('audit_body=')
    terminal_failure = priority.index('if [ "${seed_failures}" -ne 0 ] || [ "${settlement_failures}" -ne 0 ]')
    assert seed < settlement < audit < terminal_failure
    assert 'durable settlement/audit will still inspect persisted rows' in priority
    assert 'health_persistence' in priority
    assert 'source_diagnostics' in priority
    assert 'routes":[]' in universal
    assert 'name: wow-v17-prop-lifecycle-autopilot' in universal
    assert 'name: wow-v17-priority-prop-lifecycle' in priority
