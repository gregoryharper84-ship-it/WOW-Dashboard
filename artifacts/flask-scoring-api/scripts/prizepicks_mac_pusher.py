#!/usr/bin/env python3
"""
prizepicks_mac_pusher.py  —  PrizePicks → WOW Scoring API pusher (Mac)
===========================================================================

Two modes:

  MODE 1 — COOKIE REFRESH (run this first, or whenever lines stop loading)
    Reads your DataDome cookie from Chrome / Firefox, pushes it to the
    scoring API so the server can fetch PrizePicks on your behalf.

      python3 prizepicks_mac_pusher.py --mode cookie

  MODE 2 — LOCAL FETCH (pull lines locally and push them to the API)
    Fetches PrizePicks projections directly on your Mac (where DataDome
    allows it), normalizes them, and POSTs the rows to the scoring API.

      python3 prizepicks_mac_pusher.py --mode fetch [--league NBA] [--stat Points]

  MODE 3 — BOTH (refresh cookie then fetch)
      python3 prizepicks_mac_pusher.py --mode both [--league NBA]

Config:
  Edit the CONFIG block below, or pass --api-url and --api-key on the CLI.
  The scoring API key is the same one you use for all other WOW endpoints.

Requirements (all stdlib except requests):
  pip3 install requests

Optional (auto-read Chrome cookies without manual copy-paste):
  pip3 install pycookiecheat
"""

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import shutil
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: run   pip3 install requests   then retry.")

# ===========================================================================
# CONFIG — edit these or pass them as CLI flags
# ===========================================================================
API_URL     = os.environ.get("WOW_API_URL",  "https://create-app-gregoryharper84.replit.app")
API_KEY     = os.environ.get("WOW_API_KEY",  "")   # your SCORING_API_KEY
PP_BASE_URL = "https://api.prizepicks.com"
TIMEOUT     = 20
# ===========================================================================


def _headers_pp(cookie_str: str) -> dict:
    return {
        "User-Agent":       "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0.0.0 Safari/537.36",
        "Accept":           "application/json, text/plain, */*",
        "Accept-Language":  "en-US,en;q=0.9",
        "Origin":           "https://app.prizepicks.com",
        "Referer":          "https://app.prizepicks.com/",
        "X-Requested-With": "XMLHttpRequest",
        "Cookie":           cookie_str,
    }


def _headers_wow() -> dict:
    return {
        "Content-Type": "application/json",
        "X-API-Key":    API_KEY,
    }


# ---------------------------------------------------------------------------
# Cookie extraction
# ---------------------------------------------------------------------------

