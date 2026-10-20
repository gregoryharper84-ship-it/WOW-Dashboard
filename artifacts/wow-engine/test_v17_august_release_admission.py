"""Negative admission contract: no operational Render writer is created."""
from dataclasses import replace
from v17.august_release_admission import ReleaseFacts, REQUIRED, admission

SHA = "a" * 40

def good():
    return ReleaseFacts(
        target_sha=SHA, main_sha=SHA,
        checks={name: (SHA, "success") for name in REQUIRED},
        qa_approved=True, release_approved=True, approver_independent=True,
        credential_scope_attested=True, service_id_attested=True,
        writer_count=1, release_lease_owned=True, intent_reserved=True,
        provider_ack_uncertain=False,
    )

def test_inert_even_when_all_provided_facts_pass():
    result = admission(good())
    assert result["status"] == "ELIGIBLE_FOR_INDEPENDENT_RELEASE_HANDOFF"
    assert result["deploy"] is False and result["can_execute"] is False

def test_each_unverified_boundary_is_typed_hold():
    cases = [
        ({"main_sha": "b" * 40}, "RELEASE_EXACT_MAIN_SHA_INVALID_OR_STALE"),
        ({"target_sha": "bad"}, "RELEASE_EXACT_MAIN_SHA_INVALID_OR_STALE"),
        ({"checks": {}}, "RELEASE_AUGUST_CHECKS_NOT_VERIFIED"),
        ({"checks": {n: ("b" * 40, "success") for n in REQUIRED}}, "RELEASE_AUGUST_CHECKS_NOT_VERIFIED"),
        ({"qa_approved": False}, "RELEASE_INDEPENDENT_AUTHORIZATION_MISSING"),
        ({"release_approved": False}, "RELEASE_INDEPENDENT_AUTHORIZATION_MISSING"),
        ({"approver_independent": False}, "RELEASE_INDEPENDENT_AUTHORIZATION_MISSING"),
        ({"credential_scope_attested": False}, "RELEASE_PROVIDER_IDENTITY_UNATTESTED"),
        ({"service_id_attested": False}, "RELEASE_PROVIDER_IDENTITY_UNATTESTED"),
        ({"writer_count": 0}, "RELEASE_SINGLE_WRITER_UNVERIFIED"),
        ({"writer_count": 2}, "RELEASE_SINGLE_WRITER_UNVERIFIED"),
        ({"release_lease_owned": False}, "RELEASE_SINGLE_WRITER_UNVERIFIED"),
        ({"intent_reserved": False}, "RELEASE_DURABLE_INTENT_MISSING"),
        ({"provider_ack_uncertain": True}, "RELEASE_PROVIDER_ACK_UNCERTAIN_NO_RETRY"),
        ({"can_execute": True}, "RELEASE_TRADING_EXECUTION_MUST_BE_DISABLED"),
    ]
    for overrides, expected in cases:
        result = admission(replace(good(), **overrides))
        assert result["status"] == "HOLD", overrides
        assert expected in result["reasons"], overrides
        assert result["deploy"] is False and result["can_execute"] is False
