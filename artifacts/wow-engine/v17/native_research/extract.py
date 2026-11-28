"""WOW Native Document Extraction: text, sections and results tables."""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Iterable, Sequence


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    return "\n".join(parser.parts)


def markdown_section(text: str, heading: str, max_chars: int = 2000) -> str | None:
    """Return the body of the first markdown heading matching ``heading`` (regex)."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if match and re.search(heading, match.group(2), re.I):
            level = len(match.group(1))
            body = []
            for nxt in lines[i + 1:]:
                m2 = re.match(r"^(#{1,6})\s+", nxt)
                if m2 and len(m2.group(1)) <= level:
                    break
                body.append(nxt)
            return "\n".join(body).strip()[:max_chars]
    return None


def team_key(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text.replace("&", " and "))
    drop = {"fc", "afc", "cf", "sc", "ac", "ssc", "as", "ss", "us", "sv", "vfb", "vfl", "tsg", "rc", "rcd",
            "cd", "ud", "sd", "ca", "club", "de", "the", "1", "1899", "1846", "1848", "1860", "1909", "1913"}
    alias = {"utd": "united", "man": "manchester", "nottm": "nottingham", "wolves": "wolverhampton",
             "spurs": "tottenham", "bayern": "bayern", "munchen": "munich", "inter": "inter", "psg": "paris"}
    return " ".join(alias.get(tok, tok) for tok in text.split() if tok not in drop)


@dataclass(frozen=True)
class ResultRow:
    day: date
    home: str
    away: str
    home_goals: int | None
    away_goals: int | None

    @property
    def scored(self) -> bool:
        return self.home_goals is not None and self.away_goals is not None


_ALIASES = {
    "date": ("date", "match_date", "matchdate", "day", "kickoff_date"),
    "home": ("home", "home_team", "hometeam", "team1", "home_name"),
    "away": ("away", "away_team", "awayteam", "team2", "away_name"),
    "hg": ("ft_home", "home_goals", "fthg", "hg", "home_score", "score1", "homegoals"),
    "ag": ("ft_away", "away_goals", "ftag", "ag", "away_score", "score2", "awaygoals"),
}


def _parse_day(value: str) -> date | None:
    value = (value or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _goal(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(value) if value.isdigit() else None


def parse_results_csv(text: str) -> list[ResultRow]:
    """Parse a results/fixtures table whose header uses common column names."""
    reader = csv.DictReader(io.StringIO(text))
    header = {re.sub(r"[^a-z0-9_]", "", (h or "").lower()): h for h in (reader.fieldnames or [])}
    cols = {}
    for key, options in _ALIASES.items():
        cols[key] = next((header[o] for o in options if o in header), None)
    if not all(cols[k] for k in ("date", "home", "away")):
        raise ValueError("NRE_RESULTS_TABLE_COLUMNS_UNRECOGNIZED")
    rows = []
    for raw in reader:
        day = _parse_day(raw.get(cols["date"]) or "")
        home, away = (raw.get(cols["home"]) or "").strip(), (raw.get(cols["away"]) or "").strip()
        if not day or not home or not away:
            continue
        rows.append(ResultRow(day, home, away, _goal(raw.get(cols["hg"])) if cols["hg"] else None,
                              _goal(raw.get(cols["ag"])) if cols["ag"] else None))
    return rows


def parse_openfootball(matches: Iterable[dict]) -> list[ResultRow]:
    rows = []
    for m in matches:
        day = _parse_day(str(m.get("date") or ""))
        score = m.get("score")
        ft = score.get("ft") if isinstance(score, dict) else score
        hg = ag = None
        if isinstance(ft, (list, tuple)) and len(ft) == 2 and all(isinstance(x, int) for x in ft):
            hg, ag = ft
        if day and m.get("team1") and m.get("team2"):
            rows.append(ResultRow(day, str(m["team1"]).strip(), str(m["team2"]).strip(), hg, ag))
    return rows


# League and season tokens used to locate files inside a discovered dataset.
LEAGUE_TOKENS = {
    "EPL": ("premier-league", "premier_league", "eng_1", "eng1", "epl", "/e0"),
    "LALIGA": ("la-liga", "laliga", "la_liga", "esp_1", "esp1", "/sp1"),
    "BUNDESLIGA": ("bundesliga", "ger_1", "ger1", "/d1."),
    "SERIE_A": ("serie-a", "serie_a", "seriea", "ita_1", "ita1", "/i1"),
    "LIGUE_1": ("ligue-1", "ligue_1", "ligue1", "fra_1", "fra1", "/f1"),
}
LEAGUE_NAMES = {"EPL": "premier league", "LALIGA": "la liga", "BUNDESLIGA": "bundesliga",
                "SERIE_A": "serie a", "LIGUE_1": "ligue 1"}


def season_tokens(season: str) -> tuple[str, ...]:
    """'2026-27' -> tokens a dataset may use for that season."""
    start = int(season[:4])
    a, b = str(start), str(start + 1)
    return (f"{a[2:]}{b[2:]}", f"{a}-{b[2:]}", f"{a}_{b[2:]}", f"{a}-{b}", f"{a}_{b}", f"{a}{b}")


LEAGUE_COUNTRY = {"EPL": ("eng", "england"), "LALIGA": ("esp", "spain"), "BUNDESLIGA": ("ger", "germany", "deu"),
                  "SERIE_A": ("ita", "italy"), "LIGUE_1": ("fra", "france")}
# Country codes that mark another competition with the same name (Austrian
# Bundesliga, Brazilian Serie A, ...). Such paths are never used.
COUNTRY_CODES = frozenset("eng esp ger deu ita fra aut bra sui ned por bel sco tur gre rus usa arg mex jpn chn aus "
                          "den nor swe pol cze rou ukr cro srb hun svk svn bul isr cyp irl wal nir fin isl col chi "
                          "per ecu uru par bol ven kor ksa egy rsa".split())


def find_league_files(paths: Sequence[str], league: str, season: str) -> list[str]:
    tokens = LEAGUE_TOKENS.get(league, ())
    own = set(LEAGUE_COUNTRY.get(league, ()))
    seasons = season_tokens(season)
    hits = []
    for path in paths:
        low = "/" + path.lower()
        if not low.endswith((".csv", ".json")):
            continue
        if not (any(t in low for t in tokens) and any(s in low for s in seasons)):
            continue
        parts = [x for x in re.split(r"[/_\-.\s]+", low) if x]
        if any(x in COUNTRY_CODES and x not in own for x in parts):
            continue
        division = next((parts[i + 1] for i, x in enumerate(parts[:-1]) if x in own and parts[i + 1].isdigit()), None)
        if division not in (None, "1"):
            continue  # a lower division of the right country
        # Country-qualified top flight first, then explicit results files.
        score = (0 if division == "1" else 1, 0 if "result" in low else 1 if "match" in low else 2, len(low))
        hits.append((score, path))
    return [p for _, p in sorted(hits)]
