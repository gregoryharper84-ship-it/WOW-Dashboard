"""Rendered TheRundown board collector for WOW V17 Scout.

This module is acquisition-only. It never assigns WOW probabilities, never
approves wagers, and never changes ``can_execute=false``. The rendered
TheRundown board is treated as sportsbook/market evidence only.

The collector intentionally supports two auth modes without storing secrets in
source control:

* a Playwright storage-state blob supplied via ``RUNDOWN_STORAGE_STATE_B64``;
* automatic sign-in using ``RUNDOWN_EMAIL`` + ``RUNDOWN_PASSWORD`` when the
  board requires authentication.

If MFA/CAPTCHA or another interactive challenge blocks unattended login, the
collector fails closed with ``RUNDOWN_AUTH_REPAIR_REQUIRED``.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import Page, Response, TimeoutError as PlaywrightTimeoutError, sync_playwright

CAN_EXECUTE = False
PREDICTION_AUTHORITY = False
RESEARCH_CEILING = "RESEARCH_INTEREST"
BOARD_URL = os.getenv("WOW_RUNDOWN_BOARD_URL", "https://therundown.io/odds/")
TIMEOUT_MS = int(os.getenv("WOW_RUNDOWN_PLAYWRIGHT_TIMEOUT_MS", "45000"))

SPORT_LABELS = (
    "MLB",
    "NFL",
    "NCAAF",
    "NBA",
    "WNBA",
    "NCAAB",
    "NHL",
    "UFC",
    "ATP",
    "WTA",
    "MLS",
    "EPL",
)

ODDS_RE = re.compile(r"(?<!\d)[+-]\d{3,4}(?!\d)")
LOGIN_TEXT_RE = re.compile(r"sign\s*in|log\s*in", re.I)
MFA_TEXT_RE = re.compile(r"two[- ]factor|verification code|authenticator|security code|captcha|verify you are human", re.I)


def _safe_url(url: str) -> str:
    """Strip query/fragment so browser logs cannot expose tokens."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _storage_state_file() -> str | None:
    raw = os.getenv("RUNDOWN_STORAGE_STATE_B64", "").strip()
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw).decode("utf-8")
        data = json.loads(decoded)
    except Exception as exc:  # pragma: no cover - defensive configuration boundary
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


def _needs_login(page: Page) -> bool:
    # The marketing/public surface can contain a Sign In CTA while still showing
    # odds. Only treat auth as required if the page also exposes an auth form or
    # the board itself has no usable odds rows.
    if page.locator('input[type="password"]').count():
        return True
    auth_url = "auth.therundown.io" in page.url.lower()
    if auth_url:
        return True
    return False


def _has_odds_rows(page: Page) -> bool:
    selectors = [
        '[class*="OddsPreviewSection_oddsRow"]',
        '[class*="oddsRow"]',
        '[data-testid*="odds-row"]',
        '[data-testid*="event-row"]',
    ]
    for selector in selectors:
        loc = page.locator(selector)
        try:
            if loc.count() > 0:
                return True
        except Exception:
            continue
    return False


def _attempt_login(page: Page) -> tuple[bool, str | None]:
    if not _needs_login(page):
        return True, None

    email = os.getenv("RUNDOWN_EMAIL", "").strip()
    password = os.getenv("RUNDOWN_PASSWORD", "")
    if not email or not password:
        return False, "RUNDOWN_AUTH_CREDENTIALS_UNCONFIGURED"

    email_input = _first_visible(page, ['input[type="email"]', 'input[name="email"]', 'input[autocomplete="username"]'])
    if email_input is None:
        return False, "RUNDOWN_AUTH_FORM_UNRECOGNISED"
    email_input.fill(email)

    # Some auth flows submit email before presenting password.
    password_input = _first_visible(page, ['input[type="password"]', 'input[name="password"]', 'input[autocomplete="current-password"]'])
    if password_input is None:
        submit = _first_visible(page, ['button[type="submit"]', 'button:has-text("Continue")', 'button:has-text("Next")'])
        if submit is not None:
            submit.click()
            page.wait_for_timeout(700)
            password_input = _first_visible(page, ['input[type="password"]', 'input[name="password"]', 'input[autocomplete="current-password"]'])
    if password_input is None:
        return False, "RUNDOWN_AUTH_FORM_UNRECOGNISED"

    password_input.fill(password)
    submit = _first_visible(page, ['button[type="submit"]', 'button:has-text("Sign In")', 'button:has-text("Log In")'])
    if submit is None:
        return False, "RUNDOWN_AUTH_FORM_UNRECOGNISED"
    submit.click()

    try:
        page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_MS)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(1000)

    # Read only a small auth-surface text sample. Do not persist it.
    auth_text = ""
    try:
        auth_text = page.locator("body").inner_text(timeout=5000)[:4000]
    except Exception:
        pass
    if MFA_TEXT_RE.search(auth_text):
        return False, "RUNDOWN_AUTH_REPAIR_REQUIRED"
    if _needs_login(page):
        return False, "RUNDOWN_AUTH_FAILED"
    return True, None


