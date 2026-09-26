"""Strict authentication for the WOW V17 Engineering Auditor event boundary.

Only the existing WOW Action key or exact GitHub Actions OIDC identities for the
engineering-auditor workflows are accepted. This boundary never authorizes wager
execution or probability publication.
"""
from __future__ import annotations

import os
import secrets
from typing import Any

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = f"{ISSUER}/.well-known/jwks"
AUDIENCE = "wow-v17-multiscout"
REPOSITORY = "gregoryharper84-ship-it/WOW-Dashboard"
REPOSITORY_ID = "1240256887"
REPOSITORY_OWNER_ID = "285088163"
MAIN_REF = "refs/heads/main"
EVENT_WORKFLOW_REF = f"{REPOSITORY}/.github/workflows/wow-v17-engineering-auditor-events.yml@{MAIN_REF}"
CODE_HEALTH_WORKFLOW_REF = f"{REPOSITORY}/.github/workflows/wow-v17-engineering-auditor-code-health.yml@{MAIN_REF}"
ALLOWED_WORKFLOW_REFS = frozenset({EVENT_WORKFLOW_REF, CODE_HEALTH_WORKFLOW_REF})
ALLOWED_EVENTS = frozenset({"push", "pull_request", "issues", "issue_comment", "pull_request_review", "workflow_dispatch"})
PR_EVENTS = frozenset({"pull_request", "pull_request_review"})


class EngineeringAuditorAuthError(ValueError):
    pass


def validate_engineering_auditor_claims(claims: dict[str, Any]) -> dict[str, Any]:
    required = {
        "repository": REPOSITORY,
        "repository_id": REPOSITORY_ID,
        "repository_owner_id": REPOSITORY_OWNER_ID,
        "runner_environment": "github-hosted",
    }
    for field, expected in required.items():
        if str(claims.get(field) or "") != expected:
            raise EngineeringAuditorAuthError(f"ENGINEERING_AUDITOR_OIDC_{field.upper()}_MISMATCH")
    workflow_ref = str(claims.get("workflow_ref") or "")
    if workflow_ref not in ALLOWED_WORKFLOW_REFS:
        raise EngineeringAuditorAuthError("ENGINEERING_AUDITOR_OIDC_WORKFLOW_REF_MISMATCH")
    event_name = str(claims.get("event_name") or "")
    if event_name not in ALLOWED_EVENTS:
        raise EngineeringAuditorAuthError("ENGINEERING_AUDITOR_OIDC_EVENT_NOT_ALLOWED")
    if event_name in PR_EVENTS:
        if str(claims.get("base_ref") or "") != MAIN_REF:
            raise EngineeringAuditorAuthError("ENGINEERING_AUDITOR_OIDC_BASE_REF_MISMATCH")
    elif str(claims.get("ref") or "") != MAIN_REF:
        raise EngineeringAuditorAuthError("ENGINEERING_AUDITOR_OIDC_REF_MISMATCH")
    return dict(claims)


def verify_engineering_auditor_oidc(token: str, *, jwk_client: PyJWKClient | None = None) -> dict[str, Any]:
    if not isinstance(token, str) or not token.strip():
        raise EngineeringAuditorAuthError("ENGINEERING_AUDITOR_OIDC_TOKEN_MISSING")
    try:
        client = jwk_client or PyJWKClient(JWKS_URL, cache_keys=True)
        signing_key = client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iat", "nbf", "jti"]},
        )
    except Exception as exc:
        raise EngineeringAuditorAuthError("ENGINEERING_AUDITOR_OIDC_SIGNATURE_OR_STANDARD_CLAIMS_INVALID") from exc
    return validate_engineering_auditor_claims(claims)


def engineering_auditor_auth(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"code": "ENGINEERING_AUDITOR_AUTH_REQUIRED", "can_execute": False})
    supplied = authorization[len("Bearer ") :]
    configured = os.getenv("WOW_ACTION_API_KEY")
    if configured and secrets.compare_digest(supplied, configured):
        return "WOW_ACTION_API_KEY"
    try:
        verify_engineering_auditor_oidc(supplied)
    except EngineeringAuditorAuthError as exc:
        raise HTTPException(status_code=401, detail={"code": "ENGINEERING_AUDITOR_AUTH_INVALID", "can_execute": False}) from exc
    return "GITHUB_ACTIONS_OIDC"


__all__ = [
    "ALLOWED_WORKFLOW_REFS",
    "AUDIENCE",
    "CODE_HEALTH_WORKFLOW_REF",
    "EVENT_WORKFLOW_REF",
    "EngineeringAuditorAuthError",
    "engineering_auditor_auth",
    "validate_engineering_auditor_claims",
    "verify_engineering_auditor_oidc",
]
