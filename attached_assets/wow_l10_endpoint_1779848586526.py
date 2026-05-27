# ═══════════════════════════════════════════════════════════════
# WOW /wow/l10 ENDPOINT
# Paste this entire block into app.py before
# the  if __name__ == "__main__":  line
#
# ADD THESE TO YOUR EXISTING IMPORT BLOCK AT THE TOP:
#   import re, time, statistics
#   from datetime import datetime
#   from bs4 import BeautifulSoup, Comment   ← new dep
#   import pandas as pd                      ← new dep if absent
#
# ADD TO requirements.txt:
#   beautifulsoup4>=4.12.0
#   lxml>=4.9.0
#   pandas>=2.0.0
#   (requests is already present via Flask)
# ═══════════════════════════════════════════════════════════════

import re
import time
import statistics
from datetime import datetime

try:
    from bs4 import BeautifulSoup, Comment
    import pandas as pd
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

try:
    from nba_api.stats.static import players as _nba_players_static
    from nba_api.stats.endpoints import playergamelog as _nba_gamelog_ep
    _NBA_API_AVAILABLE = True
except ImportError:
    _NBA_API_AVAILABLE = False


# ── TTL cache (1 hour) ────────────────────────────────────────
_L10_CACHE: dict = {}
_L10_CACHE_TTL = 3600

def _l10_cache_get(key: str):
    entry = _L10_CACHE.get(key)
    if entry and (time.time() - entry[0]) < _L10_CACHE_TTL:
        return entry[1]
    return None

def _l10_cache_set(key: str, data: dict):
    _L10_CACHE[key] = (time.time(), data)


# ── nba_api prop column map ───────────────────────────────────
_NBA_COL_MAP = {
    "Points":               "PTS",
    "Rebounds":             "REB",
    "Assists":              "AST",
    "3-PT Made":            "FG3M",
    "Steals":               "STL",
    "Blocks":               "BLK",
    "Free Throws Made":     "FTM",
    "Free Throws Attempted":"FTA",
    "Pts+Rebs+Asts":        ["PTS", "REB", "AST"],
    "Pts+Rebs":             ["PTS", "REB"],
    "Pts+Asts":             ["PTS", "AST"],
    "Rebs+Asts":            ["REB", "AST"],
}

# ── BBRef prop column map (MLB) ───────────────────────────────
_MLB_COL_MAP = {
    "Pitcher Strikeouts":   "SO",
    "Hits Allowed":         "H",
    "Earned Runs":          "ER",
    "Walks Allowed":        "BB",
    "Hitter Hits":          "H",
    "Total Bases":          "TB",
    "Runs Scored":          "R",
    "RBIs":                 "RBI",
    "Hitter Strikeouts":    "SO",
    "H+R+RBI":              ["H", "R", "RBI"],
}

_NICHE_PROPS = {
    "1st Inn. Pitches Thrown",
    "Pitcher Fantasy Score",
    "Goalie Saves",
}


# ── Helpers ───────────────────────────────────────────────────

