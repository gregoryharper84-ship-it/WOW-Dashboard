"""Fail-closed RPC writer-fence integration contract tests; no real credentials."""
import json
import pytest
from v17.engineering_continuity_lease_attestation import (
    WriterClaim, LeaseAttestationError, attest_writer_claim,
)

CLAIM = WriterClaim(1739, "github-actions:38744867262:1:wow-worker", 4)


class Response:
    def __init__(self, body):
        self.body = body
        self.read_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, n):
        assert n == 17
        self.read_calls += 1
        return self.body[:n]


class Capture:
    def __init__(self, value=b"true", error=None):
        self.value, self.error = value, error
        self.calls = 0
        self.request = None

    def __call__(self, request, timeout):
        self.calls += 1
        self.request = request
        assert timeout == 6
        if self.error:
            raise self.error
        return Response(self.value)


def check(*, response=b"true", claim=CLAIM, endpoint="https://example.supabase.co",
          credential="fake-credential", transport=None):
    conn = transport or Capture(response)
    value = attest_writer_claim(
        claim, endpoint=endpoint, credential=credential, transport=conn
    )
    return value, conn


def test_valid_active_lease_requires_literal_json_true():
    ok, conn = check()
    assert ok is True
    assert conn.calls == 1
    assert conn.request.get_method() == "POST"
    assert conn.request.full_url.endswith("/rest/v1/rpc/wow_engineering_check_writer_lease")
    payload = json.loads(conn.request.data)
    assert payload == {
        "p_incident_id": 1739,
        "p_owner_id": CLAIM.owner_id,
        "p_epoch": 4,
    }


def test_stale_or_released_epoch_is_false_and_never_a_writer_approval():
    ok, _ = check(response=b"false")
    assert ok is False


@pytest.mark.parametrize("value", [b"null", b"1", b'"true"', b"{}", b"[]",
                                    b'{"ok":true}', b""])
def test_nonboolean_or_oversized_reply_rejected(value):
    with pytest.raises(LeaseAttestationError, match="LEASE_ATTESTATION_INVALID|LEASE_ATTESTATION_UNAVAILABLE"):
        check(response=value)


@pytest.mark.parametrize("claim", [
    WriterClaim(0, CLAIM.owner_id, 1),
    WriterClaim(True, CLAIM.owner_id, 1),
    WriterClaim(1, CLAIM.owner_id, 0),
    WriterClaim(1, CLAIM.owner_id, True),
    WriterClaim(1, "manual-unverified", 1),
    WriterClaim(1, "github-actions:bad:1:wow-worker", 1),
    WriterClaim(1, "github-actions:99:0:wow-worker", 1),
    WriterClaim(1, "github-actions:99:1:token?x", 1),
])
def test_untrusted_writer_identity_rejected_without_rpc(claim):
    conn = Capture()
    with pytest.raises(LeaseAttestationError, match="BAD_WRITER_PROVENANCE"):
        check(claim=claim, transport=conn)
    assert conn.calls == 0


@pytest.mark.parametrize("endpoint,credential", [
    ("http://example.supabase.co", "fake"),
    ("https://user:pass@example.supabase.co", "fake"),
    ("https://example.supabase.co?token=secret", "fake"),
    ("https://example.supabase.co", ""),
])
def test_misconfigured_endpoint_or_credential_fails_before_network(endpoint, credential):
    conn = Capture()
    with pytest.raises(LeaseAttestationError, match="LEASE_STORE_UNAVAILABLE"):
        check(endpoint=endpoint, credential=credential, transport=conn)
    assert conn.calls == 0


def test_transport_failure_is_typed_without_leaking_exception_details():
    conn = Capture(error=OSError("token=private-secret"))
    with pytest.raises(LeaseAttestationError, match="LEASE_ATTESTATION_UNAVAILABLE") as error:
        check(transport=conn)
    assert "private-secret" not in str(error.value)
    assert conn.calls == 1


def test_valid_receipt_never_attempts_claim_renew_release_or_deploy():
    ok, conn = check()
    assert ok is True
    assert b"p_owner_id" in conn.request.data
    assert conn.request.full_url.endswith("wow_engineering_check_writer_lease")
