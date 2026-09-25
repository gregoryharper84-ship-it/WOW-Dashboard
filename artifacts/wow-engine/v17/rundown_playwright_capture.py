"""Authenticated rendered TheRundown board collector for WOW V17 Scout.

The public ``therundown.io/odds`` routes are marketing/SEO landing pages. They
contain static preview/ticker prices and MUST NOT be accepted as Scout evidence.
The real odds screen is therefore acquired only after a successful account
session is established with Playwright.

This module is acquisition-only:
- sportsbook/external prices remain evidence only;
- ``prediction_authority=false``;
- Scout ceiling remains ``RESEARCH_INTEREST``;
- ``can_execute=false`` is immutable here.

Credentials are read only from runtime secrets (``RUNDOWN_EMAIL`` and
``RUNDOWN_PASSWORD``). Optional Playwright ``storage_state`` may be supplied as
base64 JSON through ``RUNDOWN_STORAGE_STATE_B64``. Credentials, cookies, tokens,
and query strings are never written to the capture artifact.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import Page, Response, TimeoutError as PlaywrightTimeoutError, sync_playwright

CAN_EXECUTE = False
PREDICTION_AUTHORITY = False
RESEARCH_CEILING = "RESEARCH_INTEREST"
BOARD_URL = os.getenv("WOW_RUNDOWN_BOARD_URL", "https://therundown.io/odds/")
LOGIN_URL = os.getenv("WOW_RUNDOWN_LOGIN_URL", "https://auth.therundown.io/login/")
TIMEOUT_MS = int(os.getenv("WOW_RUNDOWN_PLAYWRIGHT_TIMEOUT_MS", "45000"))

SPORT_LABELS = (
    "MLB", "NFL", "NCAAF", "NBA", "WNBA", "NCAAB", "NHL", "UFC",
    "ATP", "WTA", "MLS", "EPL",
)

ODDS_RE = re.compile(r"(?<!\d)[+-]\d{3,5}(?!\d)")
MFA_TEXT_RE = re.compile(
    r"two[- ]factor|verification code|authenticator|security code|captcha|verify you are human|one[- ]time code",
    re.I,
)
MARKETING_CLASS_TOKENS = (
    "OddsLanding", "OddsLandingHero", "OddsPreviewSection", "TickerSection",
    "SportsbookCarousel", "CoverageSection", "FinalCtaSection",
)


def _safe_url(url: str) -> str:
    """Return origin/path only. Never persist query strings or fragments."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _storage_state_file() -> str | None:
    raw = os.getenv("RUNDOWN_STORAGE_STATE_B64", "").strip()
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
        data = json.loads(decoded)
        if not isinstance(data, dict):
            raise ValueError("storage state must be an object")
    except Exception as exc:
        raise RuntimeError("RUNDOWN_STORAGE_STATE_INVALID") from exc
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(data, handle)
    handle.close()
    return handle.name


def _first_visible(page: Page, selectors: list[str]):
    for selector in selectors:
        loc = page.locator(selector)
        try:
            if loc.count() and loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    return None


def _is_marketing_landing(page: Page) -> bool:
    """True for the public SEO surface proven to contain demo/static prices."""
    probes = [
        '[class*="OddsLanding_landing"]',
        '[class*="OddsLandingHero_hero"]',
        '[class*="OddsPreviewSection_oddsRow"]',
    ]
    for selector in probes:
        try:
            if page.locator(selector).count():
                return True
        except Exception:
            continue
    return False


def _is_auth_page(page: Page) -> bool:
    try:
        if "auth.therundown.io" in urlsplit(page.url).netloc.lower():
            return True
        if page.locator('input[type="password"]').count():
            return True
    except Exception:
        pass
    return False


def _credentials() -> tuple[str, str]:
    return os.getenv("RUNDOWN_EMAIL", "").strip(), os.getenv("RUNDOWN_PASSWORD", "")


def _body_sample(page: Page, limit: int = 5000) -> str:
    try:
        return " ".join(page.locator("body").inner_text(timeout=5000).split())[:limit]
    except Exception:
        return ""


