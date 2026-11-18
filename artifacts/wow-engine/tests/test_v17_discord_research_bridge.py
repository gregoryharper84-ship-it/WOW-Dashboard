"""Offline acceptance: forged signatures, account boundaries, and research-only receipts."""
import asyncio
import importlib
import json
import sys
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
bridge = importlib.import_module("v17.discord_research_bridge")


@pytest.fixture
def signed(monkeypatch):
    private = Ed25519PrivateKey.generate()
    key = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    monkeypatch.setenv("DISCORD_PUBLIC_KEY", key)
    monkeypatch.setenv("WOW_DISCORD_ALLOWED_USER_IDS", "777")
    monkeypatch.setenv("DISCORD_APPLICATION_ID", "1572359272317063259")
    client = TestClient(bridge.app)

    def post(payload, timestamp=None):
        body = json.dumps(payload).encode()
        stamp = str(timestamp or int(time.time()))
        sig = private.sign(stamp.encode() + body).hex()
        return client.post("/discord/interactions", content=body,
                           headers={"x-signature-ed25519": sig, "x-signature-timestamp": stamp})
    return client, post


def sample_message(actor="777", body="NBA player ruled out before tipoff"):
    return {"type": 2, "id": "999555", "application_id": "1572359272317063259", "token": "interaction-test-token", "guild_id": "1111", "channel_id": "2222",
            "member": {"user": {"id": actor}}, "data": {"type": 3,
            "name": bridge.COMMAND_NAME, "target_id": "3333", "resolved": {"messages": {"3333": {
                "content": body, "timestamp": "2026-11-17T19:00:00Z", "author": {"id": "888"}}}}}}


def test_ping_requires_valid_signature(signed):
    client, post = signed
    assert post({"type": 1}).json() == {"type": 1}
    assert client.post("/discord/interactions", json={"type": 1}).status_code == 401


def test_rejects_old_signature(signed):
    _, post = signed
    assert post({"type": 1}, timestamp=int(time.time())-600).status_code == 401


def test_disallowed_user_never_written(signed, monkeypatch):
    _, post = signed
    def forbidden(_):
        raise AssertionError("unexpected DB call")
    monkeypatch.setattr(bridge, "_persist_signal", forbidden)
    r = post(sample_message(actor="666"))
    assert r.json()["data"]["flags"] == 64
    assert "could not be accepted" in r.json()["data"]["content"]


def test_no_storage_fails_closed(signed, monkeypatch):
    _, post = signed
    monkeypatch.delenv("WOW_DISCORD_DATABASE_URL", raising=False)
    edits = []
    async def edit(_app, _token, message):
        edits.append(message)
        return True
    monkeypatch.setattr(bridge, "_edit_deferred_response", edit)
    r = post(sample_message())
    assert r.json() == {"type": 5, "data": {"flags": 64}}
    assert len(edits) == 1
    assert "Nothing was saved" in edits[0]


def test_received_signal_never_contains_raw_text(signed, monkeypatch):
    _, post = signed
    seen, edits = [], []
    monkeypatch.setattr(bridge, "_persist_signal", lambda s: seen.append(s) or True)
    async def edit(_app, _token, message):
        edits.append(message)
        return True
    monkeypatch.setattr(bridge, "_edit_deferred_response", edit)
    raw = "Secret info, possible NBA availability"
    r = post(sample_message(body=raw))
    assert r.json() == {"type": 5, "data": {"flags": 64}}
    assert "receipt saved" in edits[0]
    assert len(seen) == 1
    assert raw not in str(seen[0])
    assert seen[0]["content_sha256"] and len(seen[0]["content_sha256"]) == 64
    assert seen[0]["status"] == "RESEARCH_ONLY_UNVERIFIED"
    assert seen[0]["prediction_authority"] is False
    assert seen[0]["can_execute"] is False


def test_duplicate_receipt_says_not_created(signed, monkeypatch):
    _, post = signed
    edits = []
    monkeypatch.setattr(bridge, "_persist_signal", lambda s: False)
    async def edit(_app, _token, message):
        edits.append(message)
        return True
    monkeypatch.setattr(bridge, "_edit_deferred_response", edit)
    assert post(sample_message()).json()["type"] == 5
    assert "no duplicate" in edits[0]


def test_message_content_missing_cannot_save(signed, monkeypatch):
    _, post = signed
    monkeypatch.setattr(bridge, "_persist_signal", lambda s: pytest.fail("should not save"))
    assert "could not be accepted" in post(sample_message(body="")).json()["data"]["content"]


