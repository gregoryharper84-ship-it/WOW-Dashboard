import base64
import hashlib
import hmac
import json

import pytest

from v17.render_webhook_verification import RenderWebhookError, verify_render_webhook


SECRET = "whsec_" + base64.b64encode(b"this-is-an-isolated-test-secret-32").decode()
NOW = 1791840000


def signed(body=None, event_id="evt-test123", sent_at=NOW):
    body = body or {"type": "deploy_ended", "data": {"id": event_id, "serviceId": "srv-test", "status": "succeeded"}}
    raw = json.dumps(body, separators=(",", ":")).encode()
    message = f"{event_id}.{sent_at}.".encode() + raw
    digest = hmac.new(base64.b64decode(SECRET[6:]), message, hashlib.sha256).digest()
    return {"webhook-id": event_id, "webhook-timestamp": str(sent_at), "webhook-signature": "v1," + base64.b64encode(digest).decode()}, raw


def test_accepts_authentic_event_without_dispatch():
    headers, body = signed()
    result = verify_render_webhook(headers, body, SECRET, now=NOW)
    assert (result.event_id, result.service_id, result.event_type) == ("evt-test123", "srv-test", "deploy_ended")


@pytest.mark.parametrize("mutation", ["body", "signature", "old", "future", "id", "bad_secret", "status", "large"])
def test_rejects_invalid_envelopes(mutation):
    headers, body = signed()
    secret = SECRET
    if mutation == "body":
        body += b" "
    elif mutation == "signature":
        headers["webhook-signature"] = "v1,AAAA"
    elif mutation == "old":
        headers, body = signed(sent_at=NOW - 301)
    elif mutation == "future":
        headers, body = signed(sent_at=NOW + 301)
    elif mutation == "id":
        headers["webhook-id"] = "evt-other"
    elif mutation == "bad_secret":
        secret = "invalid"
    elif mutation == "status":
        headers, body = signed(body={"type": "deploy_ended", "data": {"id": "evt-test123", "serviceId": "srv-test", "status": "unknown"}})
    elif mutation == "large":
        body += b"x" * 16385
    with pytest.raises(RenderWebhookError):
        verify_render_webhook(headers, body, secret, now=NOW)


def test_handles_multiple_rotation_signatures():
    headers, body = signed()
    headers["webhook-signature"] = "v1,AAAA " + headers["webhook-signature"]
    assert verify_render_webhook(headers, body, SECRET, now=NOW).event_id == "evt-test123"


def test_rejects_payload_id_mismatch_even_with_valid_signature():
    headers, body = signed(body={"type": "deploy_ended", "data": {"id": "evt-another", "serviceId": "srv-test"}})
    with pytest.raises(RenderWebhookError, match="EVENT_ID_MISMATCH"):
        verify_render_webhook(headers, body, SECRET, now=NOW)
