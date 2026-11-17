"""Opt-in Discord user-install bridge: research metadata, never model authority.

Standalone FastAPI entrypoint: uvicorn v17.discord_research_bridge:app.
Does not connect to user accounts via self-bots or read channel history.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlencode

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

app = FastAPI(title="WOW V17 Discord Research Intake", docs_url=None, redoc_url=None, openapi_url=None)
DISCORD_API = "https://discord.com/api/v10"
COMMAND_NAME = "Save to WOW Research"
MAX_MESSAGE_BYTES = 12_000
MAX_INTERACTION_BYTES = 60_000


def _allowed_user(user_id: str) -> bool:
    allowed = {s.strip() for s in os.getenv("WOW_DISCORD_ALLOWED_USER_IDS", "").split(",") if s.strip()}
    return bool(user_id and user_id in allowed)


def _error(message: str, *, status: int = 400):
    return JSONResponse({"status": "blocked", "code": message, "can_execute": False}, status_code=status)


def _state_secret() -> bytes:
    raw = os.getenv("WOW_DISCORD_OAUTH_STATE_SECRET", "")
    if len(raw) < 32:
        raise ValueError("OAUTH_STATE_SECRET_UNCONFIGURED")
    return raw.encode("utf-8")


def _sign_state(nonce: str, started: int) -> str:
    data = f"{nonce}.{started}"
    mac = hmac.new(_state_secret(), data.encode(), hashlib.sha256).hexdigest()
    return f"{data}.{mac}"


def _valid_state(state: str) -> bool:
    try:
        nonce, started_raw, mac = state.split(".")
        started = int(started_raw)
        if len(nonce) != 40 or not (0 <= time.time() - started <= 300):
            return False
        return hmac.compare_digest(_sign_state(nonce, started).split(".")[-1], mac)
    except (ValueError, TypeError):
        return False


def _oauth_configured() -> bool:
    return all(os.getenv(k) for k in ("DISCORD_APPLICATION_ID", "DISCORD_CLIENT_SECRET", "WOW_DISCORD_OAUTH_REDIRECT_URI"))


@app.get("/discord/oauth/start")
async def oauth_start():
    if not _oauth_configured():
        return _error("DISCORD_OAUTH_UNCONFIGURED", status=503)
    try:
        state = _sign_state(secrets.token_hex(20), int(time.time()))
    except ValueError:
        return _error("DISCORD_OAUTH_UNCONFIGURED", status=503)
    q = urlencode({
        "client_id": os.environ["DISCORD_APPLICATION_ID"],
        "redirect_uri": os.environ["WOW_DISCORD_OAUTH_REDIRECT_URI"],
        "response_type": "code",
        "scope": "identify guilds",
        "state": state,
        "prompt": "consent",
    })
    response = RedirectResponse(f"https://discord.com/oauth2/authorize?{q}", status_code=302)
    response.set_cookie("wow_discord_oauth_state", state, httponly=True, secure=True,
                        samesite="lax", max_age=300, path="/discord/oauth")
    return response


async def _oauth_identity(code: str, client: httpx.AsyncClient) -> dict:
    token = await client.post(f"{DISCORD_API}/oauth2/token", data={
        "client_id": os.environ["DISCORD_APPLICATION_ID"],
        "client_secret": os.environ["DISCORD_CLIENT_SECRET"],
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": os.environ["WOW_DISCORD_OAUTH_REDIRECT_URI"],
    }, headers={"Content-Type": "application/x-www-form-urlencoded"})
    token.raise_for_status()
    payload = token.json()
    bearer = payload["access_token"]
    user = await client.get(f"{DISCORD_API}/users/@me", headers={"Authorization": f"Bearer {bearer}"})
    user.raise_for_status()
    identity = user.json()
    if not _allowed_user(str(identity.get("id", ""))):
        raise PermissionError("DISCORD_USER_NOT_ALLOWLISTED")
    guilds = await client.get(f"{DISCORD_API}/users/@me/guilds", headers={"Authorization": f"Bearer {bearer}"})
    guilds.raise_for_status()
    rows = guilds.json()
    if not isinstance(rows, list):
        raise ValueError("DISCORD_GUILDS_INVALID")
    # No OAuth tokens, member lists or messages are retained.
    return {
        "status": "authorized_identity_discovery_only",
        "user": {"id": identity["id"], "username": identity.get("username")},
        "guilds": [{"id": g["id"], "name": g["name"], "can_manage": bool(int(g.get("permissions", "0")) & 0x20)}
                   for g in rows if isinstance(g, dict) and "id" in g and "name" in g],
        "channel_content_access": False, "oauth_token_stored": False,
        "can_execute": False,
    }


@app.get("/discord/oauth/callback")
async def oauth_callback(request: Request, code: str = "", state: str = "", iss: str = ""):
    cookie = request.cookies.get("wow_discord_oauth_state", "")
    if not state or not cookie or not hmac.compare_digest(state, cookie) or not _valid_state(state):
        return _error("DISCORD_OAUTH_STATE_INVALID", status=403)
    if iss != "https://discord.com":
        return _error("DISCORD_OAUTH_ISSUER_INVALID", status=403)
    if not code or not _oauth_configured():
        return _error("DISCORD_OAUTH_UNCONFIGURED_OR_DENIED", status=400)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            payload = await _oauth_identity(code, client)
    except PermissionError:
        return _error("DISCORD_USER_NOT_ALLOWLISTED", status=403)
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return _error("DISCORD_OAUTH_PROVIDER_FAILURE", status=502)
    resp = JSONResponse(payload, headers={"Cache-Control": "no-store"})
    resp.delete_cookie("wow_discord_oauth_state", path="/discord/oauth", secure=True, samesite="lax")
    return resp


def _verify_signature(body: bytes, signature: str, timestamp: str) -> bool:
    key = os.getenv("DISCORD_PUBLIC_KEY", "")
    try:
        at = int(timestamp)
        if not (0 <= time.time() - at <= 300):
            return False
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(key))
        pub.verify(bytes.fromhex(signature), timestamp.encode("utf-8") + body)
        return True
    except (ValueError, TypeError, InvalidSignature):
        return False


def command_spec() -> dict:
    """For an app configured to support User Install in the Discord portal."""
    return {"name": COMMAND_NAME, "type": 3, "integration_types": [1], "contexts": [0, 1, 2]}


def _ephemeral(message: str) -> dict:
    return {"type": 4, "data": {"content": message[:1900], "flags": 64, "allowed_mentions": {"parse": []}}}


def _extract_signal(payload: dict) -> dict:
    actor = payload.get("member", {}).get("user") or payload.get("user") or {}
    if not _allowed_user(str(actor.get("id", ""))):
        raise ValueError("DISCORD_ACTOR_NOT_ALLOWED")
    data = payload.get("data") or {}
    if data.get("type") != 3 or data.get("name") != COMMAND_NAME:
        raise ValueError("DISCORD_COMMAND_UNSUPPORTED")
    mid = str(data.get("target_id", ""))
    msg = (data.get("resolved") or {}).get("messages", {}).get(mid)
    if not isinstance(msg, dict) or not mid.isdecimal():
        raise ValueError("DISCORD_MESSAGE_MISSING")
    content = msg.get("content")
    if not isinstance(content, str) or not content.strip() or len(content.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ValueError("DISCORD_MESSAGE_CONTENT_UNAVAILABLE")
    iid = str(payload.get("id", ""))
    channel_id = str(payload.get("channel_id", ""))
    guild_id = str(payload.get("guild_id") or "")
    if not (iid.isdecimal() and channel_id.isdecimal() and (not guild_id or guild_id.isdecimal())):
        raise ValueError("DISCORD_IDENTITY_INVALID")
    # A user-invoked context command discloses ONE explicitly selected message.
    # Persist no message text from other people's communities; only a digest and provenance.
    return {
        "interaction_id": iid, "message_id": mid, "channel_id": channel_id,
        "guild_id": guild_id or None, "actor_id": str(actor["id"]),
        "message_author_id": str((msg.get("author") or {}).get("id") or "") or None,
        "source_created_at": msg.get("timestamp") if isinstance(msg.get("timestamp"), str) else None,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "content_bytes": len(content.encode("utf-8")),
        "source_class": "DISCORD_USER_SHARED_METADATA",
        "status": "RESEARCH_ONLY_UNVERIFIED", "prediction_authority": False, "can_execute": False,
    }


def _persist_signal(signal: dict) -> bool:
    """Commit once to private Postgres table; false = duplicate; no text in DB."""
    dsn = os.getenv("WOW_DISCORD_DATABASE_URL")
    if not dsn:
        raise RuntimeError("DISCORD_STORAGE_UNCONFIGURED")
    import psycopg  # already in WOW's Python runtime requirements
    with psycopg.connect(dsn, connect_timeout=2, options="-c statement_timeout=2000") as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO wow_discord.research_receipts
                (interaction_id,message_id,channel_id,guild_id,actor_id,message_author_id,
                 source_created_at,content_sha256,content_bytes,source_class,status,
                 prediction_authority,can_execute)
                VALUES (%(interaction_id)s,%(message_id)s,%(channel_id)s,%(guild_id)s,
                %(actor_id)s,%(message_author_id)s,%(source_created_at)s,
                %(content_sha256)s,%(content_bytes)s,%(source_class)s,%(status)s,
                %(prediction_authority)s,%(can_execute)s)
                ON CONFLICT (interaction_id) DO NOTHING RETURNING interaction_id""", signal)
            created = cur.fetchone() is not None
        conn.commit()
    return created


