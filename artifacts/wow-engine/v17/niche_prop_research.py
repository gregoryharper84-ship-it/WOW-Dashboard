"""Niche prop taxonomy and retrospective diagnostics (research-only, no scorer).

Do not treat the operator-selected winning examples as a random sample, a fitted
probability, a calibrated lower bound, or evidence that today's line exists.
Only the governing specialist + existing exact-offer/payout/slip preflight can
make downstream predictions/decisions. No execution path is provided here.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math
import re
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
RESEARCH_ONLY = True
CATEGORY_VERSION = "wow.v17.niche-props.v1"

STUDY_CATEGORIES = (
    "NFL_1Q_TWO_KICKER_FG_OVER_0_5",
    "NBA_LOW_VOLUME_UNDER",
    "NFL_1Q_QB_PASS_ATTEMPTS_OVER",
    "NFL_1Q_QB_FANTASY_SCORE_OVER",
)
NBA_UNDER_STATS = frozenset({
    "THREE_POINT_ATTEMPTS", "PERSONAL_FOULS",
    "FREE_THROW_ATTEMPTS", "TURNOVERS",
})
_STAT_ALIASES = {
    "3PTA": "THREE_POINT_ATTEMPTS", "3PA": "THREE_POINT_ATTEMPTS",
    "3PT_ATTEMPTS": "THREE_POINT_ATTEMPTS",
    "3_POINT_ATTEMPTS": "THREE_POINT_ATTEMPTS",
    "THREE_POINT_ATTEMPTS": "THREE_POINT_ATTEMPTS",
    "FOULS": "PERSONAL_FOULS", "PERSONAL_FOULS": "PERSONAL_FOULS",
    "FTA": "FREE_THROW_ATTEMPTS",
    "FREE_THROW_ATTEMPTS": "FREE_THROW_ATTEMPTS",
    "TO": "TURNOVERS", "TOV": "TURNOVERS", "TURNOVERS": "TURNOVERS",
    "FG_MADE_COMBO": "FIELD_GOALS_MADE_COMBO",
    "FIELD_GOALS_MADE_COMBO": "FIELD_GOALS_MADE_COMBO",
    "FG_MADE_COMBO_1Q": "FIELD_GOALS_MADE_COMBO",
    "PASS_ATTEMPTS": "PASS_ATTEMPTS",
    "PASSING_ATTEMPTS": "PASS_ATTEMPTS",
    "FANTASY_SCORE": "FANTASY_SCORE",
    "FANTASY_POINTS": "FANTASY_SCORE",
}
_PERIOD_ALIASES = {
    "1Q": "FIRST_QUARTER", "Q1": "FIRST_QUARTER",
    "FIRST_QUARTER": "FIRST_QUARTER",
    "FULL": "FULL_GAME", "FULL_GAME": "FULL_GAME",
    "GAME": "FULL_GAME",
}
_SIDE_ALIASES = {"OVER": "MORE", "HIGHER": "MORE",
                 "MORE": "MORE", "UNDER": "LESS",
                 "LOWER": "LESS", "LESS": "LESS"}
_CONTEXT = {
    "NFL_1Q_TWO_KICKER_FG_OVER_0_5": (
        "two verified kicker identities on opposing sides of the same official event",
        "first-quarter possession, drive and red-zone distributions",
        "coaching fourth-down choice, weather and field-goal range",
        "exact 1Q combo settlement and live offered payout",
    ),
    "NBA_LOW_VOLUME_UNDER": (
        "role-conditional minutes, attempts and foul/turnover exposure",
        "opponent-adjusted pace, matchups and usage redistribution",
        "pregame lineup/injury/rotation and high-tail scenarios",
        "exact full-game stat definition, line and payout",
    ),
    "NFL_1Q_QB_PASS_ATTEMPTS_OVER": (
        "confirmed starting QB and first-quarter snap/drive distribution",
        "neutral/pass-script and coaching run/pass tendencies",
        "opponent coverage/pressure and possession uncertainty",
        "exact first-quarter line and payout",
    ),
    "NFL_1Q_QB_FANTASY_SCORE_OVER": (
        "platform first-quarter fantasy scoring rules",
        "first-quarter passing/rushing/TD/sack/turnover distribution",
        "script, opponent, weather and possession sensitivity",
        "exact line and payout",
    ),
}


def _token(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"_+", "_", re.sub(r"[^A-Z0-9]+", "_", value.strip().upper())).strip("_")


def _line(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def classify_niche_prop(leg: Mapping[str, Any]) -> dict[str, Any]:
    """Classify exact niche research thesis; never infer a market or probability.

    Supports the existing slip_research_preflight leg vocabulary. A combination
    is ONE market with two distinct *official* kicker IDs, never two independent
    leg probabilities or a single kicker's player prop.
    """
    if not isinstance(leg, Mapping):
        raise TypeError("NICHE_PROP_LEG_NOT_MAPPING")
    sport = _token(leg.get("sport"))
    stat_raw = _token(leg.get("stat_type") or leg.get("stat_key") or leg.get("stat"))
    stat = _STAT_ALIASES.get(stat_raw, stat_raw)
    period = _PERIOD_ALIASES.get(
        _token(leg.get("market_period") or leg.get("period")),
        _token(leg.get("market_period") or leg.get("period")),
    )
    side = _SIDE_ALIASES.get(_token(leg.get("side") or leg.get("direction")), "")
    line = _line(leg.get("exact_line"))
    category = "OTHER_MARKET"
    gaps: list[str] = []
    if sport == "NFL" and period == "FIRST_QUARTER" and side == "MORE":
        if stat == "FIELD_GOALS_MADE_COMBO" and line == 0.5:
            category = "NFL_1Q_TWO_KICKER_FG_OVER_0_5"
            ids = leg.get("combo_participant_ids")
            if (not isinstance(ids, (list, tuple)) or len(ids) != 2
                    or any(not isinstance(item, str) or not item.strip() for item in ids)
                    or len({item.strip().casefold() for item in ids}) != 2):
                gaps.append("TWO_DISTINCT_OFFICIAL_KICKER_IDS_MISSING")
            if not isinstance(leg.get("official_event_id"), str) or not leg["official_event_id"].strip():
                gaps.append("OFFICIAL_EVENT_ID_MISSING")
            # Two names alone do not prove the combination spans opposing
            # teams in the same official game; this remains source-claimed.
            team_ids = leg.get("combo_team_ids")
            home, away = leg.get("home_team_id"), leg.get("away_team_id")
            if (not isinstance(team_ids, (list, tuple)) or len(team_ids) != 2
                    or any(not isinstance(x, str) or not x.strip() for x in team_ids)
                    or not isinstance(home, str) or not home.strip()
                    or not isinstance(away, str) or not away.strip()
                    or home.strip().casefold() == away.strip().casefold()
                    or {str(x).strip().casefold() for x in team_ids}
                       != {home.strip().casefold(), away.strip().casefold()}):
                gaps.append("COMBO_OPPOSING_TEAM_BINDING_MISSING")
        elif stat == "PASS_ATTEMPTS":
            category = "NFL_1Q_QB_PASS_ATTEMPTS_OVER"
        elif stat == "FANTASY_SCORE":
            category = "NFL_1Q_QB_FANTASY_SCORE_OVER"
    elif sport == "NBA" and period == "FULL_GAME" and side == "LESS" and stat in NBA_UNDER_STATS:
        category = "NBA_LOW_VOLUME_UNDER"

    if category in STUDY_CATEGORIES and category != "NFL_1Q_TWO_KICKER_FG_OVER_0_5":
        if not isinstance(leg.get("official_player_id"), str) or not leg["official_player_id"].strip():
            gaps.append("OFFICIAL_PLAYER_ID_MISSING")
    if category in {"NFL_1Q_QB_PASS_ATTEMPTS_OVER", "NFL_1Q_QB_FANTASY_SCORE_OVER"}:
        if _token(leg.get("official_player_position")) != "QB":
            gaps.append("QB_POSITION_UNVERIFIED")
    if category in STUDY_CATEGORIES and line is None:
        gaps.append("EXACT_LINE_INVALID")

    return {
        "category_version": CATEGORY_VERSION,
        "category": category,
        "stat_type": stat,
        "period": period,
        "side": side,
        "exact_line": line,
        "identity_gaps": gaps,
        "context_to_research": list(_CONTEXT.get(category, ())),
        "research_only": True,
        "probability": None,
        "calibrated_lower_bound": None,
        "expected_value": None,
        "can_execute": False,
    }


def summarize_niche_settlements(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Describe user-supplied settled examples, never backfill pregame confidence.

    Result strings and source references are caller claims, not independently
    verified official outcomes. Duplicated record IDs cannot inflate a hit rate.
    WIN/LOSS are the only denominator; PUSH/VOID remain separate. No subgroup
    estimate can authorize a production specialist or prospective value claim.
    """
    grouped: dict[str, Counter] = defaultdict(Counter)
    source_refs: dict[str, int] = defaultdict(int)
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("NICHE_SETTLEMENT_ROW_INVALID")
        record_id = row.get("record_id")
        if not isinstance(record_id, str) or not record_id.strip() or record_id in seen:
            raise ValueError("NICHE_SETTLEMENT_DUPLICATE_OR_MISSING_RECORD_ID")
        seen.add(record_id)
        result = _token(row.get("settled_result"))
        if result not in {"WIN", "LOSS", "PUSH", "VOID"}:
            raise ValueError("NICHE_SETTLEMENT_RESULT_INVALID")
        category = classify_niche_prop(row)["category"]
        grouped[category][result] += 1
        if isinstance(row.get("settlement_source_ref"), str) and row["settlement_source_ref"].strip():
            source_refs[category] += 1
    categories = {}
    for category in sorted(grouped):
        count = grouped[category]
        decided = count["WIN"] + count["LOSS"]
        categories[category] = {
            "wins": count["WIN"], "losses": count["LOSS"],
            "pushes": count["PUSH"], "voids": count["VOID"],
            "observed_hit_rate": count["WIN"] / decided if decided else None,
            "rows_with_claimed_settlement_ref": source_refs[category],
            "officially_independently_verified": False,
            "pregame_calibration_eligible": False,
        }
    return {
        "schema_version": CATEGORY_VERSION,
        "sample_type": "OPERATOR_SELECTED_RETROSPECTIVE_NOT_RANDOM",
        "leg_count": len(seen),
        "categories": categories,
        "predictive_hit_probability": None,
        "certified_joint_probability": None,
        "estimated_profitability": None,
        "automatic_promotion": False,
        "can_execute": False,
    }
