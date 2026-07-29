#!/usr/bin/env python3
"""
prizepicks_mac_pusher.py  —  PrizePicks → WOW Scoring API pusher (Mac)
===========================================================================

DataDome (PrizePicks' bot protection) ties cookies to the originating IP.
Requests from Replit's server are blocked even with a valid cookie.
This script runs on YOUR Mac (same IP that issued the cookie), fetches
projections locally, then POSTs them to the WOW scoring API for storage.

Usage:
  # Fetch all current lines and push to the API
  python3 prizepicks_mac_pusher.py

  # Filter by league
  python3 prizepicks_mac_pusher.py --league NBA
  python3 prizepicks_mac_pusher.py --league MLB --stat "Pitcher Strikeouts"

  # Set your API URL and key via env vars (or edit CONFIG below)
  WOW_API_URL=https://your-app.replit.app WOW_API_KEY=your_key python3 prizepicks_mac_pusher.py

  # Dry run — fetch locally but don't push to API
  python3 prizepicks_mac_pusher.py --dry-run

Requirements (all stdlib except requests):
  pip3 install requests

Optional (auto-read Chrome cookie without manual copy-paste):
  pip3 install pycookiecheat
===========================================================================
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    sys.exit("Missing: run   pip3 install requests   then retry.")

# ===========================================================================
# CONFIG — edit these or set as environment variables
# ===========================================================================
API_URL     = os.environ.get("WOW_API_URL", "https://create-app-gregoryharper84.replit.app")
API_KEY     = os.environ.get("WOW_API_KEY", "")
PP_BASE_URL = "https://api.prizepicks.com"
TIMEOUT     = 25
# ===========================================================================

KNOWN_LEAGUE_IDS: dict[str, int] = {
    "NFL": 2, "NBA": 7, "MLB": 8, "NHL": 9, "WNBA": 11,
    "NCAAB": 14, "PGA": 147, "UFC": 152, "NCAAF": 16,
}


# ---------------------------------------------------------------------------
# Browser cookie extraction
# ---------------------------------------------------------------------------

def _chrome_cookie_paths() -> list[Path]:
    candidates = [
        Path.home() / "Library/Application Support/Google/Chrome/Default/Cookies",
        Path.home() / "Library/Application Support/Google/Chrome/Profile 1/Cookies",
        Path.home() / "Library/Application Support/Chromium/Default/Cookies",
        Path.home() / "Library/Application Support/BraveSoftware/Brave-Browser/Default/Cookies",
        Path.home() / "Library/Application Support/Microsoft Edge/Default/Cookies",
    ]
    return [p for p in candidates if p.exists()]


def _try_pycookiecheat(cookie_path: Path, domain: str) -> str | None:
    try:
        from pycookiecheat import chrome_cookies  # type: ignore
        cookies = chrome_cookies(f"https://{domain}", cookie_file=str(cookie_path))
        if cookies:
            return "; ".join(f"{k}={v}" for k, v in cookies.items())
    except ImportError:
        pass
    except Exception as e:
        print(f"    pycookiecheat: {e}")
    return None


def _try_sqlite_plain(cookie_path: Path, domain: str) -> str | None:
    """Read Chrome cookies without decryption — only works for non-encrypted values."""
    try:
        tmp = tempfile.mktemp(suffix=".sqlite")
        shutil.copy2(str(cookie_path), tmp)
        conn = sqlite3.connect(tmp)
        cur  = conn.cursor()
        cur.execute(
            "SELECT name, value FROM cookies WHERE host_key LIKE ? AND value != ''",
            (f"%{domain}%",),
        )
        rows = cur.fetchall()
        conn.close()
        os.unlink(tmp)
        if rows:
            return "; ".join(f"{n}={v}" for n, v in rows)
    except Exception:
        pass
    return None


def _try_firefox(domain: str) -> str | None:
    profiles = Path.home() / "Library/Application Support/Firefox/Profiles"
    if not profiles.exists():
        return None
    for profile in profiles.iterdir():
        db = profile / "cookies.sqlite"
        if not db.exists():
            continue
        try:
            tmp = tempfile.mktemp(suffix=".sqlite")
            shutil.copy2(str(db), tmp)
            conn = sqlite3.connect(tmp)
            cur  = conn.cursor()
            cur.execute("SELECT name, value FROM moz_cookies WHERE host LIKE ?", (f"%{domain}%",))
            rows = cur.fetchall()
            conn.close()
            os.unlink(tmp)
            if rows:
                return "; ".join(f"{n}={v}" for n, v in rows)
        except Exception:
            continue
    return None


def get_cookie_auto() -> str | None:
    print("  Trying Chrome (pycookiecheat — decrypts Keychain-protected cookies)...")
    for path in _chrome_cookie_paths():
        c = _try_pycookiecheat(path, "prizepicks.com")
        if c:
            print("  ✓ Chrome cookie obtained.")
            return c

    print("  Trying Chrome (plaintext fallback)...")
    for path in _chrome_cookie_paths():
        c = _try_sqlite_plain(path, "prizepicks.com")
        if c:
            print("  ✓ Chrome cookie obtained (plaintext).")
            return c

    print("  Trying Firefox...")
    c = _try_firefox("prizepicks.com")
    if c:
        print("  ✓ Firefox cookie obtained.")
        return c

    return None


def get_cookie_manual() -> str:
    print()
    print("=" * 62)
    print("  HOW TO GET YOUR PRIZEPICKS COOKIE (30 seconds)")
    print("=" * 62)
    print("  1. Open https://app.prizepicks.com in Chrome and log in.")
    print("  2. Press Cmd+Option+I  to open DevTools.")
    print("  3. Click the Network tab, then refresh the page (Cmd+R).")
    print("  4. Click any request to 'api.prizepicks.com/projections'.")
    print("  5. In the right panel → Headers → Request Headers.")
    print("  6. Find the 'cookie' row — right-click → Copy value.")
    print("  7. Paste it here and press Enter.")
    print("=" * 62)
    print()
    cookie = input("> ").strip()
    if not cookie:
        sys.exit("No cookie provided.")
    return cookie


# ---------------------------------------------------------------------------
# PrizePicks local fetch
# ---------------------------------------------------------------------------

def _pp_headers(cookie: str) -> dict:
    return {
        "User-Agent":       "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0.0.0 Safari/537.36",
        "Accept":           "application/json, text/plain, */*",
        "Accept-Language":  "en-US,en;q=0.9",
        "Origin":           "https://app.prizepicks.com",
        "Referer":          "https://app.prizepicks.com/",
        "X-Requested-With": "XMLHttpRequest",
        "Cookie":           cookie,
    }


def fetch_locally(
    cookie:    str,
    league_id: int | None = None,
    stat_type: str | None = None,
    per_page:  int        = 500,
) -> list[dict[str, Any]]:
    """Fetch PrizePicks projections directly on this Mac."""
    params: dict = {"per_page": per_page}
    if league_id:
        params["league_id"] = league_id
    if stat_type:
        params["stat_type"] = stat_type

    print(f"  Fetching {PP_BASE_URL}/projections ...")
    try:
        r = requests.get(
            f"{PP_BASE_URL}/projections",
            headers=_pp_headers(cookie),
            params=params,
            timeout=TIMEOUT,
        )
    except requests.exceptions.ConnectionError as e:
        print(f"  ✗ Connection error: {e}")
        return []

    if r.status_code != 200:
        print(f"  ✗ HTTP {r.status_code}")
        if r.status_code == 403:
            print("    Cookie may be expired — re-run without --skip-cookie-refresh")
        print(f"    Body: {r.text[:200]}")
        return []

    raw = r.json()
    # DataDome captcha redirect comes back as JSON with a "url" key even on 200
    if "url" in raw and "captcha-delivery" in raw.get("url", ""):
        print("  ✗ DataDome CAPTCHA — cookie is expired or invalid.")
        print("    Re-run without --skip-cookie-refresh to get a fresh cookie.")
        return []

    data     = raw.get("data", [])
    included = raw.get("included", [])

    players = {i["id"]: i.get("attributes", {}) for i in included if i.get("type") == "new_player"}
    leagues = {i["id"]: i.get("attributes", {}) for i in included if i.get("type") == "league"}

    pulled = datetime.now(timezone.utc).isoformat()
    rows   = []
    for item in data:
        if item.get("type") != "projection":
            continue
        attrs  = item.get("attributes", {})
        rels   = item.get("relationships", {})
        pid    = (rels.get("new_player", {}).get("data") or {}).get("id", "")
        lid    = (rels.get("league", {}).get("data") or {}).get("id", "")
        player = players.get(pid, {})
        league = leagues.get(lid, {})
        rows.append({
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
            "pulled_at":        pulled,
        })

    print(f"  ✓ Fetched {len(rows)} projections.")
    return rows


# ---------------------------------------------------------------------------
# Push to WOW API
# ---------------------------------------------------------------------------

def _wow_headers() -> dict:
    return {"Content-Type": "application/json", "X-API-Key": API_KEY}


def push_cookie(cookie: str) -> bool:
    if not API_KEY:
        print("  ✗ WOW_API_KEY not set.")
        return False
    url = f"{API_URL}/wow/prizepicks/cookie"
    try:
        r = requests.post(url, headers=_wow_headers(), json={"cookie": cookie}, timeout=15)
        data = r.json()
        if r.status_code == 200 and data.get("ok"):
            print(f"  ✓ Cookie stored on server. (tail: {data.get('cookie_tail', '...')})")
            return True
        print(f"  ✗ {r.status_code}: {data.get('detail', r.text[:200])}")
        return False
    except Exception as e:
        print(f"  ✗ {e}")
        return False


def push_projections(rows: list[dict]) -> bool:
    if not API_KEY:
        print("  ✗ WOW_API_KEY not set. Export WOW_API_KEY=<your key>")
        return False
    if not rows:
        print("  No rows to push.")
        return False
    url = f"{API_URL}/wow/prizepicks/projections/ingest"
    print(f"  Pushing {len(rows)} projections to {url} ...")
    try:
        r = requests.post(
            url,
            headers=_wow_headers(),
            json={"projections": rows},
            timeout=30,
        )
        data = r.json()
        if r.status_code == 200 and data.get("ok"):
            print(f"  ✓ {data.get('detail', 'Ingested.')}")
            return True
        print(f"  ✗ {r.status_code}: {data.get('detail', r.text[:200])}")
        return False
    except Exception as e:
        print(f"  ✗ {e}")
        return False


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def print_table(rows: list[dict], max_rows: int = 25) -> None:
    if not rows:
        return
    print()
    print(f"  {'PLAYER':<26} {'TEAM':<6} {'LEAGUE':<6} {'STAT':<22} {'LINE':>6}")
    print(f"  {'-'*26} {'-'*6} {'-'*6} {'-'*22} {'-'*6}")
    for p in rows[:max_rows]:
        print(
            f"  {str(p.get('player_name') or ''):<26} "
            f"{str(p.get('team') or ''):<6} "
            f"{str(p.get('league') or ''):<6} "
            f"{str(p.get('stat_type') or ''):<22} "
            f"{str(p.get('line_score') or ''):>6}"
        )
    if len(rows) > max_rows:
        print(f"  ... and {len(rows) - max_rows} more rows")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="PrizePicks → WOW Scoring API (Mac)")
    parser.add_argument("--league", default="", help="League filter: NBA, MLB, NFL, WNBA, NHL, PGA, UFC")
    parser.add_argument("--stat",   default="", help="Stat type filter, e.g. 'Points'")
    parser.add_argument("--api-url", default="", help="Override WOW_API_URL")
    parser.add_argument("--api-key", default="", help="Override WOW_API_KEY")
    parser.add_argument("--dry-run", action="store_true", help="Fetch locally but don't push to API")
    parser.add_argument("--skip-cookie-refresh", action="store_true",
                        help="Skip pushing the cookie to the server (just ingest projections)")
    parser.add_argument("--manual-cookie", action="store_true",
                        help="Always prompt for cookie instead of auto-reading from browser")
    args = parser.parse_args()

    global API_URL, API_KEY
    if args.api_url:
        API_URL = args.api_url
    if args.api_key:
        API_KEY = args.api_key

    print()
    print("━" * 62)
    print("  PrizePicks → WOW Scoring API Pusher")
    print(f"  Target: {API_URL}")
    if args.dry_run:
        print("  Mode: DRY RUN (will not push to API)")
    print("━" * 62)

    # ── Resolve league ────────────────────────────────────────────────────
    league_id: int | None = None
    if args.league:
        if args.league.isdigit():
            league_id = int(args.league)
        else:
            league_id = KNOWN_LEAGUE_IDS.get(args.league.upper())
            if league_id is None:
                print(f"\n  Unknown league '{args.league}'.")
                print(f"  Valid names: {list(KNOWN_LEAGUE_IDS)}")
                sys.exit(1)

    # ── Step 1: Get cookie ────────────────────────────────────────────────
    print("\n[1/3] Getting PrizePicks cookie...")
    cookie: str | None = None
    if not args.manual_cookie:
        cookie = get_cookie_auto()
    if not cookie:
        print("  Auto-read failed or skipped.")
        cookie = get_cookie_manual()

    # ── Step 2: Push cookie to server (for reference) ─────────────────────
    if not args.dry_run and not args.skip_cookie_refresh:
        print("\n[2/3] Storing cookie on WOW server...")
        push_cookie(cookie)
    else:
        print("\n[2/3] Skipping cookie push (dry-run or --skip-cookie-refresh).")

    # ── Step 3: Fetch projections and push to ingest endpoint ─────────────
    print("\n[3/3] Fetching PrizePicks projections locally...")
    rows = fetch_locally(
        cookie    = cookie,
        league_id = league_id,
        stat_type = args.stat or None,
    )

    if rows:
        print_table(rows)

        if args.dry_run:
            print("\n  Dry run — not pushing to API.")
            # Save locally for inspection
            out = f"/tmp/prizepicks_{datetime.now().strftime('%H%M%S')}.json"
            with open(out, "w") as f:
                json.dump(rows, f, indent=2)
            print(f"  Saved to {out}")
        else:
            print(f"\n  Pushing {len(rows)} rows to WOW API...")
            push_projections(rows)

    print()
    print("━" * 62)
    print("  Done.")
    print()
    print("  Verify on the server:")
    print(f"    GET {API_URL}/wow/prizepicks/board")
    print(f"    GET {API_URL}/wow/prizepicks/projections?league=NBA")
    print()
    print("  Re-run any time to refresh lines:")
    print(f"    python3 prizepicks_mac_pusher.py --league NBA")
    print("━" * 62)
    print()


if __name__ == "__main__":
    main()
