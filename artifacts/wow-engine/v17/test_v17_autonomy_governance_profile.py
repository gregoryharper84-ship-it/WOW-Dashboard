"""Deterministic negative-first acceptance for non-authorizing routing policy."""
from v17.autonomy_governance_profile import Track, assess, remote_writer_reentry


def route(paths, effects, **kwargs):
    return assess(paths=paths, effects=effects,
                  exact_head_verified=kwargs.get('exact_head_verified', True),
                  diff_evidence_verified=kwargs.get('diff_evidence_verified', True),
                  source_role_verified=kwargs.get('source_role_verified', True))


def test_routine_runbook_needs_no_default_owner_approval():
    r=route(['docs/runbooks/nba-snapshot.md'], ['none'])
    assert r.track is Track.ROUTINE and not r.owner_approval_by_default
    assert r.no_execution and r.terminal_authority == 'V17_TERMINAL_REDUCER'


def test_core_engineering_routing_requires_independent_check():
    r=route(['artifacts/wow-engine/v17/nba_points_snapshot_bridge.py'], ['evidence_hydration'])
    assert r.track is Track.ENGINEERING and not r.requires_model_certification


def test_model_math_always_requires_certification():
    r=route(['artifacts/wow-engine/v17/fitted_model.py'], ['sporting_probability_math'])
    assert r.track is Track.MODEL and r.requires_model_certification


def test_threshold_override_is_model_scope():
    assert route(['docs/runbooks/calibration.md'], ['qualification_threshold']).track is Track.MODEL


def test_new_workflow_and_agent_skills_remain_trust_roots():
    for path in ['.github/workflows/new.yml', '.agents/skills/release/SKILL.md', '.claude/review.md', 'render.yaml']:
        r=route([path], ['none'])
        assert r.track is Track.TRUST_ROOT and r.requires_existing_protected_authorization


def test_protected_model_and_governance_mixed_is_trust_root():
    r=route(['.github/workflows/protect.yml', 'model.py'], ['model_promotion'])
    assert r.track is Track.TRUST_ROOT
    assert r.requires_model_certification  # Mixed scope cannot drop the scientific gate.
    assert r.requires_existing_protected_authorization


def test_every_mixed_model_effect_preserves_dual_certification():
    for effect in ('calibration', 'calibrated_lower_bound',
                   'qualification_threshold', 'fitted_artifact',
                   'scorer_registration', 'sporting_probability_math'):
        r=route(['.github/workflows/wow-verify.yml',
                 'artifacts/wow-engine/v17/probability.py'], [effect])
        assert r.track is Track.TRUST_ROOT
        assert r.requires_model_certification
        assert r.requires_existing_protected_authorization
        assert r.no_execution


def test_routing_policy_self_edit_is_protected_when_integrated():
    r=route(['artifacts/wow-engine/v17/autonomy_governance_profile.py'], ['none'])
    assert r.track is Track.TRUST_ROOT
    assert r.owner_approval_by_default


def test_untrusted_effect_declaration_fails_closed():
    assert route(['model.py'], ['marketing']).track is Track.HOLD
    assert route(['model.py'], ['none','routing']).track is Track.HOLD
    assert route([], ['none']).track is Track.HOLD
    assert route(['model.py'], []).track is Track.HOLD


def test_no_default_fast_path_for_arbitrary_python_or_tests():
    assert route(['artifacts/wow-engine/test_quality.py'], ['none']).track is Track.HOLD
    assert route(['artifacts/wow-engine/v17/probability.py'], ['none']).track is Track.HOLD


def test_invalid_identity_and_duplicate_scope_hold():
    assert route(['docs/runbooks/x.md'], ['none'], exact_head_verified=False).track is Track.HOLD
    assert route(['docs/runbooks/x.md'], ['none'], source_role_verified=False).track is Track.HOLD
    assert route(['docs/runbooks/x.md'], ['none'], diff_evidence_verified=False).track is Track.HOLD
    assert route(['docs/runbooks/x.md','docs/runbooks/x.md'], ['none']).track is Track.HOLD
    assert route(['../secret'], ['none']).track is Track.HOLD


def test_lease_release_cannot_follow_cursor_finished_alone():
    assert remote_writer_reentry(terminated_provider_receipt_verified=True,
        original_reservation_verified=True, exclusive_lease_epoch_verified=True,
        no_newer_active_run_verified=True,atomic_release_supported=False,
        fresh_readback_verified=False) == 'WRITER_CAS_FINALIZER_UNAVAILABLE'


def test_lease_epoch_mismatch_blocks_release():
    assert remote_writer_reentry(terminated_provider_receipt_verified=True,
        original_reservation_verified=True, exclusive_lease_epoch_verified=False,
        no_newer_active_run_verified=True,atomic_release_supported=True,
        fresh_readback_verified=True) == 'WRITER_FENCE_UNVERIFIED'


def test_release_proof_does_not_approve_code():
    assert remote_writer_reentry(terminated_provider_receipt_verified=True,
        original_reservation_verified=True, exclusive_lease_epoch_verified=True,
        no_newer_active_run_verified=True,atomic_release_supported=True,
        fresh_readback_verified=True) == 'WRITER_RELEASE_PROOF_COMPLETE'
    # Output is evidence only. There is no review, merge or model authority function.
