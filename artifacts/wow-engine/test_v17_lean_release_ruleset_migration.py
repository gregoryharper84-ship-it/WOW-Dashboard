"""Negative tests for the V17 two-gate retirement proposal."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v17 import lean_release_ruleset_migration as m


def current_ruleset():
    checks = [
        {"context": name, "integration_id": app}
        for name, app in {**m.TO_KEEP, **m.TO_REMOVE}.items()
    ]
    return {
        "id": m.RULESET_ID,
        "name": "wow-v17-main-agent-separation",
        "target": "branch",
        "source": m.REPO,
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "bypass_actors": [],
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "pull_request", "parameters": {
                "required_approving_review_count": 0,
                "dismiss_stale_reviews_on_push": True,
                "allowed_merge_methods": ["merge", "squash", "rebase"],
            }},
            {"type": "required_status_checks", "parameters": {
                "strict_required_status_checks_policy": True,
                "do_not_enforce_on_create": False,
                "required_status_checks": checks,
            }},
        ],
    }


def test_only_two_named_checks_removed_and_every_other_rule_preserved():
    source = current_ruleset()
    original = copy.deepcopy(source)
    target = m.plan_migration(source)
    assert source == original  # Pure planner, never mutates input
    assert set(target) == {"name", "target", "enforcement", "conditions", "rules", "bypass_actors"}
    new = {**original, **target}
    m.verify_target(new)
    assert m._contexts(new) == m.TO_KEEP
    assert new["rules"][:3] == original["rules"][:3]
    assert new["bypass_actors"] == []
    assert new["rules"][3]["parameters"]["strict_required_status_checks_policy"] is True


@pytest.mark.parametrize("mutation,reason", [
    (lambda r: r["rules"][3]["parameters"]["required_status_checks"][0].update(integration_id=333),
     "LIVE_REQUIRED_CHECK_INVENTORY_DRIFT"),
    (lambda r: r["rules"][3]["parameters"].update(strict_required_status_checks_policy=False),
     "STRICT_BASE_RECERTIFICATION_MISSING"),
    (lambda r: r["rules"][3]["parameters"]["required_status_checks"].append(
        {"context": "Unexpected mandatory gate", "integration_id": 15368}),
     "LIVE_REQUIRED_CHECK_INVENTORY_DRIFT"),
    (lambda r: r.update(bypass_actors=[{"actor_id": 1, "actor_type": "OrganizationAdmin",
                                        "bypass_mode": "always"}]),
     "BYPASS_ACTORS_CHANGED"),
    (lambda r: r["rules"].pop(0), "PROTECTIVE_RULE_MISSING:deletion"),
    (lambda r: r["conditions"]["ref_name"].update(include=["refs/heads/feature"]),
     "DEFAULT_BRANCH_SCOPE_CHANGED"),
    (lambda r: r.update(enforcement="disabled"), "RULESET_NOT_ACTIVE_BRANCH"),
    (lambda r: r.update(id=123), "RULESET_IDENTITY_MISMATCH"),
])
def test_unexpected_protection_drift_fails_closed(mutation, reason):
    source = current_ruleset()
    mutation(source)
    with pytest.raises(m.MigrationBlocked, match=reason):
        m.plan_migration(source)


def test_target_rejects_missing_required_qa_or_release_or_regression():
    source = current_ruleset()
    proposed = m.plan_migration(source)
    target = {**source, **proposed}
    checks = target["rules"][3]["parameters"]["required_status_checks"]
    for context in m.TO_KEEP:
        wrong = copy.deepcopy(target)
        wrong["rules"][3]["parameters"]["required_status_checks"] = [
            c for c in checks if c["context"] != context
        ]
        with pytest.raises(m.MigrationBlocked, match="FIVE_CHECK_TARGET_NOT_ACTIVE"):
            m.verify_target(wrong)


def test_original_seven_check_ruleset_is_not_misreported_as_migrated():
    with pytest.raises(m.MigrationBlocked, match="FIVE_CHECK_TARGET_NOT_ACTIVE"):
        m.verify_target(current_ruleset())
