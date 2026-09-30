from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
oidc = ROOT / "artifacts/wow-engine/github_actions_oidc.py"
cert_test = ROOT / "artifacts/wow-engine/test_v17_spread_certification_replay_oidc_allowlist.py"
spread_test = ROOT / "artifacts/wow-engine/test_v17_spread_forward_production_canary_oidc_allowlist.py"
prop_test = ROOT / "artifacts/wow-engine/test_v17_prop_evidence_oidc_allowlist.py"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"PATCH_EXPECTATION_FAILED:{path}:{count}:{old[:100]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    oidc,
    '''    if workflow_ref in {\n        SPREAD_FORWARD_PRODUCTION_CANARY_WORKFLOW_REF,\n        SPREAD_CERTIFICATION_REPLAY_WORKFLOW_REF,\n    }:\n        if event_name != "workflow_run":\n            raise GitHubOIDCValidationError("GITHUB_OIDC_EVENT_NOT_ALLOWED")\n''',
    '''    if workflow_ref in {\n        SPREAD_FORWARD_PRODUCTION_CANARY_WORKFLOW_REF,\n        SPREAD_CERTIFICATION_REPLAY_WORKFLOW_REF,\n    }:\n        # These exact protected-main workflows may run either as the historical\n        # direct workflow_run path or as reusable workflow_call stages behind\n        # the single post-deploy orchestrator. Do not widen any other workflow.\n        if event_name not in {"workflow_run", "workflow_call"}:\n            raise GitHubOIDCValidationError("GITHUB_OIDC_EVENT_NOT_ALLOWED")\n''',
)
replace_once(
    oidc,
    '''        if event_name not in ALLOWED_EVENTS | {"workflow_run"}:\n            raise GitHubOIDCValidationError("GITHUB_OIDC_EVENT_NOT_ALLOWED")\n''',
    '''        if event_name not in ALLOWED_EVENTS | {"workflow_run", "workflow_call"}:\n            raise GitHubOIDCValidationError("GITHUB_OIDC_EVENT_NOT_ALLOWED")\n''',
)

replace_once(
    cert_test,
    '''def test_spread_certification_replay_workflow_run_is_explicitly_authorized():\n    claims = _claims()\n    assert oidc.SPREAD_CERTIFICATION_REPLAY_WORKFLOW_REF in oidc.LIVE_CANARY_WORKFLOW_REFS\n    assert oidc.SPREAD_CERTIFICATION_REPLAY_WORKFLOW_REF not in oidc.ALLOWED_WORKFLOW_REFS\n    assert oidc.validate_github_actions_claims(claims) == claims\n\n\n''',
    '''def test_spread_certification_replay_workflow_run_is_explicitly_authorized():\n    claims = _claims()\n    assert oidc.SPREAD_CERTIFICATION_REPLAY_WORKFLOW_REF in oidc.LIVE_CANARY_WORKFLOW_REFS\n    assert oidc.SPREAD_CERTIFICATION_REPLAY_WORKFLOW_REF not in oidc.ALLOWED_WORKFLOW_REFS\n    assert oidc.validate_github_actions_claims(claims) == claims\n\n\ndef test_spread_certification_replay_workflow_call_is_explicitly_authorized():\n    claims = _claims(event_name="workflow_call")\n    assert oidc.validate_github_actions_claims(claims) == claims\n\n\n''',
)
replace_once(
    spread_test,
    '''def test_spread_forward_production_canary_workflow_run_is_explicitly_authorized():\n    claims = _claims()\n    assert oidc.SPREAD_FORWARD_PRODUCTION_CANARY_WORKFLOW_REF in oidc.LIVE_CANARY_WORKFLOW_REFS\n    assert oidc.SPREAD_FORWARD_PRODUCTION_CANARY_WORKFLOW_REF not in oidc.ALLOWED_WORKFLOW_REFS\n    assert oidc.validate_github_actions_claims(claims) == claims\n\n\n''',
    '''def test_spread_forward_production_canary_workflow_run_is_explicitly_authorized():\n    claims = _claims()\n    assert oidc.SPREAD_FORWARD_PRODUCTION_CANARY_WORKFLOW_REF in oidc.LIVE_CANARY_WORKFLOW_REFS\n    assert oidc.SPREAD_FORWARD_PRODUCTION_CANARY_WORKFLOW_REF not in oidc.ALLOWED_WORKFLOW_REFS\n    assert oidc.validate_github_actions_claims(claims) == claims\n\n\ndef test_spread_forward_production_canary_workflow_call_is_explicitly_authorized():\n    claims = _claims(event_name="workflow_call")\n    assert oidc.validate_github_actions_claims(claims) == claims\n\n\n''',
)
replace_once(
    prop_test,
    '''def test_priority_prop_lifecycle_workflow_run_is_authorized_after_exact_deploy():\n    claims = _claims(oidc.PRIORITY_PROP_LIFECYCLE_WORKFLOW_REF, "workflow_run")\n    assert oidc.validate_github_actions_claims(claims)["event_name"] == "workflow_run"\n\n\n''',
    '''def test_priority_prop_lifecycle_workflow_run_is_authorized_after_exact_deploy():\n    claims = _claims(oidc.PRIORITY_PROP_LIFECYCLE_WORKFLOW_REF, "workflow_run")\n    assert oidc.validate_github_actions_claims(claims)["event_name"] == "workflow_run"\n\n\ndef test_priority_prop_lifecycle_workflow_call_is_authorized_only_for_exact_reusable_workflow():\n    claims = _claims(oidc.PRIORITY_PROP_LIFECYCLE_WORKFLOW_REF, "workflow_call")\n    assert oidc.validate_github_actions_claims(claims)["event_name"] == "workflow_call"\n    for workflow_ref in (\n        oidc.NFL_PROP_FORWARD_EVIDENCE_WORKFLOW_REF,\n        oidc.MLB_PROP_FORWARD_EVIDENCE_WORKFLOW_REF,\n        oidc.WNBA_PROP_FORWARD_EVIDENCE_WORKFLOW_REF,\n    ):\n        with pytest.raises(oidc.GitHubOIDCValidationError, match="EVENT_NOT_ALLOWED"):\n            oidc.validate_github_actions_claims(_claims(workflow_ref, "workflow_call"))\n\n\n''',
)

print("PATCHED_REUSABLE_OIDC_1113")
