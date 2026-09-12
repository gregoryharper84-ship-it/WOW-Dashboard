from __future__ import annotations
from typing import Any
import jwt
from jwt import PyJWKClient
ISSUER="https://token.actions.githubusercontent.com"
JWKS_URL=f"{ISSUER}/.well-known/jwks"
AUDIENCE="wow-v17-multiscout"
REPOSITORY="gregoryharper84-ship-it/WOW-Dashboard"
REPOSITORY_ID="1240256887"
REPOSITORY_OWNER_ID="285088163"
REF="refs/heads/main"
WORKFLOW_REF=f"{REPOSITORY}/.github/workflows/wow-v17-nightly-multiscout.yml@{REF}"
ALLOWED_EVENTS=frozenset({"push","schedule","workflow_dispatch"})
class GitHubOIDCValidationError(ValueError): pass
def validate_github_actions_claims(claims:dict[str,Any])->dict[str,Any]:
    checks={"repository":REPOSITORY,"repository_id":REPOSITORY_ID,"repository_owner_id":REPOSITORY_OWNER_ID,"ref":REF,"workflow_ref":WORKFLOW_REF,"runner_environment":"github-hosted"}
    for field,expected in checks.items():
        if str(claims.get(field) or "")!=expected: raise GitHubOIDCValidationError(f"GITHUB_OIDC_{field.upper()}_MISMATCH")
    if str(claims.get("event_name") or "") not in ALLOWED_EVENTS: raise GitHubOIDCValidationError("GITHUB_OIDC_EVENT_NOT_ALLOWED")
    return dict(claims)
def verify_github_actions_oidc(token:str,*,jwk_client:PyJWKClient|None=None)->dict[str,Any]:
    if not isinstance(token,str) or not token.strip(): raise GitHubOIDCValidationError("GITHUB_OIDC_TOKEN_MISSING")
    try:
        client=jwk_client or PyJWKClient(JWKS_URL,cache_keys=True)
        key=client.get_signing_key_from_jwt(token)
        claims=jwt.decode(token,key.key,algorithms=["RS256"],audience=AUDIENCE,issuer=ISSUER,options={"require":["exp","iat","nbf","jti"]})
    except Exception as exc: raise GitHubOIDCValidationError("GITHUB_OIDC_SIGNATURE_OR_STANDARD_CLAIMS_INVALID") from exc
    return validate_github_actions_claims(claims)
