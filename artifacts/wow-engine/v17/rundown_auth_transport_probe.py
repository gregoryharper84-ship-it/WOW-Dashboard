"""Safe one-shot probe for the authenticated TheRundown request transport.

Only status codes and credential *names* are emitted. No header, cookie, token,
local-storage, password, or email values are serialized or printed.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from playwright.sync_api import Request, sync_playwright

from v17.rundown_authenticated_scout import BOARD_URL, TIMEOUT_MS, _login, _wait

_EVENT_RE = re.compile(r"^/api/v2/sports/\d+/events/\d{4}-\d{2}-\d{2}$")
_ILLEGAL = {"host", "content-length", "accept-encoding", "connection"}


def main() -> int:
    native_headers: dict[str, str] = {}
    native_raw_names: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1680, "height": 1300}, locale="en-US")
        page = context.new_page()
        page.set_default_timeout(TIMEOUT_MS)

        def capture(request: Request) -> None:
            nonlocal native_raw_names
            parts = urlsplit(request.url)
            if parts.netloc.lower() != "therundown.io" or not _EVENT_RE.match(parts.path) or native_headers:
                return
            try:
                raw = request.all_headers()
            except Exception:
                raw = request.headers
            native_raw_names = sorted(str(name).lower() for name in raw)
            for raw_name, raw_value in raw.items():
                name = str(raw_name).lower()
                if name.startswith(":") or name.startswith("sec-") or name in _ILLEGAL:
                    continue
                native_headers[name] = str(raw_value)

        page.on("request", capture)
        page.goto(BOARD_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        _wait(page)
        ok, blocker = _login(page)
        if not ok:
            print(json.dumps({"status": "AUTH_BLOCKED", "reason_code": blocker}, sort_keys=True))
            return 2
        page.goto(BOARD_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        _wait(page)
        page.wait_for_timeout(6000)

        cookies = context.cookies(["https://therundown.io", "https://auth.therundown.io"])
        cookie_names = sorted({str(row.get("name")) for row in cookies if row.get("name")})
        storage_names = page.evaluate("""() => ({
          local: Object.keys(window.localStorage || {}).sort(),
          session: Object.keys(window.sessionStorage || {}).sort(),
        })""")

        no_header_status = int(context.request.get(
            "https://therundown.io/api/v1/sports", timeout=TIMEOUT_MS, fail_on_status_code=False
        ).status)
        replay_status = int(context.request.get(
            "https://therundown.io/api/v1/sports", headers=native_headers,
            timeout=TIMEOUT_MS, fail_on_status_code=False
        ).status) if native_headers else None
        browser_fetch = page.evaluate("""async () => {
          try {
            const response = await fetch('/api/v1/sports', {credentials: 'include'});
            return {status: response.status, contentType: response.headers.get('content-type') || ''};
          } catch (err) {
            return {status: 0, contentType: ''};
          }
        }""")

        safe_names = sorted(name for name in native_headers if name not in {"cookie", "authorization"})
        result = {
            "status": "PROBE_COMPLETE",
            "can_execute": False,
            "native_raw_header_names": native_raw_names,
            "native_replay_header_names_redacted": safe_names,
            "native_has_cookie": "cookie" in native_headers,
            "native_has_authorization": "authorization" in native_headers,
            "cookie_names": cookie_names,
            "local_storage_key_names": storage_names.get("local", []),
            "session_storage_key_names": storage_names.get("session", []),
            "context_request_no_header_status": no_header_status,
            "context_request_native_replay_status": replay_status,
            "browser_fetch_status": int(browser_fetch.get("status") or 0),
            "browser_fetch_content_type": str(browser_fetch.get("contentType") or "").split(";")[0],
        }
        print(json.dumps(result, sort_keys=True))
        context.close(); browser.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