def test_user_install_command_only():
    spec = bridge.command_spec()
    assert spec == {"name": bridge.COMMAND_NAME, "type": 3, "integration_types": [1], "contexts": [0, 1, 2]}
    assert bridge.app.openapi()  # app is independently deployable


def test_oauth_start_requires_credentials(monkeypatch):
    monkeypatch.delenv("DISCORD_APPLICATION_ID", raising=False)
    assert TestClient(bridge.app).get("/discord/oauth/start").status_code == 503


def test_oauth_state_checks_tampering_and_expiry(monkeypatch):
    monkeypatch.setenv("WOW_DISCORD_OAUTH_STATE_SECRET", "x" * 40)
    state = bridge._sign_state("a" * 40, int(time.time()))
    assert bridge._valid_state(state)
    assert not bridge._valid_state(state[:-1] + ("f" if state[-1] != "f" else "e"))
    assert not bridge._valid_state(bridge._sign_state("a" * 40, int(time.time()) - 400))


def test_oauth_callback_rejects_mismatched_state():
    r = TestClient(bridge.app).get("/discord/oauth/callback?code=x&state=fake")
    assert r.status_code == 403


def test_oauth_identity_scope_and_allowlist(monkeypatch):
    monkeypatch.setenv("DISCORD_APPLICATION_ID", "a")
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "secret")
    monkeypatch.setenv("WOW_DISCORD_OAUTH_REDIRECT_URI", "https://example.org/callback")
    monkeypatch.setenv("WOW_DISCORD_ALLOWED_USER_IDS", "777")
    import httpx
    def response(req):
        if str(req.url).endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "some-token"})
        if str(req.url).endswith("/users/@me"):
            return httpx.Response(200, json={"id": "777", "username": "owner"})
        return httpx.Response(200, json=[{"id": "42", "name": "sports", "permissions": "0"}])
    async def capture_identity():
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            return await bridge._oauth_identity("code", client)
    result = asyncio.run(capture_identity())
    assert result["guilds"] == [{"id": "42", "name": "sports", "can_manage": False}]
    assert result["oauth_token_stored"] is False
    assert result["channel_content_access"] is False
    assert "some-token" not in str(result)


def test_oauth_callback_rejects_non_discord_issuer(monkeypatch):
    monkeypatch.setenv("WOW_DISCORD_OAUTH_STATE_SECRET", "s" * 40)
    client = TestClient(bridge.app)
    state = bridge._sign_state("a" * 40, int(time.time()))
    client.cookies.set("wow_discord_oauth_state", state)
    resp = client.get("/discord/oauth/callback", params={"code": "fake", "state": state, "iss": "https://fake.example"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "DISCORD_OAUTH_ISSUER_INVALID"


def test_signature_tolerates_small_future_clock_skew(signed):
    _, post = signed
    assert post({"type": 1}, timestamp=int(time.time())+2).json() == {"type": 1}


def test_null_member_falls_back_to_user(signed, monkeypatch):
    _, post = signed
    signal = sample_message()
    signal["member"] = None
    signal["user"] = {"id": "777"}
    captured = []
    monkeypatch.setattr(bridge, "_persist_signal", lambda s: captured.append(s) or True)
    async def edit(_app, _token, _message):
        return True
    monkeypatch.setattr(bridge, "_edit_deferred_response", edit)
    assert post(signal).json()["type"] == 5
    assert captured[0]["actor_id"] == "777"


def test_null_messages_rejected_as_bad_input(signed, monkeypatch):
    _, post = signed
    signal = sample_message()
    signal["data"]["resolved"]["messages"] = None
    monkeypatch.setattr(bridge, "_persist_signal", lambda s: pytest.fail("unexpected persistence"))
    r = post(signal)
    assert r.json()["type"] == 4
    assert "could not be accepted" in r.json()["data"]["content"]


def test_wrong_application_never_persists(signed, monkeypatch):
    _, post = signed
    signal = sample_message()
    signal["application_id"] = "1572359272317063260"
    monkeypatch.setattr(bridge, "_persist_signal", lambda s: pytest.fail("wrong app"))
    r = post(signal)
    assert r.json()["type"] == 4
    assert "configuration unavailable" in r.json()["data"]["content"]


def test_database_work_is_threaded(signed, monkeypatch):
    _, post = signed
    import threading
    caller = threading.get_ident()
    observed = []
    def save(_):
        observed.append(threading.get_ident())
        return True
    async def edit(_app, _token, _message):
        return True
    monkeypatch.setattr(bridge, "_persist_signal", save)
    monkeypatch.setattr(bridge, "_edit_deferred_response", edit)
    assert post(sample_message()).json()["type"] == 5
    assert len(observed) == 1
    assert observed[0] != caller