def _wait_after_navigation(page: Page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=12000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(1200)


def _login(page: Page) -> tuple[bool, str | None]:
    """Establish an account session using runtime secrets only."""
    email, password = _credentials()
    if not email or not password:
        return False, "RUNDOWN_AUTH_CREDENTIALS_UNCONFIGURED"

    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
    _wait_after_navigation(page)

    email_input = _first_visible(
        page,
        ['input[type="email"]', 'input[name="email"]', 'input[autocomplete="username"]'],
    )
    password_input = _first_visible(
        page,
        ['input[type="password"]', 'input[name="password"]', 'input[autocomplete="current-password"]'],
    )
    if email_input is None or password_input is None:
        return False, "RUNDOWN_AUTH_FORM_UNRECOGNISED"

    email_input.fill(email)
    password_input.fill(password)
    submit = _first_visible(
        page,
        ['button[type="submit"]', 'button:has-text("Sign In")', 'button:has-text("Log In")'],
    )
    if submit is None:
        return False, "RUNDOWN_AUTH_FORM_UNRECOGNISED"

    submit.click()
    try:
        page.wait_for_url(lambda url: "auth.therundown.io" not in url, timeout=TIMEOUT_MS)
    except PlaywrightTimeoutError:
        pass
    _wait_after_navigation(page)

    text = _body_sample(page)
    if MFA_TEXT_RE.search(text):
        return False, "RUNDOWN_AUTH_REPAIR_REQUIRED"
    if _is_auth_page(page):
        return False, "RUNDOWN_AUTH_FAILED"
    return True, None


def _response_shape(response: Response) -> dict[str, Any] | None:
    """Sanitized structure-only metadata for account odds fetches."""
    try:
        content_type = (response.headers.get("content-type") or "").lower()
        resource_type = response.request.resource_type
        if resource_type not in {"xhr", "fetch", "websocket"} and "json" not in content_type:
            return None
        host = urlsplit(response.url).netloc.lower()
        if not ("therundown" in host or "rundown" in host):
            return None
        shape: dict[str, Any] = {
            "url": _safe_url(response.url),
            "status": response.status,
            "resource_type": resource_type,
            "content_type": content_type.split(";")[0],
        }
        if "json" not in content_type:
            return shape
        payload = response.json()
    except Exception:
        return None

    shape["payload_type"] = type(payload).__name__
    if isinstance(payload, dict):
        shape["top_level_keys"] = sorted(str(k) for k in payload.keys())[:100]
        for key in ("events", "data", "markets", "odds", "sports", "results", "games", "players"):
            value = payload.get(key)
            if isinstance(value, list):
                shape[f"{key}_count"] = len(value)
            elif isinstance(value, dict):
                shape[f"{key}_keys"] = sorted(str(k) for k in value.keys())[:60]
    elif isinstance(payload, list):
        shape["length"] = len(payload)
        if payload and isinstance(payload[0], dict):
            shape["item_keys"] = sorted(str(k) for k in payload[0].keys())[:100]
    return shape


def _inside_marketing(node) -> bool:
    try:
        for token in MARKETING_CLASS_TOKENS:
            if node.locator(f'xpath=ancestor-or-self::*[contains(@class,"{token}")]').count():
                return True
    except Exception:
        return False
    return False


def _real_row_texts(page: Page) -> list[str]:
    """Capture only account-screen odds rows; marketing/demo DOM is rejected."""
    selectors = [
        '[data-testid*="odds-row"]',
        '[data-testid*="event-row"]',
        '[data-testid*="market-row"]',
        '[class*="oddsRow"]',
        '[class*="eventRow"]',
        '[class*="marketRow"]',
        'main tr',
        'main [role="row"]',
    ]
    seen: set[str] = set()
    rows: list[str] = []
    for selector in selectors:
        loc = page.locator(selector)
        try:
            count = min(loc.count(), 2000)
        except Exception:
            continue
        for idx in range(count):
            try:
                node = loc.nth(idx)
                if not node.is_visible() or _inside_marketing(node):
                    continue
                text = " ".join(node.inner_text(timeout=2500).split())
            except Exception:
                continue
            if len(text) < 8 or text in seen or not ODDS_RE.search(text):
                continue
            seen.add(text)
            rows.append(text[:2500])
    return rows


def _click_sport(page: Page, label: str) -> bool:
    candidates = [
        page.get_by_role("tab", name=re.compile(rf"^{re.escape(label)}$", re.I)),
        page.get_by_role("button", name=re.compile(rf"^{re.escape(label)}$", re.I)),
        page.get_by_role("link", name=re.compile(rf"^{re.escape(label)}$", re.I)),
        page.get_by_text(re.compile(rf"^{re.escape(label)}$", re.I), exact=True),
    ]
    for loc in candidates:
        try:
            if loc.count() and loc.first.is_visible() and not _inside_marketing(loc.first):
                loc.first.click(timeout=5000)
                page.wait_for_timeout(900)
                return True
        except Exception:
            continue
    return False


def capture_board(output: str | Path) -> dict[str, Any]:
    network_shapes: list[dict[str, Any]] = []
    storage_path = _storage_state_file()
    captured_by_sport: dict[str, list[str]] = defaultdict(list)
    clicked_sports: list[str] = []
    auth_status = "UNKNOWN"
    auth_blocker: str | None = None
    board_surface = "UNKNOWN"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context_kwargs: dict[str, Any] = {
            "viewport": {"width": 1680, "height": 1300},
            "locale": "en-US",
        }
        if storage_path:
            context_kwargs["storage_state"] = storage_path
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        page.set_default_timeout(TIMEOUT_MS)

        def on_response(response: Response) -> None:
            shape = _response_shape(response)
            if shape and shape not in network_shapes and len(network_shapes) < 500:
                network_shapes.append(shape)

        page.on("response", on_response)

        try:
            page.goto(BOARD_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            _wait_after_navigation(page)

            if storage_path and not _is_auth_page(page) and not _is_marketing_landing(page):
                auth_status = "SESSION_REUSED"
            else:
                ok, blocker = _login(page)
                if not ok:
                    auth_status = "BLOCKED"
                    auth_blocker = blocker
                else:
                    auth_status = "AUTHENTICATED"
                    page.goto(BOARD_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                    _wait_after_navigation(page)

            if auth_blocker is None:
                if _is_auth_page(page):
                    auth_blocker = "RUNDOWN_AUTH_FAILED"
                    auth_status = "BLOCKED"
                elif _is_marketing_landing(page):
                    auth_blocker = "RUNDOWN_ACCOUNT_ODDS_SURFACE_UNAVAILABLE"
                    board_surface = "MARKETING_LANDING"
                else:
                    board_surface = "AUTHENTICATED_ODDS_APP"

            if auth_blocker is None:
                initial_rows = _real_row_texts(page)
                if initial_rows:
                    captured_by_sport["INITIAL"] = initial_rows

                for sport in SPORT_LABELS:
                    if _click_sport(page, sport):
                        clicked_sports.append(sport)
                        rows = _real_row_texts(page)
                        if rows:
                            captured_by_sport[sport] = rows

                state_out = os.getenv("RUNDOWN_STORAGE_STATE_OUT", "").strip()
                if state_out:
                    context.storage_state(path=state_out)
        finally:
            context.close()
            browser.close()
            if storage_path:
                try:
                    os.unlink(storage_path)
                except OSError:
                    pass

    unique_rows = {row for rows in captured_by_sport.values() for row in rows}
    if auth_blocker:
        status = "DISCOVERY_BLOCKED"
        reason_code = auth_blocker
    elif not unique_rows:
        status = "DISCOVERY_BLOCKED"
        reason_code = "RUNDOWN_AUTHENTICATED_BOARD_EMPTY"
    else:
        status = "DISCOVERY_COMPLETE"
        reason_code = None

    result = {
        "schema": "wow.v17.rundown.authenticated-board.capture.v2",
        "source": "THERUNDOWN_AUTHENTICATED_ODDS_APP",
        "source_url": _safe_url(BOARD_URL),
        "status": status,
        "reason_code": reason_code,
        "auth_status": auth_status,
        "board_surface": board_surface,
        "can_execute": CAN_EXECUTE,
        "prediction_authority": PREDICTION_AUTHORITY,
        "research_only": True,
        "research_ceiling": RESEARCH_CEILING,
        "sports_attempted": list(SPORT_LABELS),
        "sports_clicked": clicked_sports,
        "sports_with_rows": sorted(captured_by_sport),
        "row_count": len(unique_rows),
        "rows_by_sport": dict(captured_by_sport),
        "network_json_shapes": network_shapes[:500],
        "coverage": {
            "sports_attempted": len(SPORT_LABELS),
            "sports_clicked": len(clicked_sports),
            "sports_with_rows": len(captured_by_sport),
            "rows_captured": len(unique_rows),
            "marketing_rows_accepted": 0,
        },
    }
    Path(output).write_text(json.dumps(result, indent=2, sort_keys=True))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = capture_board(args.output)
    print(json.dumps({
        "status": result["status"],
        "reason_code": result["reason_code"],
        "auth_status": result["auth_status"],
        "board_surface": result["board_surface"],
        "sports_clicked": result["coverage"]["sports_clicked"],
        "sports_with_rows": result["coverage"]["sports_with_rows"],
        "rows_captured": result["coverage"]["rows_captured"],
        "marketing_rows_accepted": result["coverage"]["marketing_rows_accepted"],
        "network_json_responses": len(result["network_json_shapes"]),
        "can_execute": result["can_execute"],
    }, sort_keys=True))
    return 0 if result["status"] == "DISCOVERY_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
