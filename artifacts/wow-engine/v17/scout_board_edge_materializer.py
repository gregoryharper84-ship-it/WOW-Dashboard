"""Secretless Scout board materialization through the governed Supabase Edge boundary."""
from __future__ import annotations
import argparse, json, os
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from v17.github_actions_oidc_client import GitHubOIDCMintError, mint_github_actions_oidc

DEFAULT_URL = "https://iczfhsmjrrafhvcpmqhr.supabase.co/functions/v1/wow-v17-scout-brain-persist"

def materialize(slate_date: date) -> dict:
    url = str(os.environ.get("WOW_SCOUT_PERSIST_URL") or DEFAULT_URL).strip()
    try:
        token = mint_github_actions_oidc()
    except GitHubOIDCMintError as exc:
        raise RuntimeError(str(exc)) from exc
    payload = {"persist_phase": "MATERIALIZE", "slate_date": slate_date.isoformat(), "can_execute": False}
    req = Request(url, data=json.dumps(payload, separators=(",", ":")).encode(), headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json",
    }, method="POST")
    try:
        with urlopen(req, timeout=120) as response:
            body = json.loads(response.read().decode())
    except HTTPError as exc:
        try:
            body = json.loads(exc.read().decode())
            code = body.get("code") if isinstance(body, dict) else None
        except Exception:
            code = None
        raise RuntimeError(code or f"SCOUT_BOARD_EDGE_HTTP_{exc.code}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("SCOUT_BOARD_EDGE_TRANSPORT_FAILED") from exc
    if not isinstance(body, dict) or body.get("ok") is not True or body.get("can_execute") is not False:
        raise RuntimeError(str(body.get("code") if isinstance(body, dict) else "SCOUT_BOARD_EDGE_RESPONSE_INVALID"))
    return body

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--receipt")
    args = parser.parse_args()
    result = materialize(date.fromisoformat(args.date))
    if args.receipt:
        Path(args.receipt).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
