# ═══════════════════════════════════════════════════════════════
# WOW /wow/l10/v2 ADDENDUM
# Paste into app.py BEFORE the if __name__ == "__main__": line
#
# BACKWARD COMPAT: /wow/l10 (v1) stays live unchanged.
#                  This adds /wow/l10/v2 alongside it.
#
# NEW DEP — add to requirements.txt then restart:
#   cloudscraper>=1.2.71
#
# Everything else (requests, bs4, pandas) already in env.
# ═══════════════════════════════════════════════════════════════

import re
import time
import statistics
from datetime import datetime

try:
    from bs4 import BeautifulSoup, Comment
    import pandas as pd
    _BS4_OK = True
except ImportError:
    _BS4_OK = False

try:
    from nba_api.stats.static import players as _nba_players_static
    from nba_api.stats.endpoints import playergamelog as _nba_gl_ep
    _NBA_OK = True
except ImportError:
    _NBA_OK = False

try:
    import cloudscraper as _cs_lib
    _CS_OK = True
except ImportError:
    _CS_OK = False


# ── Shared TTL cache ─────────────────────────────────────────
# Response cache: 1 hour (player + prop + line combos)
_L10V2_CACHE: dict = {}
_L10V2_TTL = 3600

# Game PBP cache: 24 hours (completed games never change)
# Key: gamePk (int), Value: (timestamp, allPlays list)
_PBP_CACHE: dict = {}
_PBP_TTL = 86400

def _cache_get(store: dict, key, ttl: int):
    e = store.get(key)
    return e[1] if e and (time.time() - e[0]) < ttl else None

def _cache_set(store: dict, key, val):
    store[key] = (time.time(), val)