def _response_shape(response: Response) -> dict[str, Any] | None:
    """Return safe structural metadata for TheRundown JSON responses."""
    try:
        content_type = (response.headers.get("content-type") or "").lower()
        if "json" not in content_type:
            return None
        host = urlsplit(response.url).netloc.lower()
        if "therundown" not in host:
            return None
        payload = response.json()
    except Exception:
        return None

    shape: dict[str, Any] = {
        "url": _safe_url(response.url),
        "status": response.status,
        "payload_type": type(payload).__name__,
    }
    if isinstance(payload, dict):
        shape["top_level_keys"] = sorted(str(k) for k in payload.keys())[:80]
        for key in ("events", "data", "markets", "odds", "sports", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                shape[f"{key}_count"] = len(value)
            elif isinstance(value, dict):
                shape[f"{key}_keys"] = sorted(str(k) for k in value.keys())[:40]
    elif isinstance(payload, list):
        shape["length"] = len(payload)
        if payload and isinstance(payload[0], dict):
            shape["item_keys"] = sorted(str(k) for k in payload[0].keys())[:80]
    return shape


def _row_texts(page: Page) -> list[str]:
    selectors = [
        '[class*="OddsPreviewSection_oddsRow"]',
        '[class*="oddsRow"]',
        '[data-testid*="odds-row"]',
        '[data-testid*="event-row"]',
        'main tr',
    ]
    seen: set[str] = set()
    rows: list[str] = []
    for selector in selectors:
        loc = page.locator(selector)
        try:
            count = min(loc.count(), 250)
        except Exception:
            continue
        for idx in range(count):
            try:
                node = loc.nth(idx)
                if not node.is_visible():
                    continue
                text = " ".join(node.inner_text(timeout=2500).split())
            except Exception:
                continue
            if len(text) < 8 or text in seen:
                continue
            # Generic table rows are retained only when they look like odds rows.
            if selector == "main tr" and len(ODDS_RE.findall(text)) < 1:
                continue
            seen.add(text)
            rows.append(text[:2000])
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
            if loc.count() and loc.first.is_visible():
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
    auth_status = "NOT_REQUIRED"
    auth_blocker: str | None = None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context_kwargs: dict[str, Any] = {
            "viewport": {"width": 1600, "height": 1200},
            "locale": "en-US",
        }
        if storage_path:
            context_kwargs["storage_state"] = storage_path
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        page.set_default_timeout(TIMEOUT_MS)
        page.on("response", lambda response: (lambda shape: network_shapes.append(shape) if shape else None)(_response_shape(response)))

        try:
            page.goto(BOARD_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(1200)

            # If the page exposes only a Sign In CTA, try it only when credentials
            # or storage state are configured; otherwise public-board extraction is
            # still allowed.
            if not _has_odds_rows(page):
                sign_in = _first_visible(page, ['a:has-text("Sign In")', 'button:has-text("Sign In")', 'a:has-text("Log In")'])
                if sign_in is not None and (os.getenv("RUNDOWN_EMAIL") or storage_path):
                    sign_in.click()
                    page.wait_for_timeout(700)

            ok, blocker = _attempt_login(page)
            if not ok:
                auth_status = "BLOCKED"
                auth_blocker = blocker
            elif _needs_login(page):
                auth_status = "BLOCKED"
                auth_blocker = "RUNDOWN_AUTH_FAILED"
            elif storage_path or os.getenv("RUNDOWN_EMAIL"):
                auth_status = "AUTHENTICATED_OR_SESSION_REUSED"

            # Return to the board after successful login if auth flow redirected.
            if auth_blocker is None and "/odds" not in urlsplit(page.url).path:
                page.goto(BOARD_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                page.wait_for_timeout(1200)

            initial_rows = _row_texts(page)
            if initial_rows:
                captured_by_sport["INITIAL"] = initial_rows

            for sport in SPORT_LABELS:
                if _click_sport(page, sport):
                    clicked_sports.append(sport)
                    rows = _row_texts(page)
                    if rows:
                        captured_by_sport[sport] = rows

            # Persist storage state only to an explicitly supplied ephemeral path.
            state_out = os.getenv("RUNDOWN_STORAGE_STATE_OUT", "").strip()
            if state_out and auth_blocker is None:
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
    status = "DISCOVERY_COMPLETE" if unique_rows and auth_blocker is None else "DISCOVERY_BLOCKED"
    reason_code = auth_blocker or (None if unique_rows else "RUNDOWN_RENDERED_BOARD_EMPTY")
    result = {
        "schema": "wow.v17.rundown.rendered-board.capture.v1",
        "source": "THERUNDOWN_RENDERED_BOARD",
        "source_url": _safe_url(BOARD_URL),
        "status": status,
        "reason_code": reason_code,
        "auth_status": auth_status,
        "can_execute": CAN_EXECUTE,
        "prediction_authority": PREDICTION_AUTHORITY,
        "research_only": True,
        "research_ceiling": RESEARCH_CEILING,
        "sports_attempted": list(SPORT_LABELS),
        "sports_clicked": clicked_sports,
        "sports_with_rows": sorted(captured_by_sport),
        "row_count": len(unique_rows),
        "rows_by_sport": dict(captured_by_sport),
        "network_json_shapes": network_shapes[:300],
        "coverage": {
            "sports_attempted": len(SPORT_LABELS),
            "sports_clicked": len(clicked_sports),
            "sports_with_rows": len(captured_by_sport),
            "rows_captured": len(unique_rows),
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
        "sports_clicked": result["coverage"]["sports_clicked"],
        "sports_with_rows": result["coverage"]["sports_with_rows"],
        "rows_captured": result["coverage"]["rows_captured"],
        "network_json_responses": len(result["network_json_shapes"]),
        "can_execute": result["can_execute"],
    }, sort_keys=True))
    return 0 if result["status"] == "DISCOVERY_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
