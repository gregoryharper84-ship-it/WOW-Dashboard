"""Adversarial input tests for an inert release-claim consistency rule."""
from dataclasses import replace

import pytest

from v17.august_release_admission import ReleaseFacts, REQUIRED, admission

SHA = "a" * 40


def good():
    return ReleaseFacts(
        target_sha=SHA,
        main_sha=SHA,
        checks={name: (SHA, "success") for name in REQUIRED},
        qa_approved=True,
        release_approved=True,
        approver_independent=True,
        credential_scope_attested=True,
        service_id_attested=True,
        writer_count=1,
        release_lease_owned=True,
        intent_reserved=True,
        provider_ack_uncertain=False,
        can_execute=False,
    )


def assert_inert(result):
    assert result["deploy"] is False
    assert result["can_execute"] is False


def test_valid_claims_are_not_proof_or_deployment_authority():
    result = admission(good())
    assert result["status"] == "CLAIMS_CONSISTENT_NOT_AUTHORIZED"
    assert result["reasons"] == []
    assert_inert(result)


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"main_sha": "b" * 40}, "RELEASE_EXACT_MAIN_SHA_INVALID_OR_STALE"),
        ({"main_sha": False}, "RELEASE_EXACT_MAIN_SHA_INVALID_OR_STALE"),
        ({"target_sha": "bad"}, "RELEASE_EXACT_MAIN_SHA_INVALID_OR_STALE"),
        ({"target_sha": None}, "RELEASE_EXACT_MAIN_SHA_INVALID_OR_STALE"),
        ({"checks": {}}, "RELEASE_AUGUST_CHECKS_NOT_VERIFIED"),
        ({"checks": False}, "RELEASE_AUGUST_CHECKS_NOT_VERIFIED"),
        ({"checks": []}, "RELEASE_AUGUST_CHECKS_NOT_VERIFIED"),
        ({"checks": {n: ("b" * 40, "success") for n in REQUIRED}}, "RELEASE_AUGUST_CHECKS_NOT_VERIFIED"),
        ({"checks": {n: [SHA, "success"] for n in REQUIRED}}, "RELEASE_AUGUST_CHECKS_NOT_VERIFIED"),
        ({"checks": {n: (SHA, True) for n in REQUIRED}}, "RELEASE_AUGUST_CHECKS_NOT_VERIFIED"),
        ({"checks": {n: (SHA, "completed") for n in REQUIRED}}, "RELEASE_AUGUST_CHECKS_NOT_VERIFIED"),
        ({"qa_approved": False}, "RELEASE_INDEPENDENT_AUTHORIZATION_MISSING"),
        ({"qa_approved": "true"}, "RELEASE_INDEPENDENT_AUTHORIZATION_MISSING"),
        ({"qa_approved": 1}, "RELEASE_INDEPENDENT_AUTHORIZATION_MISSING"),
        ({"release_approved": False}, "RELEASE_INDEPENDENT_AUTHORIZATION_MISSING"),
        ({"release_approved": "true"}, "RELEASE_INDEPENDENT_AUTHORIZATION_MISSING"),
        ({"approver_independent": False}, "RELEASE_INDEPENDENT_AUTHORIZATION_MISSING"),
        ({"approver_independent": "no"}, "RELEASE_INDEPENDENT_AUTHORIZATION_MISSING"),
        ({"credential_scope_attested": False}, "RELEASE_PROVIDER_IDENTITY_UNATTESTED"),
        ({"credential_scope_attested": "false"}, "RELEASE_PROVIDER_IDENTITY_UNATTESTED"),
        ({"credential_scope_attested": 1}, "RELEASE_PROVIDER_IDENTITY_UNATTESTED"),
        ({"service_id_attested": False}, "RELEASE_PROVIDER_IDENTITY_UNATTESTED"),
        ({"service_id_attested": "yes"}, "RELEASE_PROVIDER_IDENTITY_UNATTESTED"),
        ({"writer_count": 0}, "RELEASE_SINGLE_WRITER_UNVERIFIED"),
        ({"writer_count": 2}, "RELEASE_SINGLE_WRITER_UNVERIFIED"),
        ({"writer_count": True}, "RELEASE_SINGLE_WRITER_UNVERIFIED"),
        ({"writer_count": "1"}, "RELEASE_SINGLE_WRITER_UNVERIFIED"),
        ({"writer_count": 1.0}, "RELEASE_SINGLE_WRITER_UNVERIFIED"),
        ({"release_lease_owned": False}, "RELEASE_SINGLE_WRITER_UNVERIFIED"),
        ({"release_lease_owned": "yes"}, "RELEASE_SINGLE_WRITER_UNVERIFIED"),
        ({"intent_reserved": False}, "RELEASE_DURABLE_INTENT_MISSING"),
        ({"intent_reserved": 1}, "RELEASE_DURABLE_INTENT_MISSING"),
        ({"provider_ack_uncertain": True}, "RELEASE_PROVIDER_ACK_UNCERTAIN_NO_RETRY"),
        ({"provider_ack_uncertain": "false"}, "RELEASE_PROVIDER_ACK_UNCERTAIN_NO_RETRY"),
        ({"provider_ack_uncertain": None}, "RELEASE_PROVIDER_ACK_UNCERTAIN_NO_RETRY"),
        ({"can_execute": True}, "RELEASE_TRADING_EXECUTION_MUST_BE_DISABLED"),
        ({"can_execute": 0}, "RELEASE_TRADING_EXECUTION_MUST_BE_DISABLED"),
        ({"can_execute": "false"}, "RELEASE_TRADING_EXECUTION_MUST_BE_DISABLED"),
    ],
)
def test_each_boundary_fails_closed(overrides, expected):
    result = admission(replace(good(), **overrides))
    assert result["status"] == "HOLD", overrides
    assert expected in result["reasons"], overrides
    assert_inert(result)


@pytest.mark.parametrize("value", [None, {}, False, [], "valid", 1])
def test_non_release_fact_objects_hold(value):
    result = admission(value)
    assert result["status"] == "HOLD"
    assert "RELEASE_FACTS_INVALID" in result["reasons"]
    assert_inert(result)


def test_multiple_unsafe_claims_keep_distinct_reasons():
    result = admission(replace(
        good(), writer_count=True, credential_scope_attested="false",
        approver_independent="no", can_execute="false",
    ))
    assert result["status"] == "HOLD"
    assert set(result["reasons"]) == {
        "RELEASE_SINGLE_WRITER_UNVERIFIED",
        "RELEASE_PROVIDER_IDENTITY_UNATTESTED",
        "RELEASE_INDEPENDENT_AUTHORIZATION_MISSING",
        "RELEASE_TRADING_EXECUTION_MUST_BE_DISABLED",
    }
    assert_inert(result)