# ── Shared helpers ───────────────────────────────────────────
def _f(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None

def _hit(value: float, line: float, direction: str) -> str:
    if value == line: return "PUSH"
    if direction == "MORE": return "HIT" if value > line else "MISS"
    return "HIT" if value < line else "MISS"

def _stats(games: list, line: float, direction: str) -> dict:
    if not games: return {}
    vals  = [g["value"] for g in games]
    l5v   = vals[:5]; l10v = vals[:10]
    l5h   = sum(1 for g in games[:5]  if g["hit"] == "HIT")
    l10h  = sum(1 for g in games[:10] if g["hit"] == "HIT")
    avg10 = sum(l10v) / len(l10v)
    edge  = round((avg10 - line) if direction == "MORE" else (line - avg10), 2)
    return {
        "l5_avg":       round(sum(l5v) / len(l5v), 2),
        "l10_avg":      round(avg10, 2),
        "l10_median":   round(statistics.median(l10v), 2),
        "l5_hit_rate":  f"{l5h}/5 ({round(l5h/5*100)}%)",
        "l10_hit_rate": f"{l10h}/{len(l10v)} ({round(l10h/len(l10v)*100)}%)",
        "edge":         edge,
    }

def _tier(rows: int, complete: bool) -> str:
    if complete and rows >= 10: return "FINAL LOCK ELIGIBLE"
    if rows >= 5:               return "CONDITIONAL — L5 ONLY"
    if rows >= 3:               return "WATCH / RESEARCH ONLY"
    return "REJECT — INSUFFICIENT DATA"


# ─────────────────────────────────────────────────────────────
# NBA via nba_api
# ─────────────────────────────────────────────────────────────
_NBA_COLS = {
    "Points":                "PTS",
    "Rebounds":              "REB",
    "Assists":               "AST",
    "3-PT Made":             "FG3M",
    "Steals":                "STL",
    "Blocks":                "BLK",
    "Free Throws Made":      "FTM",
    "Free Throws Attempted": "FTA",
    "Pts+Rebs+Asts":         ["PTS","REB","AST"],
    "Pts+Rebs":              ["PTS","REB"],
    "Pts+Asts":              ["PTS","AST"],
    "Rebs+Asts":             ["REB","AST"],
}

def _nba(first, last, prop, direction, line, season):
    r = {"source": "stats.nba.com (nba_api)", "games": [],
         "complete": False, "rows": 0, "gap": ""}
    if not _NBA_OK:
        r["gap"] = "nba_api not installed"; return r
    col = _NBA_COLS.get(prop)
    if not col:
        r["gap"] = f"Prop '{prop}' not in NBA column map"; return r
    full = f"{first} {last}".strip()
    matches = _nba_players_static.find_players_by_full_name(full)
    if not matches:
        r["gap"] = f"'{full}' not found in nba_api static list"; return r
    pid = matches[0]["id"]
    try:
        gl = _nba_gl_ep.PlayerGameLog(
            player_id=pid, season=season,
            season_type_all_star="Regular Season", timeout=10)
        df = gl.get_data_frames()[0]
    except Exception as e:
        r["gap"] = f"nba_api error: {e}"; return r
    if df.empty:
        r["gap"] = "No rows returned"; return r
    games = []
    for _, row in df.head(10).iterrows():
        val = sum(_f(row.get(c,0)) or 0 for c in col) if isinstance(col, list) \
              else _f(row.get(col))
        if val is None: continue
        mu  = str(row.get("MATCHUP",""))
        opp = mu.split("vs.")[-1].strip() if "vs." in mu \
              else mu.split("@")[-1].strip() if "@" in mu else mu
        games.append({"g": len(games)+1,
                      "date": str(row.get("GAME_DATE",""))[:10],
                      "opp": opp, "context": str(row.get("MIN","")),
                      "value": round(float(val),1),
                      "hit": _hit(float(val), line, direction), "notes": ""})
    r["games"] = games; r["rows"] = len(games); r["complete"] = len(games) >= 10
    if not r["complete"]: r["gap"] = f"Only {len(games)} rows this season"
    r.update(_stats(games, line, direction)); return r


# ─────────────────────────────────────────────────────────────
# Baseball Reference scraper (MLB / WNBA / NFL)
# ─────────────────────────────────────────────────────────────
_MLB_COLS = {
    "Pitcher Strikeouts": "SO", "Hits Allowed": "H",
    "Earned Runs": "ER",        "Walks Allowed": "BB",
    "Hitter Hits": "H",         "Total Bases": "TB",
    "Runs Scored": "R",         "RBIs": "RBI",
    "H+R+RBI": ["H","R","RBI"], "Hitter Strikeouts": "SO",
}

_BBREF_LAST: float = 0.0
_BBREF_DELAY = 4.0   # 20 req/min max

def _bbref_get(url: str):
    global _BBREF_LAST
    wait = _BBREF_DELAY - (time.time() - _BBREF_LAST)
    if wait > 0: time.sleep(wait)
    _BBREF_LAST = time.time()
    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"})
        return resp.text if resp.status_code == 200 else None
    except Exception:
        return None

def _bbref_table(html: str, tid: str):
    if not _BS4_OK: return None
    soup  = BeautifulSoup(html, "lxml")
    table = soup.find("table", {"id": tid})
    if not table:
        for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
            if tid in str(c):
                cs = BeautifulSoup(str(c), "lxml")
                table = cs.find("table", {"id": tid})
                if table: break
    if not table: return None
    try:
        df = pd.read_html(str(table))[0]
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [" ".join(str(x) for x in c).strip() for c in df.columns]
        df = df[df.iloc[:,0] != df.columns[0]]
        df = df[df.iloc[:,0].notna()].reset_index(drop=True)
        return df
    except Exception:
        return None

def _pid(first, last, sport):
    f = re.sub(r"[^a-z]","", first.lower())
    l = re.sub(r"[^a-z]","", last.lower())
    ltr = l[0] if l else "a"
    if sport == "wnba": return f"{ltr}/{l[:5]}{f[:2]}01w"
    if sport == "nfl":  return f"{ltr}/{l[:4]}{f[:2]}00"
    return f"{ltr}/{l[:5]}{f[:2]}01"

_BBREF_CFG = {
    "mlb_pitcher": ("https://www.baseball-reference.com",
                    "/players/{pid}/pitching_gamelogs/{year}/",
                    "pitching_gamelogs", "IP"),
    "mlb_batter":  ("https://www.baseball-reference.com",
                    "/players/{pid}/batting_gamelogs/{year}/",
                    "batting_gamelogs", "PA"),
    "wnba":        ("https://www.basketball-reference.com",
                    "/players/{pid}/gamelog/{year}/", "pgl_basic", "MP"),
    "nfl":         ("https://www.pro-football-reference.com",
                    "/players/{pid}/gamelog/{year}/", "stats", "SnapPct"),
}

def _bbref(first, last, sport, prop, direction, line, year):
    r = {"source": "", "games": [], "complete": False, "rows": 0, "gap": ""}
    if not _BS4_OK:
        r["gap"] = "beautifulsoup4/lxml not installed"; return r
    cfg = _BBREF_CFG.get(sport)
    if not cfg:
        r["gap"] = f"No BBRef config for sport: {sport}"; return r
    base, path_tpl, tid, ctx = cfg
    r["source"] = base.replace("https://www.","")
    url  = base + path_tpl.format(pid=_pid(first, last, sport), year=year)
    html = _bbref_get(url)
    if not html:
        r["gap"] = f"BBRef fetch failed — {url}"; return r
    df = _bbref_table(html, tid)
    if df is None or df.empty:
        r["gap"] = f"Table '{tid}' not found — player ID may be wrong"; return r
    col = _MLB_COLS.get(prop)
    if not col:
        r["gap"] = f"Prop '{prop}' not in MLB column map"; return r
    games = []
    for _, row in df.iterrows():
        rd = row.to_dict()
        val = sum(_f(rd.get(c,0)) or 0 for c in col) if isinstance(col,list) \
              else _f(rd.get(col))
        if val is None: continue
        games.append({"g": len(games)+1,
                      "date": str(rd.get("Date",""))[:10],
                      "opp":  str(rd.get("Opp","")),
                      "context": str(rd.get(ctx,"")),
                      "value": round(float(val),1),
                      "hit": _hit(float(val), line, direction),
                      "notes": ""})
        if len(games) == 10: break
    games = list(reversed(games))
    for i, g in enumerate(games): g["g"] = i+1
    r["games"] = games; r["rows"] = len(games); r["complete"] = len(games) >= 10
    if not r["complete"]: r["gap"] = f"Only {len(games)} rows found"
    r.update(_stats(games, line, direction)); return r


# ─────────────────────────────────────────────────────────────
# Pitcher Fantasy Score — BBRef reconstruct
#
# Confirmed formula (oddsassist.com example):
#   6 IP (18 outs) + 8 K + 2 ER + QS → 18 + 24 − 6 + 4 = 40
#   +1/out  +3/K  −3/ER  +4/QS(≥18 outs & ≤3 ER)  +6/W
# ─────────────────────────────────────────────────────────────
def _ip_to_outs(ip):
    try:
        ip = float(str(ip))
        full = int(ip)
        frac = round(ip - full, 1)
        return float(full * 3 + {0.0:0, 0.1:1, 0.2:2}.get(frac, 0))
    except (ValueError, TypeError):
        return 0.0

def _pitcher_fs(outs, ks, er, win=False):
    qs = outs >= 18 and er <= 3
    return round(outs + ks*3 - er*3 + (4 if qs else 0) + (6 if win else 0), 1)

def _pitcher_fantasy_score(first, last, direction, line, year):
    r = {"source": "baseball-reference.com (PP formula reconstruct)",
         "formula": "+1/out  +3/K  −3/ER  +4/QS  +6/W",
         "games": [], "complete": False, "rows": 0, "gap": ""}
    if not _BS4_OK:
        r["gap"] = "beautifulsoup4/lxml not installed"; return r
    url  = (f"https://www.baseball-reference.com"
            f"/players/{_pid(first,last,'mlb_pitcher')}"
            f"/pitching_gamelogs/{year}/")
    html = _bbref_get(url)
    if not html:
        r["gap"] = f"BBRef fetch failed — {url}"; return r
    df = _bbref_table(html, "pitching_gamelogs")
    if df is None or df.empty:
        r["gap"] = "pitching_gamelogs table not found"; return r
    games = []
    for _, row in df.iterrows():
        rd = row.to_dict()
        ip = rd.get("IP","0")
        if not ip or str(ip) in ("IP","nan",""): continue
        outs = _ip_to_outs(ip)
        if outs == 0: continue
        ks  = int(_f(rd.get("SO",0)) or 0)
        er  = int(_f(rd.get("ER",0)) or 0)
        win = str(rd.get("Dec","")).strip().upper() == "W"
        fs  = _pitcher_fs(outs, ks, er, win)
        qs  = outs >= 18 and er <= 3
        games.append({"g": len(games)+1,
                      "date": str(rd.get("Date",""))[:10],
                      "opp":  str(rd.get("Opp","")),
                      "context": f"{ip} IP",
                      "value": fs,
                      "hit": _hit(fs, line, direction),
                      "notes": f"{ks}K {er}ER"
                               + (" QS" if qs  else "")
                               + (" W"  if win else "")})
        if len(games) == 10: break
    games = list(reversed(games))
    for i, g in enumerate(games): g["g"] = i+1
    r["games"] = games; r["rows"] = len(games); r["complete"] = len(games) >= 10
    if not r["complete"]: r["gap"] = f"Only {len(games)} starts found"
    r.update(_stats(games, line, direction)); return r


# ─────────────────────────────────────────────────────────────
# 1st Inning Pitches — MLB Stats API (free, official, no key)
#
# PBP cache: 24h per gamePk — completed games never change.
# A single L10 request fires ≤10 PBP fetches on first call,
# then zero on any repeat within 24h.
# Each feed is 1-3 MB; responses are not stored in memory raw —
# only the extracted pitch count integer is cached per gamePk.
# ─────────────────────────────────────────────────────────────
_MLB_API = "https://statsapi.mlb.com/api/v1"

def _mlb_get(path, params=None):
    try:
        r = requests.get(f"{_MLB_API}{path}", params=params,
                         timeout=10, headers={"User-Agent":"WOW/1.0"})
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None

def _mlb_player_id(first, last):
    d = _mlb_get("/people/search", {"names": f"{first} {last}", "sportId": 1})
    if not d: return None
    people = d.get("people", [])
    if not people: return None
    for p in people:
        if p.get("primaryPosition",{}).get("abbreviation") in ("P","SP","RP"):
            return p["id"]
    return people[0]["id"]

def _mlb_game_pks(player_id, season):
    d = _mlb_get(f"/people/{player_id}/stats",
                 {"stats":"gameLog","group":"pitching","season":season})
    if not d: return []
    splits = d.get("stats",[{}])[0].get("splits",[])
    out = []
    for s in splits:
        gm = s.get("game",{})
        out.append({"gamePk": gm.get("gamePk"),
                    "date":   s.get("date","")[:10],
                    "opp":    s.get("opponent",{}).get("abbreviation","?")})
    return out[-10:]  # last 10 starts, oldest first

def _count_pitches_inning1(game_pk: int, pitcher_id: int) -> int | None:
    """
    Count pitches thrown by pitcher in inning 1.
    Result cached per gamePk for 24h — raw JSON is NOT stored,
    only the final integer, keeping memory usage flat.
    """
    cache_key = f"{game_pk}:{pitcher_id}"
    cached = _cache_get(_PBP_CACHE, cache_key, _PBP_TTL)
    if cached is not None:
        return cached

    d = _mlb_get(f"/game/{game_pk}/playByPlay")
    if not d: return None

    count = 0
    found = False
    for play in d.get("allPlays", []):
        ab = play.get("about", {})
        if ab.get("inning", 0) > 1: break
        if ab.get("inning", 0) != 1: continue
        if play.get("matchup",{}).get("pitcher",{}).get("id") != pitcher_id:
            continue
        found = True
        for ev in play.get("playEvents", []):
            if ev.get("isPitch", False):
                count += 1

    result = count if found else None
    if result is not None:
        _cache_set(_PBP_CACHE, cache_key, result)
    return result

def _first_inn_pitches(first, last, direction, line, season):
    r = {"source": "statsapi.mlb.com (official, no key)",
         "games": [], "complete": False, "rows": 0, "gap": ""}
    pid = _mlb_player_id(first, last)
    if not pid:
        r["gap"] = f"'{first} {last}' not found in MLB Stats API"; return r
    pks = _mlb_game_pks(pid, season)
    if not pks:
        r["gap"] = f"No game log found for season {season}"; return r
    games = []
    for entry in reversed(pks):  # most recent first
        gp = entry.get("gamePk")
        if not gp: continue
        pitches = _count_pitches_inning1(gp, pid)
        if pitches is None: continue
        games.append({"g": len(games)+1,
                      "date": entry["date"], "opp": entry["opp"],
                      "context": "1st Inn.", "value": float(pitches),
                      "hit": _hit(float(pitches), line, direction),
                      "notes": f"gamePk:{gp}"})
        if len(games) == 10: break
    r["games"] = games; r["rows"] = len(games); r["complete"] = len(games) >= 10
    if not r["complete"]: r["gap"] = f"Only {len(games)} starts in MLB API"
    r.update(_stats(games, line, direction)); return r


# ─────────────────────────────────────────────────────────────
# CS2 — HLTV (cloudscraper for Cloudflare bypass)
# Note: if this starts returning empty results, cloudscraper's
# challenge solver may be stale vs CF update. First suspect.
# ─────────────────────────────────────────────────────────────
_CS2_COLS = {
    "Kills": "Kills", "Headshots": "Headshots",
    "Maps 1-2 Kills": "Kills", "Maps 1-2 Headshots": "Headshots",
    "Rating": "Rating",
}

def _cs2(player_name, hltv_id, prop, direction, line):
    r = {"source": "hltv.org", "games": [], "complete": False,
         "rows": 0, "gap": ""}
    if not _CS_OK:
        r["gap"] = "cloudscraper not installed — add to requirements.txt"; return r
    slug = player_name.lower().replace(" ","-")
    url  = f"https://www.hltv.org/stats/players/matches/{hltv_id}/{slug}"
    scraper = _cs_lib.create_scraper(
        browser={"browser":"chrome","platform":"windows","desktop":True})
    try:
        resp = scraper.get(url, timeout=20)
    except Exception as e:
        r["gap"] = f"HLTV fetch exception: {e}"; return r
    if resp.status_code != 200:
        r["gap"] = (f"HLTV HTTP {resp.status_code} — "
                    "cloudscraper challenge may be stale, try Playwright fallback")
        return r
    if not _BS4_OK:
        r["gap"] = "beautifulsoup4 not installed"; return r
    soup  = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", {"class": "stats-table"})
    if not table:
        r["gap"] = "HLTV stats-table not found — page structure may have changed"
        return r
    field = _CS2_COLS.get(prop, "Kills")
    thead = table.find("thead")
    headers = [th.get_text(strip=True) for th in thead.find_all("th")] if thead else []
    try:
        fi = headers.index(field)
    except ValueError:
        r["gap"] = f"Column '{field}' not in HLTV headers: {headers}"; return r
    games = []
    tbody = table.find("tbody")
    rows  = tbody.find_all("tr") if tbody else []
    for tr in rows[:10]:
        cols = tr.find_all("td")
        if len(cols) <= fi: continue
        val = _f(cols[fi].get_text(strip=True))
        if val is None: continue
        date_txt = cols[0].get_text(strip=True) if cols else ""
        opp_a    = cols[2].find("a") if len(cols) > 2 else None
        opp_txt  = opp_a.get_text(strip=True) if opp_a \
                   else (cols[2].get_text(strip=True) if len(cols) > 2 else "")
        map_txt  = cols[3].get_text(strip=True) if len(cols) > 3 else ""
        games.append({"g": len(games)+1, "date": date_txt, "opp": opp_txt,
                      "context": map_txt, "value": val,
                      "hit": _hit(val, line, direction), "notes": ""})
    r["games"] = games; r["rows"] = len(games); r["complete"] = len(games) >= 10
    if not r["complete"]: r["gap"] = f"Only {len(games)} HLTV rows"
    r.update(_stats(games, line, direction)); return r


# ─────────────────────────────────────────────────────────────
# Tennis — Tennis Abstract (static HTML, no anti-bot)
# ─────────────────────────────────────────────────────────────
_TENNIS_COLS = {
    "Aces": "Aces", "Double Faults": "DblFaults", "1st Serve %": "1stIn",
}

def _tennis(first, last, prop, direction, line):
    r = {"source": "tennisabstract.com", "games": [],
         "complete": False, "rows": 0, "gap": ""}
    if not _BS4_OK:
        r["gap"] = "pandas not installed"; return r
    name = f"{first}{last}".replace(" ","")
    url  = f"http://tennisabstract.com/cgi-bin/player.cgi?p={name}"
    try:
        resp = requests.get(url, timeout=10,
                            headers={"User-Agent":"Mozilla/5.0"})
        if resp.status_code != 200:
            r["gap"] = f"Tennis Abstract HTTP {resp.status_code}"; return r
        tables = pd.read_html(resp.text)
    except Exception as e:
        r["gap"] = f"Fetch/parse error: {e}"; return r
    match_df = None
    for t in tables:
        cols = [str(c).strip() for c in t.columns]
        if any(k in cols for k in ("Opponent","opponent","Result","result")):
            match_df = t; break
    if match_df is None:
        r["gap"] = (f"Match table not found — verify name at "
                    f"tennisabstract.com/cgi-bin/player.cgi?p={name}")
        return r
    match_df.columns = [str(c).strip() for c in match_df.columns]
    match_df = match_df.dropna(how="all").reset_index(drop=True)
    field = _TENNIS_COLS.get(prop)
    if not field or field not in match_df.columns:
        r["gap"] = (f"Column '{field}' not found. "
                    f"Available: {list(match_df.columns)}")
        return r
    games = []
    for _, row in match_df.head(10).iterrows():
        val = _f(row.get(field))
        if val is None: continue
        games.append({"g": len(games)+1,
                      "date":    str(row.get("Date", row.get("date",""))).strip()[:10],
                      "opp":     str(row.get("Opponent", row.get("opponent",""))).strip(),
                      "context": str(row.get("Surface", row.get("surface",""))).strip(),
                      "value":   val,
                      "hit":     _hit(val, line, direction),
                      "notes":   str(row.get("Result","")).strip()})
    r["games"] = games; r["rows"] = len(games); r["complete"] = len(games) >= 10
    if not r["complete"]: r["gap"] = f"Only {len(games)} match rows"
    r.update(_stats(games, line, direction)); return r


# ─────────────────────────────────────────────────────────────
# /wow/l10/v2 — Main route
# /wow/l10 (v1) is UNCHANGED — backward compat preserved.
# ─────────────────────────────────────────────────────────────
@app.route("/wow/l10/v2", methods=["GET"])
def wow_l10_v2():
    """
    WOW L10 Data Endpoint v2
    ────────────────────────
    sport:  nba | wnba | mlb_batter | mlb_pitcher | nfl | cs2 | tennis
    prop:   any PrizePicks prop name
    Special prop routing (auto, sport param still required):
      "Pitcher Fantasy Score"   → BBRef reconstruct (mlb_pitcher)
      "1st Inn. Pitches Thrown" → MLB Stats API    (mlb_pitcher)
    CS2 requires: &hltv_id=<NNNN>  (look up once at hltv.org/stats/players)

    Examples:
      /wow/l10/v2?player=Victor+Wembanyama&sport=nba&prop=Points&direction=MORE&line=19.5
      /wow/l10/v2?player=Jack+Kochanowicz&sport=mlb_pitcher&prop=Pitcher+Fantasy+Score&direction=LESS&line=23.5
      /wow/l10/v2?player=Shane+Baz&sport=mlb_pitcher&prop=1st+Inn.+Pitches+Thrown&direction=MORE&line=15.5
      /wow/l10/v2?player=zywoo&sport=cs2&prop=Kills&direction=MORE&line=25.5&hltv_id=11893
    """
    player    = request.args.get("player",    "").strip()
    sport     = request.args.get("sport",     "").strip().lower()
    prop      = request.args.get("prop",      "").strip()
    direction = request.args.get("direction", "MORE").strip().upper()
    line      = request.args.get("line",      type=float)
    season    = request.args.get("season",    "2025-26")
    mlb_ssn   = request.args.get("mlb_season","2026")
    year      = request.args.get("year",      "2026")
    hltv_id   = request.args.get("hltv_id",   type=int)
    nocache   = request.args.get("nocache",   "0") == "1"

    if not all([player, sport, prop]) or line is None:
        return jsonify({"ok": False,
                        "error": "player, sport, prop, line all required"}), 400
    if direction not in ("MORE","LESS"):
        return jsonify({"ok": False,
                        "error": "direction must be MORE or LESS"}), 400

    ck = f"v2|{player}|{sport}|{prop}|{line}|{direction}|{year}"
    if not nocache:
        hit = _cache_get(_L10V2_CACHE, ck, _L10V2_TTL)
        if hit: return jsonify({"ok": True, "cached": True, **hit})

    parts = player.split(" ", 1)
    first = parts[0]; last = parts[1] if len(parts) > 1 else ""

    # Route
    if   prop  == "Pitcher Fantasy Score":
        data = _pitcher_fantasy_score(first, last, direction, line, year)
    elif prop  == "1st Inn. Pitches Thrown":
        data = _first_inn_pitches(first, last, direction, line, mlb_ssn)
    elif sport == "cs2":
        if not hltv_id:
            return jsonify({"ok": False,
                            "error": "CS2 needs &hltv_id=NNNN "
                                     "(look up at hltv.org/stats/players)"}), 400
        data = _cs2(player, hltv_id, prop, direction, line)
    elif sport == "tennis":
        data = _tennis(first, last, prop, direction, line)
    elif sport == "nba":
        data = _nba(first, last, prop, direction, line, season)
    elif sport in ("mlb_batter","mlb_pitcher","wnba","nfl"):
        data = _bbref(first, last, sport, prop, direction, line, year)
    else:
        return jsonify({"ok": False,
                        "error": f"Unknown sport '{sport}'"}), 400

    resp = {
        "player": player, "sport": sport, "prop": prop,
        "direction": direction, "line": line,
        "pulled_at": datetime.now().strftime("%H:%M:%S"),
        "source":    data.get("source"),
        "formula":   data.get("formula"),
        "rows":      data.get("rows",0),
        "complete":  data.get("complete",False),
        "gap":       data.get("gap",""),
        "games":     data.get("games",[]),
        "l5_avg":    data.get("l5_avg"),
        "l10_avg":   data.get("l10_avg"),
        "l10_median":data.get("l10_median"),
        "l5_hit_rate":  data.get("l5_hit_rate"),
        "l10_hit_rate": data.get("l10_hit_rate"),
        "edge":      data.get("edge"),
        "confidence_tier": _tier(data.get("rows",0), data.get("complete",False)),
    }
    if data.get("rows",0) >= 3:
        _cache_set(_L10V2_CACHE, ck, resp)
    return jsonify({"ok": True, "cached": False, **resp})
