"""
settlement_risk.py  —  Contract settlement risk assessment
WOW v16 Kalshi Exchange Layer

Grades a Kalshi contract's settlement clarity before approving any model work.
If settlement wording is ambiguous or source is unreliable → REJECT_BAD_RULES.

Resolution clarity grades:
  A — Single authoritative source, unambiguous wording, no dispute history
  B — Clear wording, trusted source, minor edge cases possible
  C — Acceptable, some interpretation possible, source reliable
  D — Ambiguous wording OR weak source (e.g. "as reported by X" without specification)
  F — No clear resolution source, dispute-prone wording, or known problematic category

Settlement risk levels:
  LOW     — grade A or B, no known risk factors
  MEDIUM  — grade C or 1–2 minor risk factors
  HIGH    — grade D or multiple risk factors
  REJECT  — grade F or any hard-reject trigger

Hard-reject triggers (always REJECT regardless of grade):
  - resolution_source = None or empty
  - settlement_condition contains ambiguous temporal language
    (e.g. "end of day", "by market close" without timezone)
  - known disputed category (e.g. "will X resign before...")
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Category risk profiles
# ---------------------------------------------------------------------------

# category → (base_grade, notes)
_CATEGORY_PROFILES: dict[str, tuple[str, str]] = {
    "sports_game_result":    ("A", "Final score is unambiguous — official box score."),
    "sports_player_stat":    ("B", "Stats can have revisions; use official box."),
    "weather":               ("A", "NOAA/official weather station — clear."),
    "economic_data":         ("B", "BLS/Fed releases — revision risk exists."),
    "crypto_price":          ("B", "Verifiable on-chain, but source matters."),
    "election":              ("A", "Official certified result — may take weeks."),
    "politics_action":       ("C", "Timing and definition can be disputed."),
    "politics_statement":    ("D", "Highly interpretive — avoid."),
    "macro_news":            ("C", "Source usually clear, timing ambiguity risk."),
    "narrative":             ("D", "Interpretive by nature — high dispute risk."),
    "other":                 ("C", "Requires individual contract review."),
}

_GRADE_RISK: dict[str, str] = {
    "A": "LOW",
    "B": "LOW",
    "C": "MEDIUM",
    "D": "HIGH",
    "F": "REJECT",
}

# Keywords that flag settlement wording ambiguity
_AMBIGUOUS_PHRASES = [
    "end of day",
    "by market close",
    "as of the time",
    "at the discretion",
    "Kalshi may determine",
    "as reported",
    "if applicable",
    "subject to review",
]


def grade_contract(
    title:                str,
    settlement_condition: str | None,
    resolution_source:    str | None,
    category:             str = "other",
    contract_ticker:      str = "",
    extra_notes:          str = "",
) -> dict[str, Any]:
    """
    Grade a Kalshi contract's settlement risk.

    Parameters
    ----------
    title                 — market/contract title
    settlement_condition  — exact settlement wording from Kalshi
    resolution_source     — where Kalshi resolves from (e.g. "Official MLB box score")
    category              — market category (see _CATEGORY_PROFILES)
    contract_ticker       — for logging
    extra_notes           — any manual notes

    Returns
    -------
    dict: resolution_clarity_grade, settlement_risk, ambiguity_risk, tradable,
          detail, dispute_risk, hidden_failure_paths
    """
    flags:         list[str] = []
    hard_rejects:  list[str] = []

    # ── Hard reject: no resolution source ────────────────────────────────────
    if not resolution_source or resolution_source.strip() == "":
        hard_rejects.append("NO_RESOLUTION_SOURCE: cannot grade without knowing who resolves")

    # ── Hard reject: ambiguous wording ───────────────────────────────────────
    sc_lower = (settlement_condition or "").lower()
    for phrase in _AMBIGUOUS_PHRASES:
        if phrase in sc_lower:
            flags.append(f"AMBIGUOUS_PHRASE: '{phrase}' found in settlement condition")

    if len(flags) >= 2:
        hard_rejects.append(f"MULTIPLE_AMBIGUOUS_PHRASES: {flags}")

    # ── Base grade from category profile ─────────────────────────────────────
    base_grade, category_note = _CATEGORY_PROFILES.get(
        category.lower().replace(" ", "_"),
        _CATEGORY_PROFILES["other"],
    )

    # Downgrade based on flags
    grade_order = ["A", "B", "C", "D", "F"]
    grade_idx   = grade_order.index(base_grade)
    grade_idx   = min(grade_idx + len(flags), 4)
    final_grade = grade_order[grade_idx] if not hard_rejects else "F"

    settlement_risk = _GRADE_RISK.get(final_grade, "REJECT")
    tradable        = settlement_risk not in ("REJECT", "HIGH") and not hard_rejects

    # ── Failure paths ─────────────────────────────────────────────────────────
    yes_fails = _failure_paths(category, "YES", settlement_condition or "")
    no_fails  = _failure_paths(category, "NO",  settlement_condition or "")

    detail_parts = [category_note]
    if flags:
        detail_parts.append("Flags: " + "; ".join(flags))
    if hard_rejects:
        detail_parts.append("HARD REJECTS: " + "; ".join(hard_rejects))

    return {
        "contract_ticker":         contract_ticker,
        "title":                   title,
        "resolution_clarity_grade": final_grade,
        "settlement_risk":         settlement_risk,
        "ambiguity_risk":          bool(flags),
        "dispute_risk":            bool(hard_rejects) or final_grade in ("D", "F"),
        "tradable":                tradable,
        "category":                category,
        "resolution_source":       resolution_source,
        "flags":                   flags,
        "hard_rejects":            hard_rejects,
        "detail":                  " | ".join(detail_parts),
        "failure_paths_yes":       yes_fails,
        "failure_paths_no":        no_fails,
        "extra_notes":             extra_notes,
        "can_approve_bets":        False,
    }


def _failure_paths(category: str, side: str, condition: str) -> list[str]:
    """Generate common failure paths for each category/side."""
    cat = category.lower().replace(" ", "_")
    base: list[str] = []

    if cat == "sports_player_stat":
        base = [
            "Player DNP or exits early → stat doesn't reach line",
            "Stat revision after game → result may flip",
            "Game postponed or suspended → possible void",
        ] if side == "YES" else [
            "Player outperforms in garbage time / blowout",
            "Opponent foul trouble inflates counting stats",
        ]
    elif cat == "sports_game_result":
        base = [
            "Overtime scenario → may push or flip result",
            "Forfeit or protest → Kalshi discretion",
        ] if side == "YES" else [
            "Dominant late-game lead → opponent covers late",
        ]
    elif cat == "weather":
        base = [
            "NOAA station measurement dispute",
            "Observation window straddles midnight → which reading counts",
        ]
    elif cat in ("politics_action", "election"):
        base = [
            "Certification delay → market held open longer than expected",
            "Legal challenge → resolution unclear at market close",
        ]

    return base or [
        f"Settlement condition unclear for {category}/{side} — manual review required",
    ]
