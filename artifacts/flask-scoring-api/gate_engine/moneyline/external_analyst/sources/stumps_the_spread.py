"""
gate_engine/moneyline/external_analyst/sources/stumps_the_spread.py
WOW-PATCH-2026-08-08-EXTERNAL-ANALYST-INTELLIGENCE

StumpTheSpread (stumpsthespread.com) free picks adapter.

Access model:
  1. Enrichment-supplied data (primary): enrichment["external_analyst_picks"]
     ["stumps_the_spread"] — GPT operator supplies pre-fetched data.
  2. HTTP retrieval (fallback): publicly accessible picks pages.
     Uses a resilient text-pattern approach (not CSS-class dependent).
     Fails gracefully to DATA_UNOBTAINABLE.

Direct probability weight: 0.0 ALWAYS.
Analyst claims → narrative/thesis tags ONLY.
Verified factual claims must flow through independent verification before
entering failure_path_matrix or sport model.

Supported sports: MLB, NBA, NFL, NCAAF, NCAAB (where STS publishes picks).

can_execute=False unconditional.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from gate_engine.moneyline.external_analyst.sources.base import ExternalAnalystSourceBase
from gate_engine.moneyline.external_analyst.types import (
    AnalystOpinion,
    AnalystSourceStatus,
    ThesisTags,
)
from gate_engine.moneyline.external_analyst.family_resolver import (
    resolve_source_family,
    resolve_analyst_family,
)

can_execute: bool = False  # UNCONDITIONAL

# ---------------------------------------------------------------------------
# Sport → STS URL path mapping
# ---------------------------------------------------------------------------

_SPORT_URL_MAP: dict[str, str] = {
    "MLB":   "https://www.stumpsthespread.com/mlb-picks/",
    "NBA":   "https://www.stumpsthespread.com/nba-picks/",
    "NFL":   "https://www.stumpsthespread.com/nfl-picks/",
    "NCAAF": "https://www.stumpsthespread.com/college-football-picks/",
    "NCAAB": "https://www.stumpsthespread.com/college-basketball-picks/",
    "WNBA":  "https://www.stumpsthespread.com/wnba-picks/",
}

_HTTP_TIMEOUT = 12   # seconds
_MAX_AGE_HOURS = 4   # hours before marking STALE

# Thesis keyword patterns for structured extraction
_THESIS_PATTERNS = {
    "starter_pitcher_thesis": [
        "starter", "starting pitcher", "sp ", "ace", "eras", "era ", "whip",
        "pitch count", "pitch limit", "innings limit", "days rest",
    ],
    "bullpen_thesis": [
        "bullpen", "relief", "closer", "middle relief", "pen ", "reliever",
        "overused", "depleted", "fatigued",
    ],
    "offense_form_thesis": [
        "offense", "scoring", "runs", "batting", "hitting", "lineup", "wrc+",
        "ops", "slash line", "hot", "cold", "streak",
    ],
    "lineup_injury_thesis": [
        "injury", "injured", "out", "il ", "disabled list", "day-to-day",
        "questionable", "scratched", "absence", "return from",
    ],
    "historical_matchup_thesis": [
        "history", "historical", "matchup", "head to head", "h2h",
        "batter vs pitcher", "career stats",
    ],
    "home_road_thesis": [
        "home", "road", "away", "travel", "home field", "home court",
        "home advantage",
    ],
    "weather_venue_thesis": [
        "weather", "wind", "rain", "temperature", "dome", "outdoor",
        "park factor", "venue",
    ],
    "rest_travel_thesis": [
        "rest", "back-to-back", "b2b", "travel", "fatigue", "days off",
        "schedule",
    ],
    "market_value_thesis": [
        "value", "line", "odds", "juice", "price", "public",
        "sharp", "action", "steam", "move",
    ],
}


class StumpsTheSpreadAdapter(ExternalAnalystSourceBase):
    """
    StumpTheSpread free picks adapter.

    Works with both enrichment-supplied data and live HTTP retrieval.
    All failures return DATA_UNOBTAINABLE and never raise.
    """

    source_name   = "stumpsthespread.com"
    source_family = "stumps_the_spread"

    def fetch(
        self,
        sport:      str,
        team:       str,
        opponent:   str,
        event_date: str | None = None,
        enrichment: dict[str, Any] | None = None,
    ) -> list[AnalystOpinion]:
        """
        Retrieve StumpTheSpread picks for the given matchup.

        Tries enrichment["external_analyst_picks"]["stumps_the_spread"] first,
        then falls back to HTTP retrieval.
        """
        sport_upper = sport.upper().strip()

        # ── 1. Enrichment-supplied data (highest priority) ──────────────────
        if enrichment:
            supplied = (
                enrichment.get("external_analyst_picks") or {}
            ).get("stumps_the_spread")
            if supplied:
                return self._parse_supplied_data(
                    supplied, sport_upper, team, opponent, event_date
                )

        # ── 2. HTTP retrieval ────────────────────────────────────────────────
        url = _SPORT_URL_MAP.get(sport_upper)
        if not url:
            return self._unobtainable_opinion(
                sport, team, opponent, event_date,
                f"StumpTheSpread: unsupported sport {sport_upper}",
            )

        try:
            picks = self._fetch_http(url, sport_upper, team, opponent, event_date)
            return picks if picks else self._unobtainable_opinion(
                sport, team, opponent, event_date,
                "StumpTheSpread: no matching pick found for this matchup",
            )
        except Exception as exc:
            return self._unobtainable_opinion(
                sport, team, opponent, event_date,
                f"StumpTheSpread: HTTP retrieval failed ({exc!s:.80})",
            )

    # ── Enrichment-supplied data parser ─────────────────────────────────────

    def _parse_supplied_data(
        self,
        data:       Any,
        sport:      str,
        team:       str,
        opponent:   str,
        event_date: str | None,
    ) -> list[AnalystOpinion]:
        """Parse pre-supplied StumpTheSpread data from the enrichment dict."""
        now_iso = datetime.now(timezone.utc).isoformat()

        # Accept a single dict or a list of dicts
        items = data if isinstance(data, list) else [data]
        opinions: list[AnalystOpinion] = []

        for item in items:
            if not isinstance(item, dict):
                continue
            op = self._build_opinion_from_dict(item, sport, team, opponent,
                                               event_date, now_iso)
            opinions.append(op)

        if not opinions:
            return self._unobtainable_opinion(
                sport, team, opponent, event_date,
                "StumpTheSpread: supplied data contained no parseable items",
            )
        return opinions

    def _build_opinion_from_dict(
        self,
        item:       dict[str, Any],
        sport:      str,
        team:       str,
        opponent:   str,
        event_date: str | None,
        now_iso:    str,
    ) -> AnalystOpinion:
        analyst_name   = item.get("analyst") or item.get("byline") or None
        analyst_family = resolve_analyst_family(analyst_name, self.source_family)

        raw_side   = str(item.get("pick") or item.get("side") or "").lower().strip()
        team_pick  = item.get("team") or item.get("picked_team") or ""
        side       = self._resolve_side(raw_side, team_pick, team, opponent)

        thesis_text = item.get("reasoning") or item.get("thesis") or ""
        thesis_tags = _extract_thesis_tags(str(thesis_text))

        return AnalystOpinion(
            direct_probability_weight = 0.0,
            source_name    = self.source_name,
            source_family  = self.source_family,
            source_url     = item.get("url") or _SPORT_URL_MAP.get(sport),
            analyst_name   = analyst_name,
            analyst_family = analyst_family,
            retrieved_at   = now_iso,
            published_at   = item.get("published_at"),
            sport          = sport,
            league         = item.get("league") or sport,
            event_id       = item.get("event_id"),
            event_date     = item.get("event_date") or event_date,
            team           = team_pick or team,
            opponent       = opponent,
            side           = side,
            displayed_line = str(item.get("line") or item.get("odds") or ""),
            market_type    = item.get("market_type") or "moneyline",
            favorite_role  = item.get("favorite_role"),
            thesis_tags    = thesis_tags,
            source_status  = AnalystSourceStatus.RETRIEVED,
            acquisition_notes = ["supplied_via_enrichment"],
        )

    # ── HTTP retrieval ───────────────────────────────────────────────────────

    def _fetch_http(
        self,
        url:        str,
        sport:      str,
        team:       str,
        opponent:   str,
        event_date: str | None,
    ) -> list[AnalystOpinion]:
        """
        Fetch the STS picks page and extract picks for this matchup.
        Resilient text-pattern approach — not CSS-class dependent.
        Returns [] if no matching pick found (caller handles unobtainable).
        """
        import requests  # local import to avoid cold-start penalty

        resp = requests.get(
            url,
            timeout   = _HTTP_TIMEOUT,
            headers   = {
                "User-Agent": (
                    "Mozilla/5.0 (compatible; WOW-Research/1.0; "
                    "+https://wow.ai)"
                )
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")

        now_iso = datetime.now(timezone.utc).isoformat()
        text    = resp.text

        return self._extract_picks_from_text(
            text, sport, team, opponent, event_date, url, now_iso
        )

    def _extract_picks_from_text(
        self,
        text:       str,
        sport:      str,
        team:       str,
        opponent:   str,
        event_date: str | None,
        url:        str,
        now_iso:    str,
    ) -> list[AnalystOpinion]:
        """
        Parse free-text HTML for picks matching team/opponent.
        Uses pattern matching on visible text, not HTML structure.
        """
        # Strip HTML tags to get plain text
        plain = re.sub(r"<[^>]+>", " ", text)
        plain = re.sub(r"\s+", " ", plain)

        team_norm     = team.lower().strip()
        opponent_norm = opponent.lower().strip()

        # Look for paragraphs or sentences mentioning both teams
        team_words = [w for w in team_norm.split() if len(w) > 3]
        opp_words  = [w for w in opponent_norm.split() if len(w) > 3]

        # Split into candidate segments (sentence or article blocks)
        segments = re.split(r"(?<=[.!?])\s+|[\n\r]{2,}", plain)

        matched_segments: list[str] = []
        for seg in segments:
            seg_lower = seg.lower()
            team_hit = any(w in seg_lower for w in team_words)
            opp_hit  = any(w in seg_lower for w in opp_words)
            if team_hit and opp_hit:
                matched_segments.append(seg)

        if not matched_segments:
            return []

        combined_text = " ".join(matched_segments)

        # Detect pick direction from pick-indicator patterns
        pick_indicators = {
            "take":  r"(?:take|taking|go with|lean|favor)\s+(?:the\s+)?([A-Z][a-zA-Z\s]+?)(?:\s+[-+]\d+|\s+to|\s+and|\.|,)",
            "best_bet": r"best bet[:\s]+([A-Z][a-zA-Z\s]+?)(?:\s+[-+]\d+|\s*\.)",
        }

        picked_team: str | None = None
        for label, pattern in pick_indicators.items():
            m = re.search(pattern, combined_text, re.IGNORECASE)
            if m:
                picked_team = m.group(1).strip()
                break

        # Fallback: look for team names preceded by strong pick verbs
        if not picked_team:
            for word in team_words:
                verb_re = rf"(?:pick|select|backing|backing the)\s+(?:the\s+)?{re.escape(word)}"
                if re.search(verb_re, combined_text, re.IGNORECASE):
                    picked_team = team
                    break
            for word in opp_words:
                verb_re = rf"(?:pick|select|backing|backing the)\s+(?:the\s+)?{re.escape(word)}"
                if re.search(verb_re, combined_text, re.IGNORECASE):
                    picked_team = opponent
                    break

        side = self._resolve_side(
            "", picked_team or "", team, opponent
        ) if picked_team else None

        # Price extraction
        price_match = re.search(r"([-+]\d{3,4})", combined_text)
        displayed_line = price_match.group(1) if price_match else None

        thesis_tags = _extract_thesis_tags(combined_text)

        opinion = AnalystOpinion(
            direct_probability_weight = 0.0,
            source_name    = self.source_name,
            source_family  = self.source_family,
            source_url     = url,
            analyst_name   = None,
            analyst_family = resolve_analyst_family(None, self.source_family),
            retrieved_at   = now_iso,
            published_at   = None,
            sport          = sport,
            league         = sport,
            event_id       = None,
            event_date     = event_date,
            team           = picked_team or team,
            opponent       = opponent,
            side           = side,
            displayed_line = displayed_line,
            market_type    = "moneyline",
            favorite_role  = None,
            thesis_tags    = thesis_tags,
            source_status  = AnalystSourceStatus.RETRIEVED,
            acquisition_notes = ["http_text_extraction"],
        )
        return [opinion]

    # ── Side resolution ──────────────────────────────────────────────────────

    @staticmethod
    def _resolve_side(
        raw_side: str,
        picked_team: str,
        team: str,
        opponent: str,
    ) -> str | None:
        """
        Map raw pick text / team name to "home" or "away".
        Returns None when ambiguous.
        """
        rs = raw_side.lower().strip()
        if rs in ("home", "home team"):
            return "home"
        if rs in ("away", "away team", "road", "visitor"):
            return "away"

        # Try to match picked team name against our known team/opponent
        if picked_team:
            pt = picked_team.lower().strip()
            team_l = team.lower().strip()
            opp_l  = opponent.lower().strip()

            # Partial match
            team_words = [w for w in team_l.split() if len(w) > 3]
            opp_words  = [w for w in opp_l.split() if len(w) > 3]

            if any(w in pt for w in team_words) or any(w in team_l for w in pt.split() if len(w) > 3):
                return "home"   # caller's "team" is treated as the candidate (home side context)
            if any(w in pt for w in opp_words) or any(w in opp_l for w in pt.split() if len(w) > 3):
                return "away"

        return None   # ambiguous


# ---------------------------------------------------------------------------
# Thesis tag extractor
# ---------------------------------------------------------------------------

def _extract_thesis_tags(text: str) -> ThesisTags:
    """
    Assign thesis categories based on keyword presence in analyst text.
    Multiple categories can fire for the same text.
    The full text is also kept in unverified_narrative.
    """
    text_lower = text.lower()
    tags = ThesisTags(unverified_narrative=[text] if text.strip() else [])

    def _extract_sentences(keywords: list[str]) -> str | None:
        """Find the first sentence containing any keyword."""
        sentences = re.split(r"(?<=[.!?])\s+", text)
        for sent in sentences:
            sl = sent.lower()
            if any(kw in sl for kw in keywords):
                return sent.strip()
        return None

    tags.starter_pitcher_thesis   = _extract_sentences(_THESIS_PATTERNS["starter_pitcher_thesis"])
    tags.bullpen_thesis            = _extract_sentences(_THESIS_PATTERNS["bullpen_thesis"])
    tags.offense_form_thesis       = _extract_sentences(_THESIS_PATTERNS["offense_form_thesis"])
    tags.lineup_injury_thesis      = _extract_sentences(_THESIS_PATTERNS["lineup_injury_thesis"])
    tags.historical_matchup_thesis = _extract_sentences(_THESIS_PATTERNS["historical_matchup_thesis"])
    tags.home_road_thesis          = _extract_sentences(_THESIS_PATTERNS["home_road_thesis"])
    tags.weather_venue_thesis      = _extract_sentences(_THESIS_PATTERNS["weather_venue_thesis"])
    tags.rest_travel_thesis        = _extract_sentences(_THESIS_PATTERNS["rest_travel_thesis"])
    tags.market_value_thesis       = _extract_sentences(_THESIS_PATTERNS["market_value_thesis"])

    return tags
