"""Single public browser-rendered PropsMadness page, for manual research only.

No vendor API calls, paid account, stealth, captcha solving, data upload, retries,
cron or production scoring. The browser can render a public page's ordinary
JavaScript, but unknown/disallowed robots and 403/429 always stop the probe.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from urllib.robotparser import RobotFileParser
import re
from typing import Any

from v17.propsmadness_multisport_public_explore import (
    ROOT, USER_AGENT, MAX_HTML, _config, _RejectRedirects,
    PublicPageHold, parse_multisport_public_html,
)

CAN_EXECUTE = False
MAX_ROBOTS = 50_000


def _robots_permits(raw: bytes, url: str) -> None:
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_ROBOTS:
        raise PublicPageHold("PM_PUBLIC_ROBOTS_UNVERIFIED")
    try:
        txt = raw.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise PublicPageHold("PM_PUBLIC_ROBOTS_UNVERIFIED") from exc
    if not re.search(r"(?im)^\s*User-agent\s*:", txt):
        raise PublicPageHold("PM_PUBLIC_ROBOTS_UNVERIFIED")
    robots = RobotFileParser()
    robots.parse(txt.splitlines())
    if not robots.can_fetch(USER_AGENT, url):
        raise PublicPageHold("PM_PUBLIC_ROBOTS_DISALLOW")


def _check_robots_live(url: str) -> None:
    robots_url = ROOT + "/robots.txt"
    try:
        with build_opener(_RejectRedirects()).open(
            Request(robots_url, headers={"User-Agent": USER_AGENT}),
            timeout=12,
        ) as resp:
            if resp.geturl() != robots_url:
                raise PublicPageHold("PM_PUBLIC_REDIRECT_NOT_ALLOWED")
            if resp.status in (403, 429):
                raise PublicPageHold("PM_PUBLIC_SOURCE_ACCESS_DENIED")
            if resp.status != 200:
                raise PublicPageHold("PM_PUBLIC_ROBOTS_UNVERIFIED")
            raw = resp.read(MAX_ROBOTS + 1)
    except PublicPageHold:
        raise
    except HTTPError as exc:
        if exc.code in (403, 429):
            raise PublicPageHold("PM_PUBLIC_SOURCE_ACCESS_DENIED") from exc
        raise PublicPageHold("PM_PUBLIC_ROBOTS_UNVERIFIED") from exc
    except URLError as exc:
        raise PublicPageHold("PM_PUBLIC_ROBOTS_UNVERIFIED") from exc
    _robots_permits(raw, url)


def _public_browser_asset_allowed(url: str, resource_type: str) -> bool:
    """Public page/assets only; never follow undocumented data API traffic."""
    p = urlparse(url)
    if p.scheme != "https" or p.hostname != "propsmadness.com":
        return False
    if resource_type not in ("document", "script", "stylesheet", "font", "image"):
        return False
    if any(part in ("api", "graphql", "rpc", "admin", "private")
           for part in p.path.lower().split("/") if part):
        return False
    return True


def _render_public_page(url: str) -> bytes:
    """One headless browser navigation to a public allowlisted Explore page."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PublicPageHold("PM_PUBLIC_BROWSER_DEPENDENCY_MISSING") from exc

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=USER_AGENT,
                    accept_downloads=False,
                    service_workers="block",
                )
                page = context.new_page()
                # JS can otherwise trigger undocumented private XHR/fetch APIs
                # implicitly. Deny those requests: if data relies on them,
                # report no verified public rows rather than work around them.
                def restrict(route: Any) -> None:
                    if _public_browser_asset_allowed(route.request.url, route.request.resource_type):
                        route.continue_()
                    else:
                        route.abort()
                page.route("**/*", restrict)
                response = page.goto(url, wait_until="domcontentloaded", timeout=15000)
                if page.url != url:
                    raise PublicPageHold("PM_PUBLIC_REDIRECT_NOT_ALLOWED")
                status = response.status if response is not None else 0
                if status in (401, 403, 429):
                    raise PublicPageHold("PM_PUBLIC_SOURCE_ACCESS_DENIED")
                if status != 200:
                    raise PublicPageHold("PM_PUBLIC_SOURCE_HTTP_FAILURE")
                # UI only: wait briefly for publicly visible links; no hidden API access.
                try:
                    page.locator('a[href*="player="]').first.wait_for(
                        state="attached", timeout=5000
                    )
                except Exception:
                    pass
                html = page.content().encode("utf-8")
                if not html or len(html) > MAX_HTML:
                    raise PublicPageHold("PM_PUBLIC_HTML_SIZE_INVALID")
                return html
            finally:
                browser.close()
    except PublicPageHold:
        raise
    except Exception as exc:
        raise PublicPageHold("PM_PUBLIC_BROWSER_RENDER_UNAVAILABLE") from exc


def probe_public_rendered_once(
    sport: str, market: str, *, personal_noncommercial_use_confirmed: bool
) -> dict:
    """No schedule or persistence; invocation cannot imply reuse/training rights."""
    _, _, url = _config(sport, market)
    if personal_noncommercial_use_confirmed is not True:
        raise PublicPageHold("PM_PUBLIC_USE_NOT_CONFIRMED")
    _check_robots_live(url)
    return parse_multisport_public_html(
        _render_public_page(url),
        sport=sport, market=market,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )
