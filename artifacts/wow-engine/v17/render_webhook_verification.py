"""Fail-closed, verification-only Render Standard Webhooks envelope parser.

No HTTP endpoint, network calls, release dispatch, or deployment authority.
A future receiver must atomically deduplicate the returned event_id in durable
storage before acknowledging/processing an event.
"""
import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from typing import Mapping


class RenderWebhookError(ValueError):
    """Typed rejection of an untrusted event."""


@dataclass(frozen=True)
class VerifiedRenderEvent:
    event_id: str
    service_id: str
    event_type: str
    status: str | None
    sent_at: int


def verify_render_webhook(
    headers: Mapping[str, str],
    raw_body: bytes,
    secret: str,
    *,
    now: int | None = None,
    max_age_seconds: int = 300,
    max_body_bytes: int = 16384,
    allowed_service_ids: frozenset[str] | None = None,
    allowed_event_types: frozenset[str] | None = None,
) -> VerifiedRenderEvent:
    """Verify signature, freshness and envelope without producing side effects."""
    if not isinstance(raw_body, bytes) or len(raw_body) > max_body_bytes:
        raise RenderWebhookError("INVALID_BODY_SIZE")
    if not secret or not secret.startswith("whsec_"):
        raise RenderWebhookError("SIGNING_SECRET_INVALID")
    try:
        signing_key = base64.b64decode(secret[6:], validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise RenderWebhookError("SIGNING_SECRET_INVALID") from exc
    if len(signing_key) < 16:
        raise RenderWebhookError("SIGNING_SECRET_INVALID")
    normalized = {str(k).lower(): v for k, v in headers.items()}
    event_id = normalized.get("webhook-id", "")
    timestamp = normalized.get("webhook-timestamp", "")
    signature = normalized.get("webhook-signature", "")
    if not isinstance(event_id, str) or not re.fullmatch(r"evt-[a-zA-Z0-9_-]{1,124}", event_id, re.ASCII):
        raise RenderWebhookError("EVENT_ID_INVALID")
    if not isinstance(timestamp, str) or not timestamp.isascii() or not timestamp.isdecimal() or len(timestamp) > 12:
        raise RenderWebhookError("TIMESTAMP_INVALID")
    sent_at = int(timestamp)
    current = int(time.time()) if now is None else now
    if max_age_seconds <= 0 or abs(current - sent_at) > max_age_seconds:
        raise RenderWebhookError("TIMESTAMP_OUT_OF_RANGE")
    message = event_id.encode("ascii") + b"." + timestamp.encode("ascii") + b"." + raw_body
    expected = hmac.new(signing_key, message, hashlib.sha256).digest()
    valid = False
    for candidate in signature.split() if isinstance(signature, str) else ():
        if not candidate.startswith("v1,"):
            continue
        try:
            supplied = base64.b64decode(candidate[3:], validate=True)
        except (ValueError, base64.binascii.Error):
            continue
        valid |= hmac.compare_digest(expected, supplied)
    if not valid:
        raise RenderWebhookError("SIGNATURE_INVALID")
    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RenderWebhookError("BODY_INVALID_JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise RenderWebhookError("ENVELOPE_INVALID")
    data = payload["data"]
    if data.get("id") != event_id:
        raise RenderWebhookError("EVENT_ID_MISMATCH")
    service_id = data.get("serviceId")
    event_type = payload.get("type")
    if not isinstance(service_id, str) or not service_id.startswith("srv-"):
        raise RenderWebhookError("SERVICE_ID_INVALID")
    if not allowed_service_ids or service_id not in allowed_service_ids:
        raise RenderWebhookError("SERVICE_NOT_ALLOWED")
    if not isinstance(event_type, str) or not event_type or len(event_type) > 100:
        raise RenderWebhookError("EVENT_TYPE_INVALID")
    if not allowed_event_types or event_type not in allowed_event_types:
        raise RenderWebhookError("EVENT_TYPE_NOT_ALLOWED")
    status = data.get("status")
    if status is not None and status not in ("succeeded", "failed", "canceled"):
        raise RenderWebhookError("STATUS_INVALID")
    return VerifiedRenderEvent(event_id, service_id, event_type, status, sent_at)
