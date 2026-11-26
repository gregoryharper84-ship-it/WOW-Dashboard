"""Discord command registration: explicit dry-run and secret-backed HTTP contract."""
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17 import discord_register_command as target


def test_plan_only_uses_public_application_id():
    plan = target.registration_plan()
    assert plan["registration_performed"] is False
    assert plan["payload"] == {"name": "Save to WOW Research", "type": 3,
                               "integration_types": [1], "contexts": [0, 1, 2]}
    assert plan["endpoint"].endswith("/applications/1572359272317063259/commands")
    assert plan["can_execute"] is False


def test_missing_secret_never_contacts_discord(monkeypatch):
    monkeypatch.delenv("DISCORD_CLIENT_SECRET", raising=False)
    def unexpected(request):
        pytest.fail("unexpected HTTP call")
    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(target.RegistrationError, match="DISCORD_CLIENT_SECRET_MISSING"):
            target.register_command(client=client)


def test_mock_registration_checks_oauth_scope_and_response(monkeypatch):
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "private-in-test-only")
    seen = []
    def mock(request):
        seen.append(request)
        if request.url.path.endswith("/oauth2/token"):
            assert b"applications.commands.update" in request.content
            assert request.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "dummy-test-token"})
        assert request.url.path.endswith("/applications/1572359272317063259/commands")
        assert request.headers["authorization"] == "Bearer dummy-test-token"
        assert json.loads(request.content) == target.command_spec()
        return httpx.Response(201, json={"application_id": target.DEFAULT_APPLICATION_ID,
                                         "id": "99999", "name": target.command_spec()["name"],
                                         "type": 3, "integration_types": [1]})
    with httpx.Client(transport=httpx.MockTransport(mock)) as client:
        receipt = target.register_command(client=client)
    assert receipt["registered"] is True
    assert receipt["can_execute"] is False
    assert len(seen) == 2
    assert "dummy-test-token" not in str(receipt)


def test_non_2xx_registration_fails_without_exposing_secrets(monkeypatch):
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "private-in-test-only")
    def mock(req):
        if req.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "secret-test-token"})
        return httpx.Response(403, json={"error": "not authorized", "token": "secret-test-token"})
    with httpx.Client(transport=httpx.MockTransport(mock)) as client:
        with pytest.raises(target.RegistrationError) as e:
            target.register_command(client=client)
    assert str(e.value) == "DISCORD_COMMAND_REGISTER_FAILED_403"
    assert "secret-test-token" not in str(e.value)


def test_wrong_application_in_response_fails(monkeypatch):
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "private-in-test-only")
    def mock(req):
        if req.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "test-token"})
        return httpx.Response(201, json={"application_id": "123123123123123123", "name": target.command_spec()["name"],
                                         "type": 3, "integration_types": [1]})
    with httpx.Client(transport=httpx.MockTransport(mock)) as client:
        with pytest.raises(target.RegistrationError, match="DISCORD_COMMAND_RESPONSE_INVALID"):
            target.register_command(client=client)


def test_application_id_validation():
    with pytest.raises(target.RegistrationError, match="INVALID_DISCORD_APPLICATION_ID"):
        target.registration_plan("https://discord.com/not-an-app-id")
