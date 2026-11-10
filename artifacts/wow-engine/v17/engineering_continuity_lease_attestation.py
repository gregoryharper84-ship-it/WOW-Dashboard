"""Bounded read-only attestation of the existing WOW V17 DB incident-writer fence.

Never claims/renews/releases a lease, creates a writer, dispatches a workflow,
or authorizes a merge/deployment. The protected worker is responsible for
authenticating owner provenance and acquiring its own distinct writer lease.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OWNER_ID = re.compile(r"^github-actions:[0-9]+:[1-9][0-9]*:[a-zA-Z0-9_.-]{3,100}$")
ENDPOINT = "/rest/v1/rpc/wow_engineering_check_writer_lease"


class LeaseAttestationError(RuntimeError):
    """Typed result without source error messages, URLs, headers or secrets."""


@dataclass(frozen=True)
class WriterClaim:
    incident: int
    owner_id: str
    epoch: int


def attest_writer_claim(claim: WriterClaim, *, endpoint: str | None = None,
                        credential: str | None = None, transport=None) -> bool:
    """True only for an active matching epoch in the actual lease database.

    It is a *fence check*, not a signature that independently proves the
    principal's identity. The authorized host must supply the server-side
    owner and epoch from an independently authenticated workflow context,
    never from user/issue/LLM text.
    """
    if not isinstance(claim, WriterClaim):
        raise LeaseAttestationError("BAD_WRITER_CLAIM")
    if (type(claim.incident) is not int or claim.incident <= 0
            or type(claim.epoch) is not int or claim.epoch <= 0
            or not isinstance(claim.owner_id, str)
            or not OWNER_ID.fullmatch(claim.owner_id)):
        raise LeaseAttestationError("BAD_WRITER_PROVENANCE")
    url = endpoint if endpoint is not None else os.environ.get("SUPABASE_URL", "")
    key = credential if credential is not None else os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if (not isinstance(url, str) or not url.startswith("https://")
            or "@" in url or "?" in url or "#" in url or not isinstance(key, str)
            or not key.strip()):
        raise LeaseAttestationError("LEASE_STORE_UNAVAILABLE")
    request = Request(
        url.rstrip("/") + ENDPOINT,
        data=json.dumps({
            "p_incident_id": claim.incident,
            "p_owner_id": claim.owner_id,
            "p_epoch": claim.epoch,
        }, sort_keys=True).encode("utf-8"),
        headers={"apikey": key, "Authorization": "Bearer " + key,
                 "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    opener = transport if transport is not None else urlopen
    try:
        with opener(request, timeout=6) as response:
            # Bound response size and reject arbitrary object-shaped "success".
            raw = response.read(17)
        if len(raw) > 16:
            raise LeaseAttestationError("LEASE_ATTESTATION_INVALID")
        value = json.loads(raw)
    except LeaseAttestationError:
        raise
    except (HTTPError, URLError, OSError, ValueError, TypeError, TimeoutError):
        # Do not include exception strings: HTTP errors may carry credential URLs.
        raise LeaseAttestationError("LEASE_ATTESTATION_UNAVAILABLE") from None
    if type(value) is not bool:
        raise LeaseAttestationError("LEASE_ATTESTATION_INVALID")
    return value