@app.post("/discord/interactions")
async def discord_interactions(request: Request):
    raw = await request.body()
    if len(raw) > MAX_INTERACTION_BYTES:
        return _error("DISCORD_INTERACTION_TOO_LARGE", status=413)
    if not _verify_signature(raw, request.headers.get("x-signature-ed25519", ""),
                             request.headers.get("x-signature-timestamp", "")):
        return _error("DISCORD_SIGNATURE_INVALID", status=401)
    try:
        interaction = json.loads(raw)
        if not isinstance(interaction, dict):
            raise ValueError("not object")
    except (ValueError, UnicodeDecodeError):
        return _error("DISCORD_PAYLOAD_INVALID", status=400)
    if interaction.get("type") == 1:
        return {"type": 1}
    if interaction.get("type") != 2:
        return _ephemeral("WOW: unsupported Discord interaction. No research was saved.")
    try:
        signal = _extract_signal(interaction)
        created = _persist_signal(signal)
    except ValueError:
        return _ephemeral("WOW: message could not be accepted. Check account access and message content.")
    except Exception:
        # No exception details or private text in public response or logs.
        return _ephemeral("WOW: research storage unavailable. Nothing was saved.")
    if created:
        return _ephemeral("WOW: research-only source receipt saved (message fingerprint and provenance; no content archived). No prediction or bet was generated.")
    return _ephemeral("WOW: this interaction was already received; no duplicate created.")


@app.get("/healthz")
def healthz():
    return {"status": "ok", "discord_configured": bool(os.getenv("DISCORD_PUBLIC_KEY")),
            "research_storage_configured": bool(os.getenv("WOW_DISCORD_DATABASE_URL")),
            "prediction_authority": False, "can_execute": False}