def _safe_float(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None

def _hit_label(value: float, line: float, direction: str) -> str:
    if value == line:
        return "PUSH"
    if direction == "MORE":
        return "HIT" if value > line else "MISS"
    return "HIT" if value < line else "MISS"

def _calc_stats(games: list, line: float, direction: str) -> dict:
    if not games:
        return {}
    vals   = [g["value"] for g in games]
    l5v    = vals[:5]
    l10v   = vals[:10]
    l5h    = sum(1 for g in games[:5]  if g["hit"] == "HIT")
    l10h   = sum(1 for g in games[:10] if g["hit"] == "HIT")
    avg10  = sum(l10v) / len(l10v)
    edge   = round((avg10 - line) if direction == "MORE" else (line - avg10), 2)
    return {
        "l5_avg":      round(sum(l5v)  / len(l5v),  2),
        "l10_avg":     round(avg10, 2),
        "l10_median":  round(statistics.median(l10v), 2),
        "l5_hit_rate": f"{l5h}/5 ({round(l5h/5*100)}%)",
        "l10_hit_rate":f"{l10h}/{len(l10v)} ({round(l10h/len(l10v)*100)}%)",
        "edge":        edge,
    }

def _confidence_tier(rows: int, complete: bool) -> str:
    if complete and rows >= 10: return "FINAL LOCK ELIGIBLE"
    if rows >= 5:               return "CONDITIONAL — L5 ONLY"
    if rows >= 3:               return "WATCH / RESEARCH ONLY"
    return "REJECT — INSUFFICIENT DATA"


# ── NBA via nba_api ───────────────────────────────────────────

def _l10_nba(first: str, last: str, prop: str,
              direction: str, line: float, season: str) -> dict:
    """Pull NBA L10 via nba_api (no scraping, free)."""
    result = {"source": "stats.nba.com (nba_api)", "games": [],
              "complete": False, "rows": 0, "gap": ""}

    if not _NBA_API_AVAILABLE:
        result["gap"] = "nba_api not installed"
        return result

    col_def = _NBA_COL_MAP.get(prop)
    if not col_def:
        result["gap"] = f"Prop '{prop}' not mapped for NBA. Check _NBA_COL_MAP."
        return result

    # Find player ID
    full_name = f"{first} {last}".strip()
    matches = _nba_players_static.find_players_by_full_name(full_name)
    if not matches:
        result["gap"] = f"Player '{full_name}' not found in nba_api static list"
        return result

    player_id = matches[0]["id"]

    try:
        gl = _nba_gamelog_ep.PlayerGameLog(
            player_id=player_id,
            season=season,                    # e.g. "2025-26"
            season_type_all_star="Regular Season",
            timeout=10
        )
        df = gl.get_data_frames()[0]
    except Exception as e:
        result["gap"] = f"nba_api fetch error: {e}"
        return result

    if df.empty:
        result["gap"] = "No game log rows returned (player may not have played this season)"
        return result

    # Extract opponent from MATCHUP (e.g. "SAS vs. OKC" or "SAS @ OKC")
    games = []
    for _, row in df.head(10).iterrows():
        if isinstance(col_def, list):
            value = sum(_safe_float(row.get(c, 0)) or 0 for c in col_def)
        else:
            value = _safe_float(row.get(col_def))
        if value is None:
            continue

        matchup = str(row.get("MATCHUP", ""))
        opp = matchup.split("vs.")[-1].strip() if "vs." in matchup \
              else matchup.split("@")[-1].strip() if "@" in matchup else matchup

        games.append({
            "g":       len(games) + 1,
            "date":    str(row.get("GAME_DATE", ""))[:10],
            "opp":     opp,
            "context": str(row.get("MIN", "")),
            "value":   round(float(value), 1),
            "hit":     _hit_label(float(value), line, direction),
            "notes":   "",
        })

    result["games"]    = games
    result["rows"]     = len(games)
    result["complete"] = len(games) >= 10
    if not result["complete"]:
        result["gap"] = f"Only {len(games)} rows available this season"
    result.update(_calc_stats(games, line, direction))
    return result


# ── MLB via Baseball Reference ────────────────────────────────

_BBREF_LAST_REQUEST = 0.0
_BBREF_DELAY        = 4.0   # max 20 req/min

def _bbref_fetch(url: str) -> str | None:
    """Rate-limited fetch for Baseball Reference."""
    global _BBREF_LAST_REQUEST
    elapsed = time.time() - _BBREF_LAST_REQUEST
    if elapsed < _BBREF_DELAY:
        time.sleep(_BBREF_DELAY - elapsed)
    _BBREF_LAST_REQUEST = time.time()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        return resp.text if resp.status_code == 200 else None
    except Exception:
        return None

def _bbref_parse_table(html: str, table_id: str):
    """Parse BBRef table; handles hidden-in-comment case."""
    if not _BS4_AVAILABLE:
        return None
    soup  = BeautifulSoup(html, "lxml")
    table = soup.find("table", {"id": table_id})

    if not table:
        for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
            if table_id in str(comment):
                cs    = BeautifulSoup(str(comment), "lxml")
                table = cs.find("table", {"id": table_id})
                if table:
                    break

    if not table:
        return None
    try:
        df = pd.read_html(str(table))[0]
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [" ".join(str(c) for c in col).strip() for col in df.columns]
        df = df[df.iloc[:, 0] != df.columns[0]]
        df = df[df.iloc[:, 0].notna()].reset_index(drop=True)
        return df
    except Exception:
        return None

def _build_bbref_pid(first: str, last: str, sport: str) -> str:
    f = re.sub(r"[^a-z]", "", first.lower())
    l = re.sub(r"[^a-z]", "", last.lower())
    letter = l[0] if l else "a"
    if sport == "wnba":
        return f"{letter}/{l[:5]}{f[:2]}01w"
    elif sport == "nfl":
        return f"{letter}/{l[:4]}{f[:2]}00"
    return f"{letter}/{l[:5]}{f[:2]}01"

def _l10_bbref(first: str, last: str, sport: str, prop: str,
               direction: str, line: float, year: str) -> dict:
    """Pull MLB (or WNBA/NFL) L10 from Baseball/Basketball/Football Reference."""
    result = {"source": "baseball-reference.com", "games": [],
              "complete": False, "rows": 0, "gap": ""}

    if not _BS4_AVAILABLE:
        result["gap"] = "beautifulsoup4/pandas not installed in Replit env"
        return result

    pid = _build_bbref_pid(first, last, sport)

    cfg = {
        "mlb_pitcher": {
            "base":     "https://www.baseball-reference.com",
            "path":     f"/players/{pid}/pitching_gamelogs/{year}/",
            "table_id": "pitching_gamelogs",
            "ctx_col":  "IP",
        },
        "mlb_batter": {
            "base":     "https://www.baseball-reference.com",
            "path":     f"/players/{pid}/batting_gamelogs/{year}/",
            "table_id": "batting_gamelogs",
            "ctx_col":  "PA",
        },
        "wnba": {
            "base":     "https://www.basketball-reference.com",
            "path":     f"/players/{pid}/gamelog/{year}/",
            "table_id": "pgl_basic",
            "ctx_col":  "MP",
        },
        "nfl": {
            "base":     "https://www.pro-football-reference.com",
            "path":     f"/players/{pid}/gamelog/{year}/",
            "table_id": "stats",
            "ctx_col":  "SnapPct",
        },
    }.get(sport)

    if not cfg:
        result["gap"] = f"BBRef config missing for sport: {sport}"
        return result

    result["source"] = cfg["base"].replace("https://www.", "")
    url  = cfg["base"] + cfg["path"]
    html = _bbref_fetch(url)

    if not html:
        result["gap"] = (
            f"BBRef fetch failed for {url}. "
            "Player ID may need manual correction — check "
            f"{cfg['base']}/players/{pid[0]}/"
        )
        return result

    df = _bbref_parse_table(html, cfg["table_id"])
    if df is None or df.empty:
        result["gap"] = f"Table '{cfg['table_id']}' not found — player ID may be wrong"
        return result

    col_def = _MLB_COL_MAP.get(prop)
    if col_def is None:
        result["gap"] = f"Prop '{prop}' not mapped. Check _MLB_COL_MAP."
        return result

    games = []
    for _, row in df.iterrows():
        row_d = row.to_dict()
        if isinstance(col_def, list):
            value = sum(_safe_float(row_d.get(c, 0)) or 0 for c in col_def)
        else:
            value = _safe_float(row_d.get(col_def))
        if value is None:
            continue

        games.append({
            "g":       len(games) + 1,
            "date":    str(row_d.get("Date", ""))[:10],
            "opp":     str(row_d.get("Opp", "")),
            "context": str(row_d.get(cfg["ctx_col"], "")),
            "value":   round(float(value), 1),
            "hit":     _hit_label(float(value), line, direction),
            "notes":   "",
        })
        if len(games) == 10:
            break

    # Reverse to most-recent-first
    games = list(reversed(games))
    for i, g in enumerate(games):
        g["g"] = i + 1

    result["games"]    = games
    result["rows"]     = len(games)
    result["complete"] = len(games) >= 10
    if not result["complete"]:
        result["gap"] = f"Only {len(games)} rows found — fewer games this season or wrong player ID"
    result.update(_calc_stats(games, line, direction))
    return result


# ── Main route ────────────────────────────────────────────────

@app.route("/wow/l10", methods=["GET"])
def wow_l10():
    """
    WOW L10 Data Endpoint
    ─────────────────────
    GET /wow/l10?player=Victor Wembanyama&sport=nba&prop=Points&direction=MORE&line=19.5

    Params:
      player    Full name (required)
      sport     nba | wnba | mlb_batter | mlb_pitcher | nfl   (required)
      prop      PrizePicks prop name (required)
      direction MORE | LESS  (required)
      line      PrizePicks line as float (required)
      season    NBA season string, default "2025-26"
      year      BBRef year string, default "2026"
      nocache   Pass nocache=1 to bypass cache
    """
    player    = request.args.get("player",    "").strip()
    sport     = request.args.get("sport",     "").strip().lower()
    prop      = request.args.get("prop",      "").strip()
    direction = request.args.get("direction", "MORE").strip().upper()
    line      = request.args.get("line",      type=float)
    season    = request.args.get("season",    "2025-26")
    year      = request.args.get("year",      "2026")
    nocache   = request.args.get("nocache",   "0") == "1"

    # ── Validate ───────────────────────────────────────────────
    if not player:
        return jsonify({"ok": False, "error": "player param required"}), 400
    if not sport:
        return jsonify({"ok": False, "error": "sport param required"}), 400
    if not prop:
        return jsonify({"ok": False, "error": "prop param required"}), 400
    if line is None:
        return jsonify({"ok": False, "error": "line param required (float)"}), 400
    if direction not in ("MORE", "LESS"):
        return jsonify({"ok": False, "error": "direction must be MORE or LESS"}), 400

    # ── Niche prop gate ───────────────────────────────────────
    if prop in _NICHE_PROPS:
        return jsonify({
            "ok":    False,
            "error": f"'{prop}' is a niche prop — requires manual data pull. "
                     "See WOW Rule 3: MLB Gameday for pitches, "
                     "PrizePicks scoring formula for Fantasy Score."
        }), 422

    # ── Cache check ───────────────────────────────────────────
    cache_key = f"{player}|{sport}|{prop}|{line}|{direction}|{year}"
    if not nocache:
        cached = _l10_cache_get(cache_key)
        if cached:
            return jsonify({"ok": True, "cached": True, **cached})

    # ── Route to data source ──────────────────────────────────
    name_parts = player.split(" ", 1)
    first = name_parts[0]
    last  = name_parts[1] if len(name_parts) > 1 else ""

    if sport == "nba":
        data = _l10_nba(first, last, prop, direction, line, season)
    elif sport in ("mlb_batter", "mlb_pitcher", "wnba", "nfl"):
        data = _l10_bbref(first, last, sport, prop, direction, line, year)
    else:
        return jsonify({
            "ok":    False,
            "error": f"Unsupported sport: '{sport}'. "
                     "Supported: nba, wnba, mlb_batter, mlb_pitcher, nfl"
        }), 400

    # ── Build response ────────────────────────────────────────
    response_data = {
        "player":            player,
        "sport":             sport,
        "prop":              prop,
        "direction":         direction,
        "line":              line,
        "pulled_at":         datetime.now().strftime("%H:%M:%S"),
        "source":            data.get("source"),
        "rows":              data.get("rows", 0),
        "complete":          data.get("complete", False),
        "gap":               data.get("gap", ""),
        "games":             data.get("games", []),
        "l5_avg":            data.get("l5_avg"),
        "l10_avg":           data.get("l10_avg"),
        "l10_median":        data.get("l10_median"),
        "l5_hit_rate":       data.get("l5_hit_rate"),
        "l10_hit_rate":      data.get("l10_hit_rate"),
        "edge":              data.get("edge"),
        "confidence_tier":   _confidence_tier(
                                 data.get("rows", 0),
                                 data.get("complete", False)
                             ),
    }

    # Cache if we got enough rows
    if data.get("rows", 0) >= 3:
        _l10_cache_set(cache_key, response_data)

    return jsonify({"ok": True, "cached": False, **response_data})
