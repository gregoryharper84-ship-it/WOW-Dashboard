"""Register WOW's single Discord user-install message command.

Manual, operator-triggered step. Never starts a bot, polls channels or places wagers.
Requires environment DISCORD_CLIENT_SECRET for real registration. No secrets in logs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any

import httpx

from v17.discord_research_bridge import command_spec

DISCORD_API = "https://discord.com/api/v10"
DEFAULT_APPLICATION_ID = "1572359272317063259"  # public, user-provided ID


class RegistrationError(RuntimeError):
    """Safe-to-display integration error; never contains bearer/token details."""


def application_id(value: str | None = None) -> str:
    aid = str(value or os.getenv("DISCORD_APPLICATION_ID") or DEFAULT_APPLICATION_ID)
    if not re.fullmatch(r"[0-9]{17,22}", aid):
        raise RegistrationError("INVALID_DISCORD_APPLICATION_ID")
    return aid


def registration_plan(app_id: str | None = None) -> dict[str, Any]:
    aid = application_id(app_id)
    return {
        "method": "POST",
        "endpoint": f"{DISCORD_API}/applications/{aid}/commands",
        "payload": command_spec(),
        "no_bot_or_guild_install_required": True,
        "prediction_authority": False,
        "can_execute": False,
        "registration_performed": False,
    }


def _credential_token(client: httpx.Client, aid: str, client_secret: str) -> str:
    result = client.post(
        f"{DISCORD_API}/oauth2/token",
        data={"grant_type": "client_credentials", "scope": "applications.commands.update"},
        auth=(aid, client_secret),
    )
    if result.status_code != 200:
        raise RegistrationError(f"DISCORD_OAUTH_TOKEN_REQUEST_FAILED_{result.status_code}")
    try:
        body = result.json()
        token = body["access_token"]
        if not isinstance(token, str) or not token.strip():
            raise ValueError("missing token")
    except (ValueError, KeyError, TypeError):
        raise RegistrationError("DISCORD_OAUTH_TOKEN_INVALID") from None
    return token


def register_command(*, app_id: str | None = None, client: httpx.Client | None = None) -> dict[str, Any]:
    """Register after secrets are provisioned. No write if credentials absent."""
    aid = application_id(app_id)
    secret = os.getenv("DISCORD_CLIENT_SECRET", "")
    if not secret:
        raise RegistrationError("DISCORD_CLIENT_SECRET_MISSING")
    own_client = client is None
    http = client or httpx.Client(timeout=8.0, follow_redirects=False)
    try:
        bearer = _credential_token(http, aid, secret)
        result = http.post(
            f"{DISCORD_API}/applications/{aid}/commands",
            headers={"Authorization": f"Bearer {bearer}"},
            json=command_spec(),
        )
        if result.status_code not in (200, 201):
            raise RegistrationError(f"DISCORD_COMMAND_REGISTER_FAILED_{result.status_code}")
        try:
            payload = result.json()
            if (payload.get("application_id") != aid
                or payload.get("name") != command_spec()["name"]
                or payload.get("type") != 3
                or 1 not in payload.get("integration_types", [])):
                raise ValueError("mismatched registration")
        except (ValueError, TypeError, AttributeError):
            raise RegistrationError("DISCORD_COMMAND_RESPONSE_INVALID") from None
        return {"registered": True, "application_id": aid,
                "command_id": payload.get("id"), "user_install": True,
                "prediction_authority": False, "can_execute": False}
    except httpx.HTTPError:
        raise RegistrationError("DISCORD_REGISTRATION_NETWORK_FAILURE") from None
    finally:
        if own_client:
            http.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Register WOW Discord user-install message command")
    p.add_argument("--application-id", default=None, help="Public Discord application ID")
    p.add_argument("--register", action="store_true", help="Perform the registration write (default is dry run)")
    args = p.parse_args()
    try:
        receipt = (register_command(app_id=args.application_id) if args.register
                   else registration_plan(args.application_id))
    except RegistrationError as exc:
        print(json.dumps({"ok": False, "code": str(exc), "can_execute": False}))
        return 1
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
