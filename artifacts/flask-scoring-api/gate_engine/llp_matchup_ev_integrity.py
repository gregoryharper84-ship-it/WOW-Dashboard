"""
WOW-PATCH-2026-08-02-LLP-MATCHUP-EV-INTEGRITY
Precedence 99 — analytical rules only, no dashboard code change required.

Five rules added to the LLP 14-step workflow:
  1. SMALL-SAMPLE MATCHUP FLOOR     — sub-25 PA samples cannot be primary driver
  2. ABSENCE-OF-DATA NEUTRALITY     — zero matchup history = DATA_UNAVAILABLE, never negative signal
  3. EV-CLAIM AUDIT GATE            — EV % must carry model_prob + fair_odds + book + timestamp
  4. VARIANCE-VS-SAFETY SEPARATION  — substitution labeled VARIANCE_INCREASE if hit-prob drops
  5. UPSTREAM DEPENDENCY LOCK       — dependent step cannot complete if upstream is unfinished

can_execute = False  (unconditional)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

can_execute: bool = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MATCHUP_PA_FLOOR: int = 25          # minimum plate appearances (or sport equivalent)
REQUIRED_EV_FIELDS: tuple[str, ...] = ("model_prob", "fair_odds", "book", "timestamp")

ABSENT_MATCHUP_PHRASES: tuple[str, ...] = (
    "no career",
    "0 career",
    "zero career",
    "no history",
    "no matchup",
    "never faced",
    "0 pa",
    "no pa",
)

# Statuses that count as "incomplete" for the dependency lock
INCOMPLETE_STATUSES: frozenset[str] = frozenset({
    "incomplete",
    "running",
    "pending",
    "timeout",
    "timed_out",
    "error",
    "failed",
    "unchecked",
    "not_started",
})

# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class MatchupEvIntegrityResult:
    passed: bool = True
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    label_ceiling: str | None = None          # WATCH cap when triggered
    dropped: bool = False                     # PIPELINE_INTEGRITY_FAILURE → drop from output
    detail: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Rule 1 — Small-sample matchup floor
# ---------------------------------------------------------------------------

def check_matchup_sample_floor(
    sample_pa: int | None,
    is_primary_driver: bool,
) -> MatchupEvIntegrityResult:
    """
    A BvP / head-to-head sample below 25 PA may be shown as context but must
    NEVER be the primary driver of a tier change or direction flip.

    Args:
        sample_pa:        Number of plate appearances (or sport equivalent).
                          Pass None when sample size is unknown.
        is_primary_driver: True when the caller has marked this sample as the
                          stated reason for a tier/side movement.

    Returns:
        MatchupEvIntegrityResult with label_ceiling=WATCH and blocker
        l5-l10-overtrusted when floor is violated.
    """
    result = MatchupEvIntegrityResult()
    if sample_pa is None:
        result.warnings.append("matchup_sample_pa_unknown — treated as context only")
        return result

    if sample_pa < MATCHUP_PA_FLOOR:
        result.detail["sample_pa"] = sample_pa
        result.detail["floor"] = MATCHUP_PA_FLOOR
        if is_primary_driver:
            result.passed = False
            result.blockers.append(
                f"l5-l10-overtrusted: {sample_pa} PA sample below {MATCHUP_PA_FLOOR} PA floor "
                "used as primary driver — capped at WATCH"
            )
            result.label_ceiling = "WATCH"
        else:
            result.warnings.append(
                f"matchup_sample_below_floor ({sample_pa} PA < {MATCHUP_PA_FLOOR} PA) "
                "— context only, not a driver"
            )
    return result


# ---------------------------------------------------------------------------
# Rule 2 — Absence-of-data neutrality
# ---------------------------------------------------------------------------

def check_absence_of_data_neutrality(
    matchup_note: str | None,
) -> MatchupEvIntegrityResult:
    """
    'Zero career matchups' or 'no BvP history' must be DATA_UNAVAILABLE,
    never a negative / cautionary signal.

    Args:
        matchup_note: Free-text rationale string from the candidate row.
                      Pass None to skip.

    Returns:
        MatchupEvIntegrityResult with reasoned-not-modeled blocker when
        absent-data language is used as a negative signal.
    """
    result = MatchupEvIntegrityResult()
    if not matchup_note:
        return result

    note_lower = matchup_note.lower()
    detected_phrase: str | None = None
    for phrase in ABSENT_MATCHUP_PHRASES:
        if phrase in note_lower:
            detected_phrase = phrase
            break

    if detected_phrase:
        result.passed = False
        result.blockers.append(
            f"reasoned-not-modeled: absence-of-matchup-data cited as negative signal "
            f"(phrase: '{detected_phrase}') — must be logged as DATA_UNAVAILABLE, "
            "not used as a risk indicator"
        )
        result.detail["detected_phrase"] = detected_phrase
    return result


# ---------------------------------------------------------------------------
# Rule 3 — EV-claim audit gate
# ---------------------------------------------------------------------------

def check_ev_claim_audit(
    ev_claim: dict[str, Any] | None,
) -> MatchupEvIntegrityResult:
    """
    An EV percentage may only be used in ranking when it carries all four
    mandatory fields: model_prob, fair_odds, book, timestamp.

    Args:
        ev_claim: Dict representing the EV claim, or None when no EV is shown.

    Returns:
        MatchupEvIntegrityResult with missing-projection-support blocker
        when any required field is absent or None.
    """
    result = MatchupEvIntegrityResult()
    if ev_claim is None:
        # No EV shown — no violation
        return result

    missing: list[str] = [
        f for f in REQUIRED_EV_FIELDS if not ev_claim.get(f)
    ]
    if missing:
        result.passed = False
        result.blockers.append(
            f"missing-projection-support: EV claim missing required fields: "
            f"{missing} — EV percentage REJECTED from output (not merely flagged)"
        )
        result.detail["ev_claim_missing_fields"] = missing
        result.detail["ev_claim_received"] = {
            k: ("<present>" if ev_claim.get(k) else "<missing>")
            for k in REQUIRED_EV_FIELDS
        }
    return result


# ---------------------------------------------------------------------------
# Rule 4 — Variance-vs-safety separation
# ---------------------------------------------------------------------------

def check_variance_vs_safety(
    original_hit_prob_lb: float | None,
    replacement_hit_prob_lb: float | None,
    is_claimed_safer: bool,
) -> MatchupEvIntegrityResult:
    """
    A candidate substitution may only be described as 'safer' when the
    replacement's raw hit-probability lower bound is ≥ original's.

    Args:
        original_hit_prob_lb:     Calibrated lower-bound hit-probability of
                                  the candidate being replaced.
        replacement_hit_prob_lb:  Same metric for the proposed replacement.
        is_claimed_safer:         True when the substitution is described as
                                  'safer', 'added safety', etc.

    Returns:
        MatchupEvIntegrityResult with VARIANCE_INCREASE blocker when the swap
        lowers hit-probability but is marketed as a safety upgrade.
    """
    result = MatchupEvIntegrityResult()
    if not is_claimed_safer:
        return result
    if original_hit_prob_lb is None or replacement_hit_prob_lb is None:
        result.warnings.append(
            "variance_vs_safety check skipped — hit_prob_lb unavailable on one or both legs"
        )
        return result

    if replacement_hit_prob_lb < original_hit_prob_lb:
        result.passed = False
        result.blockers.append(
            f"VARIANCE_INCREASE: substitution claimed 'safer' but lowers hit-probability "
            f"lower bound ({original_hit_prob_lb:.3f} → {replacement_hit_prob_lb:.3f}) — "
            "must be labeled VARIANCE_INCREASE, not a safety upgrade"
        )
        result.detail["original_hit_prob_lb"] = original_hit_prob_lb
        result.detail["replacement_hit_prob_lb"] = replacement_hit_prob_lb
    return result


# ---------------------------------------------------------------------------
# Rule 5 — Upstream dependency lock
# ---------------------------------------------------------------------------

def check_upstream_dependency_lock(
    upstream_step_name: str | None,
    upstream_step_status: str | None,
) -> MatchupEvIntegrityResult:
    """
    A dependent step may not return results or be marked 'complete' if its
    upstream analysis step did not complete successfully.

    Args:
        upstream_step_name:   Human-readable name of the analysis step this
                              candidate depends on.
        upstream_step_status: Status string from the pipeline runner.

    Returns:
        MatchupEvIntegrityResult with dropped=True and PIPELINE_INTEGRITY_FAILURE
        blocker when the upstream step is incomplete.
    """
    result = MatchupEvIntegrityResult()
    if upstream_step_status is None:
        # No dependency declared — not a violation
        return result

    status_norm = upstream_step_status.strip().lower()
    if status_norm in INCOMPLETE_STATUSES:
        result.passed = False
        result.dropped = True
        result.blockers.append(
            f"PIPELINE_INTEGRITY_FAILURE: dependent step cannot report results — "
            f"upstream step '{upstream_step_name or 'unknown'}' status='{upstream_step_status}' "
            "— candidate DROPPED from final output"
        )
        result.detail["upstream_step_name"] = upstream_step_name
        result.detail["upstream_step_status"] = upstream_step_status
    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_matchup_ev_integrity(row: dict[str, Any]) -> MatchupEvIntegrityResult:
    """
    Run all five matchup/EV/pipeline integrity checks against a candidate row.

    Expected keys in `row` (all optional — absent keys skip that check):
        matchup_sample_pa       int   — BvP or head-to-head sample size
        matchup_is_primary_driver bool — True when sample drove the decision
        matchup_note            str   — free-text rationale for absence-of-data check
        ev_claim                dict  — {"model_prob": …, "fair_odds": …,
                                          "book": …, "timestamp": …} or None
        original_hit_prob_lb    float — calibrated LB of original candidate
        replacement_hit_prob_lb float — calibrated LB of replacement
        is_claimed_safer        bool  — True when swap pitched as 'safer'
        upstream_step_name      str   — name of the analysis step depended on
        upstream_step_status    str   — status string from pipeline runner

    Returns:
        Merged MatchupEvIntegrityResult (worst-case merge across all checks).
    """
    checks = [
        check_matchup_sample_floor(
            sample_pa=row.get("matchup_sample_pa"),
            is_primary_driver=bool(row.get("matchup_is_primary_driver", False)),
        ),
        check_absence_of_data_neutrality(
            matchup_note=row.get("matchup_note"),
        ),
        check_ev_claim_audit(
            ev_claim=row.get("ev_claim"),
        ),
        check_variance_vs_safety(
            original_hit_prob_lb=row.get("original_hit_prob_lb"),
            replacement_hit_prob_lb=row.get("replacement_hit_prob_lb"),
            is_claimed_safer=bool(row.get("is_claimed_safer", False)),
        ),
        check_upstream_dependency_lock(
            upstream_step_name=row.get("upstream_step_name"),
            upstream_step_status=row.get("upstream_step_status"),
        ),
    ]

    merged = MatchupEvIntegrityResult()
    for c in checks:
        if not c.passed:
            merged.passed = False
        merged.blockers.extend(c.blockers)
        merged.warnings.extend(c.warnings)
        merged.detail.update(c.detail)
        if c.dropped:
            merged.dropped = True
        # Ceiling: WATCH is the only cap this module issues
        if c.label_ceiling == "WATCH":
            merged.label_ceiling = "WATCH"

    return merged
