"""Inert Render hook candidate: negative/adjacent contract and no secret leakage."""
import pytest
from urllib.parse import parse_qs, urlsplit
from v17.render_hook_candidate import (
    HookContractError, PreparedHook, SERVICE_ID, HOOK_SECRET,
    prepare_exact_commit_hook, classify_hook_ack,
)

SHA = "a" * 40
HOOK = f"https://api.render.com/deploy/{SERVICE_ID}?key=super-secret-test-value"


def test_candidate_builds_only_exact_sha_post_without_executing():
    prepared = prepare_exact_commit_hook(HOOK, SHA)
    assert isinstance(prepared, PreparedHook)
    assert prepared.request.get_method() == "POST"
    u = urlsplit(prepared.request.full_url)
    assert u.netloc == "api.render.com"
    assert u.path == "/deploy/" + SERVICE_ID
    assert parse_qs(u.query) == {"key": ["super-secret-test-value"], "ref": [SHA]}
    assert prepared.can_execute is False
    assert "super-secret-test-value" not in repr(prepared)
    assert HOOK_SECRET == "WOW_RENDER_DEPLOY_HOOK"


@pytest.mark.parametrize("url,code", [
    ("", "HOOK_SECRET_MISSING"),
    (None, "HOOK_SECRET_MISSING"),
    ("https://api.render.com/deploy/srv-other?key=secret", "HOOK_ENDPOINT_MISMATCH"),
    ("http://api.render.com/deploy/" + SERVICE_ID + "?key=x", "HOOK_ENDPOINT_MISMATCH"),
    ("https://evil.example/deploy/" + SERVICE_ID + "?key=x", "HOOK_ENDPOINT_MISMATCH"),
    ("https://api.render.com.evil.example/deploy/" + SERVICE_ID + "?key=x", "HOOK_ENDPOINT_MISMATCH"),
    ("https://api.render.com/deploy/" + SERVICE_ID + "?key=x#fragment", "HOOK_ENDPOINT_MISMATCH"),
    ("https://api.render.com/deploy/" + SERVICE_ID + "?ref=" + SHA + "&key=x", "HOOK_QUERY_INVALID"),
    ("https://api.render.com/deploy/" + SERVICE_ID + "?key=x&key=y", "HOOK_QUERY_INVALID"),
    ("https://api.render.com/deploy/" + SERVICE_ID + "?key=", "HOOK_QUERY_INVALID"),
    ("https://api.render.com/deploy/" + SERVICE_ID + "?x=y", "HOOK_QUERY_INVALID"),
    ("https://api.render.com/deploy/" + SERVICE_ID + "?key=x&imgURL=a", "HOOK_QUERY_INVALID"),
    ("https://api.render.com/deploy/" + SERVICE_ID + "?key", "HOOK_URL_INVALID"),
])
def test_malformed_foreign_or_duplicate_hook_always_holds_redacted(url, code):
    with pytest.raises(HookContractError) as e:
        prepare_exact_commit_hook(url, SHA)
    assert e.value.code == code
    assert "super-secret-test-value" not in str(e.value)
    assert "key=" not in str(e.value)


@pytest.mark.parametrize("sha", ["", None, "abc", "A" * 40, "z" * 40, "a" * 39, True, 1])
def test_invalid_sha_fails_closed(sha):
    with pytest.raises(HookContractError) as e:
        prepare_exact_commit_hook(HOOK, sha)
    assert e.value.code == "HOOK_TARGET_SHA_INVALID"


def test_other_service_cannot_be_selected_by_caller():
    with pytest.raises(HookContractError) as e:
        prepare_exact_commit_hook(HOOK, SHA, service_id="srv-other")
    assert e.value.code == "HOOK_SERVICE_ID_INVALID"


@pytest.mark.parametrize("status,body,expected", [
    (200, b'{"deploy":{"id":"dep-abc123"}}', "HOOK_ACK_PENDING_EXACT_SHA_READBACK"),
    (200, b'{"deploy":{}}', "HOOK_ACK_AMBIGUOUS_RECONCILE_NO_RETRY"),
    (200, b'not-json', "HOOK_ACK_AMBIGUOUS_RECONCILE_NO_RETRY"),
    (200, b'{"deploy":{"id":"http://evil"}}', "HOOK_ACK_AMBIGUOUS_RECONCILE_NO_RETRY"),
    (202, None, "HOOK_QUEUED_RECONCILE_NO_RETRY"),
    (400, b'bad', "HOOK_RESPONSE_HOLD"),
    (401, b'unauthorized', "HOOK_RESPONSE_HOLD"),
    (404, b'missing', "HOOK_RESPONSE_HOLD"),
    (409, b'conflict', "HOOK_RESPONSE_HOLD"),
    (500, b'oops', "HOOK_RESPONSE_HOLD"),
    (True, None, "HOOK_RESPONSE_UNVERIFIED"),
])
def test_response_never_claims_production_acceptance_or_retries(status, body, expected):
    result = classify_hook_ack(status, body)
    assert result["status"] == expected
    assert result["retry"] is False
    assert result["can_execute"] is False
    if expected == "HOOK_ACK_PENDING_EXACT_SHA_READBACK":
        assert result["deploy_id"] == "dep-abc123"
    else:
        assert result["deploy_id"] == ""


def test_adapter_exports_no_transport_path():
    # This code must remain inert until a separately reviewed protected job
    # provides genuine provider attestation, lease and authorized transport.
    from pathlib import Path
    module_source = Path(__file__).with_name("v17").joinpath("render_hook_candidate.py").read_text()
    assert "urlopen" not in module_source
    assert "requests.post" not in module_source
    assert "http.client" not in module_source