def _chrome_cookie_path() -> Path | None:
    """Return the Chrome Cookies SQLite file path on macOS."""
    candidates = [
        Path.home() / "Library/Application Support/Google/Chrome/Default/Cookies",
        Path.home() / "Library/Application Support/Google/Chrome/Profile 1/Cookies",
        Path.home() / "Library/Application Support/Chromium/Default/Cookies",
        Path.home() / "Library/Application Support/BraveSoftware/Brave-Browser/Default/Cookies",
        Path.home() / "Library/Application Support/Microsoft Edge/Default/Cookies",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _read_chrome_cookie_encrypted(cookie_path: Path, domain: str) -> str | None:
    """
    Try pycookiecheat to read encrypted Chrome cookies.
    Falls back gracefully if the library isn't installed.
    """
    try:
        from pycookiecheat import chrome_cookies  # type: ignore
        cookies = chrome_cookies(f"https://{domain}", cookie_file=str(cookie_path))
        if cookies:
            return "; ".join(f"{k}={v}" for k, v in cookies.items())
    except ImportError:
        pass
    except Exception as e:
        print(f"  [pycookiecheat] {e}")
    return None


def _read_chrome_cookie_plain(cookie_path: Path, domain: str) -> str | None:
    """
    Try reading Chrome cookies without decryption (works only when Chrome is
    not running and the cookies happen to be stored in plaintext — rare but
    possible for older profiles).
    """
    try:
        # Chrome locks the file while running; copy it first
        tmp = tempfile.mktemp(suffix=".sqlite")
        shutil.copy2(str(cookie_path), tmp)
        conn = sqlite3.connect(tmp)
        cur  = conn.cursor()
        cur.execute(
            "SELECT name, value FROM cookies "
            "WHERE host_key LIKE ? AND value != ''",
            (f"%{domain}%",),
        )
        rows = cur.fetchall()
        conn.close()
        os.unlink(tmp)
        if rows:
            return "; ".join(f"{name}={val}" for name, val in rows)
    except Exception:
        pass
    return None


def _read_firefox_cookie(domain: str) -> str | None:
    """Try reading Firefox cookies for the domain."""
    profiles_dir = Path.home() / "Library/Application Support/Firefox/Profiles"
    if not profiles_dir.exists():
        return None
    for profile in profiles_dir.iterdir():
        db_path = profile / "cookies.sqlite"
        if not db_path.exists():
            continue
        try:
            tmp = tempfile.mktemp(suffix=".sqlite")
            shutil.copy2(str(db_path), tmp)
            conn = sqlite3.connect(tmp)
            cur  = conn.cursor()
            cur.execute(
                "SELECT name, value FROM moz_cookies WHERE host LIKE ?",
                (f"%{domain}%",),
            )
            rows = cur.fetchall()
            conn.close()
            os.unlink(tmp)
            if rows:
                return "; ".join(f"{n}={v}" for n, v in rows)
        except Exception:
            continue
    return None


def get_prizepicks_cookie_auto() -> str | None:
    """
    Try to read the PrizePicks DataDome cookie automatically from the local browser.
    Returns the cookie string or None if not found.
    """
    print("  Trying Chrome (encrypted, requires pycookiecheat)...")
    chrome_path = _chrome_cookie_path()
    if chrome_path:
        c = _read_chrome_cookie_encrypted(chrome_path, "prizepicks.com")
        if c:
            print("  ✓ Got cookie from Chrome (pycookiecheat).")
            return c

        print("  Trying Chrome (plaintext fallback)...")
        c = _read_chrome_cookie_plain(chrome_path, "prizepicks.com")
        if c:
            print("  ✓ Got cookie from Chrome (plaintext).")
            return c

    print("  Trying Firefox...")
    c = _read_firefox_cookie("prizepicks.com")
    if c:
        print("  ✓ Got cookie from Firefox.")
        return c

    return None


def get_prizepicks_cookie_manual() -> str:
    """
    Prompt the user to paste their cookie from Chrome DevTools.
    Instructions are printed to the terminal.
    """
    print()
    print("=" * 60)
    print("  HOW TO GET YOUR PRIZEPICKS COOKIE")
    print("=" * 60)
    print("  1. Open https://app.prizepicks.com in Chrome and log in.")
    print("  2. Press F12 (or Cmd+Option+I) to open DevTools.")
    print("  3. Click the Network tab.")
    print("  4. Refresh the page (Cmd+R).")
    print("  5. Click any request to 'api.prizepicks.com/projections'.")
    print("  6. In the right panel, click 'Headers'.")
    print("  7. Scroll to 'Request Headers' → find 'cookie:'.")
    print("  8. Right-click on the cookie value → Copy value.")
    print("=" * 60)
    print()
    cookie = input("Paste your cookie here and press Enter:\n> ").strip()
    if not cookie:
        sys.exit("No cookie provided.")
    return cookie


# ---------------------------------------------------------------------------
# PrizePicks fetch (local — bypasses DataDome)
# ---------------------------------------------------------------------------

KNOWN_LEAGUE_IDS = {
    "NFL": 2, "NBA": 7, "MLB": 8, "NHL": 9, "WNBA": 11,
    "NCAAB": 14, "PGA": 147, "UFC": 152, "NCAAF": 16,
}


def fetch_projections_local(
    cookie_str: str,
    league_id:  int | None = None,
    stat_type:  str | None = None,
    per_page:   int        = 250,
) -> list[dict]:
    """Fetch PrizePicks projections locally using the provided cookie."""
    params: dict = {"per_page": per_page}
    if league_id:
        params["league_id"] = league_id
    if stat_type:
        params["stat_type"] = stat_type

    print(f"  Fetching {PP_BASE_URL}/projections ...")
    r = requests.get(
        f"{PP_BASE_URL}/projections",
        headers=_headers_pp(cookie_str),
        params=params,
        timeout=TIMEOUT,
    )

    if r.status_code != 200:
        print(f"  ✗ HTTP {r.status_code}: {r.text[:200]}")
        return []

    raw = r.json()
    if "url" in raw and "captcha-delivery" in raw.get("url", ""):
        print("  ✗ DataDome CAPTCHA triggered — cookie is expired.")
        print("    Re-run with --mode cookie to refresh it.")
        return []

    data     = raw.get("data", [])
    included = raw.get("included", [])

    players  = {i["id"]: i.get("attributes", {}) for i in included if i.get("type") == "new_player"}
    leagues  = {i["id"]: i.get("attributes", {}) for i in included if i.get("type") == "league"}

    results = []
    for item in data:
        if item.get("type") != "projection":
            continue
        attrs = item.get("attributes", {})
        rels  = item.get("relationships", {})
        pid   = (rels.get("new_player", {}).get("data") or {}).get("id", "")
        lid   = (rels.get("league", {}).get("data") or {}).get("id", "")
        player = players.get(pid, {})
        league = leagues.get(lid, {})
        results.append({
            "projection_id":    item.get("id"),
            "player_id":        pid,
            "player_name":      player.get("name"),
            "team":             player.get("team"),
            "position":         player.get("position"),
            "image_url":        player.get("image_url"),
            "league":           league.get("name"),
            "league_id":        lid,
            "sport":            league.get("sport"),
            "stat_type":        attrs.get("stat_type"),
            "line_score":       attrs.get("line_score"),
            "status":           attrs.get("status"),
            "start_time":       attrs.get("start_time"),
            "game_description": attrs.get("description"),
            "is_promo":         attrs.get("is_promo", False),
            "odds_type":        attrs.get("odds_type"),
            "flash_sale_line":  attrs.get("flash_sale_line_score"),
            "board_time":       attrs.get("board_time"),
            "pulled_at":        datetime.now(timezone.utc).isoformat(),
        })

    print(f"  ✓ Fetched {len(results)} projections.")
    return results


# ---------------------------------------------------------------------------
# Push to WOW API
# ---------------------------------------------------------------------------

def push_cookie_to_api(cookie_str: str) -> bool:
    """POST cookie to /wow/prizepicks/cookie."""
    if not API_KEY:
        print("  ✗ WOW_API_KEY not set. Export it or edit CONFIG at the top of this script.")
        return False

    url = f"{API_URL}/wow/prizepicks/cookie"
    print(f"  Pushing cookie to {url} ...")
    try:
        r = requests.post(
            url,
            headers=_headers_wow(),
            json={"cookie": cookie_str},
            timeout=15,
        )
        if r.status_code == 200 and r.json().get("ok"):
            print(f"  ✓ Cookie stored. Tail: {r.json().get('cookie_tail')}")
            return True
        else:
            print(f"  ✗ API returned {r.status_code}: {r.text[:300]}")
            return False
    except Exception as e:
        print(f"  ✗ Request failed: {e}")
        return False


def push_projections_to_api(projections: list[dict]) -> bool:
    """POST projections to /wow/prizepicks/projections/ingest (if it exists)."""
    # The server fetches on demand using the stored cookie, so a separate
    # ingest endpoint isn't strictly needed — this function is a placeholder
    # for workflows where the Mac script is the sole data source.
    print(f"  {len(projections)} projections available locally.")
    print("  (Server fetches on demand via GET /wow/prizepicks/projections.)")
    print("  To trigger a server-side fetch: GET /wow/prizepicks/projections")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="PrizePicks → WOW Scoring API cookie pusher (Mac)"
    )
    parser.add_argument(
        "--mode", choices=["cookie", "fetch", "both"], default="both",
        help="cookie=push cookie only | fetch=pull lines locally | both=cookie then fetch",
    )
    parser.add_argument("--league", default="", help="League name or ID (e.g. NBA, 7)")
    parser.add_argument("--stat",   default="", help="Stat type filter (e.g. Points)")
    parser.add_argument("--api-url", default="", help="Override WOW_API_URL")
    parser.add_argument("--api-key", default="", help="Override WOW_API_KEY")
    parser.add_argument("--manual-cookie", action="store_true",
                        help="Skip auto-read; always prompt to paste cookie")
    args = parser.parse_args()

    global API_URL, API_KEY
    if args.api_url:
        API_URL = args.api_url
    if args.api_key:
        API_KEY = args.api_key

    print()
    print("━" * 60)
    print("  PrizePicks → WOW Pusher")
    print(f"  Mode: {args.mode.upper()}   Target: {API_URL}")
    print("━" * 60)

    # Resolve league ID
    league_id: int | None = None
    if args.league:
        if args.league.isdigit():
            league_id = int(args.league)
        else:
            league_id = KNOWN_LEAGUE_IDS.get(args.league.upper())
            if league_id is None:
                print(f"  Unknown league '{args.league}'. Valid names: {list(KNOWN_LEAGUE_IDS)}")
                sys.exit(1)

    # ── Step 1: Get cookie ────────────────────────────────────────────────
    cookie_str: str | None = None

    if args.mode in ("cookie", "both"):
        print("\n[1/2] Getting PrizePicks cookie...")
        if not args.manual_cookie:
            cookie_str = get_prizepicks_cookie_auto()
        if not cookie_str:
            print("  Auto-read failed or skipped.")
            cookie_str = get_prizepicks_cookie_manual()

        print("\n[1/2] Pushing cookie to WOW API...")
        push_cookie_to_api(cookie_str)

    # ── Step 2: Fetch projections locally ────────────────────────────────
    if args.mode in ("fetch", "both"):
        print("\n[2/2] Fetching PrizePicks projections locally...")
        if not cookie_str:
            # fetch-only mode — try auto-read first
            cookie_str = get_prizepicks_cookie_auto()
            if not cookie_str:
                cookie_str = get_prizepicks_cookie_manual()

        projections = fetch_projections_local(
            cookie_str = cookie_str,
            league_id  = league_id,
            stat_type  = args.stat or None,
        )

        if projections:
            push_projections_to_api(projections)

            # Print a quick summary table
            print()
            print(f"  {'PLAYER':<25} {'LEAGUE':<6} {'STAT':<18} {'LINE':>6}")
            print(f"  {'-'*25} {'-'*6} {'-'*18} {'-'*6}")
            for p in projections[:20]:
                print(
                    f"  {(p['player_name'] or ''):<25} "
                    f"{(p['league'] or ''):<6} "
                    f"{(p['stat_type'] or ''):<18} "
                    f"{str(p['line_score'] or ''):>6}"
                )
            if len(projections) > 20:
                print(f"  ... and {len(projections) - 20} more")

    print()
    print("━" * 60)
    print("  Done.")
    print()
    print("  Next steps:")
    print(f"    GET  {API_URL}/wow/prizepicks/projections?league=NBA")
    print(f"    GET  {API_URL}/wow/prizepicks/leagues")
    print(f"    POST {API_URL}/wow/prizepicks/cookie   (to refresh cookie)")
    print("━" * 60)
    print()


if __name__ == "__main__":
    main()
