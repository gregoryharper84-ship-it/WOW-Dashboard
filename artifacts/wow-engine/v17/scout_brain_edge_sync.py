"""Secretless Scout Brain persistence through the governed Supabase Edge Function.

The caller must be the exact protected-main Scout persistence GitHub workflow.
GitHub OIDC is short-lived; no Supabase database or service-role credential is
stored in GitHub. The Edge Function re-validates governance and writes only
research-layer state with can_execute=false.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from v17.github_actions_oidc_client import GitHubOIDCMintError, mint_github_actions_oidc

DEFAULT_URL = "https://iczfhsmjrrafhvcpmqhr.supabase.co/functions/v1/wow-v17-scout-brain-persist"


def sync(input_path: str, receipt_path: str) -> dict:
    handoff = json.loads(Path(input_path).read_text(encoding="utf-8"))
    if handoff.get("can_execute") is not False:
        raise RuntimeError("SCOUT_HANDOFF_GOVERNANCE_INVALID")
    try:
        token = mint_github_actions_oidc(force=True)
    except GitHubOIDCMintError as exc:
        raise RuntimeError(str(exc)) from exc

    url = str(os.environ.get("WOW_SCOUT_PERSIST_URL") or DEFAULT_URL).strip()
    req = Request(
        url,
        data=json.dumps(handoff, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            code = payload.get("code") if isinstance(payload, dict) else None
        except Exception:
            code = None
        raise RuntimeError(code or f"SCOUT_EDGE_PERSIST_HTTP_{exc.code}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("SCOUT_EDGE_PERSIST_TRANSPORT_FAILED") from exc

    if not isinstance(payload, dict) or payload.get("ok") is not True or payload.get("can_execute") is not False:
        raise RuntimeError(str(payload.get("code") if isinstance(payload, dict) else "SCOUT_EDGE_PERSIST_RESPONSE_INVALID"))
    receipt = {k: v for k, v in payload.items() if k != "ok"}
    Path(receipt_path).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    receipt = sync(args.input, args.receipt)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
