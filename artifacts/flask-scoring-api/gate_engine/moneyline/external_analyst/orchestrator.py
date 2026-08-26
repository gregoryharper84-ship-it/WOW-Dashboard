"""
gate_engine/moneyline/external_analyst/orchestrator.py
WOW-PATCH-2026-08-08-EXTERNAL-ANALYST-INTELLIGENCE

Main entry point for the External Analyst Intelligence layer.

Orchestrates:
  1. All registered source adapters (StumpTheSpread, + future)
  2. Syndication deduplication (family_resolver)
  3. Contradiction analysis (contradiction_engine)
  4. Ledger recording (best-effort, never breaks the model)
  5. Returns AnalystIntelligenceResult for storage in MoneylineResult layer

Governance invariants enforced here:
  - direct_probability_weight = 0.0 on every opinion and on the result
  - Analyst picks never enter failure_path_matrix directly
  - Unverified claims remain in thesis_tags.unverified_narrative only
  - Source failures → DATA_UNOBTAINABLE, base model continues
  - All exceptions swallowed — layer is optional enrichment only

can_execute=False unconditional.
"""
from __future__ import annotations

from typing import Any

from gate_engine.moneyline.external_analyst.types import (
    AnalystIntelligenceResult,
    AnalystOpinion,
    AnalystSourceStatus,
)
from gate_engine.moneyline.external_analyst.family_resolver import deduplicate_opinions
from gate_engine.moneyline.external_analyst.contradiction_engine import run_contradiction_analysis
from gate_engine.moneyline.external_analyst.sources.stumps_the_spread import (
    StumpsTheSpreadAdapter,
)

can_execute: bool = False  # UNCONDITIONAL

# ---------------------------------------------------------------------------
# Registered source adapters
# Add new sources here when they are implemented.
# ---------------------------------------------------------------------------

_SOURCES = [
    StumpsTheSpreadAdapter(),
    # PickDawgzAdapter(),   # future
    # DocsSportsAdapter(),  # future
]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_external_analyst_intelligence(
    row:                  dict[str, Any],
    enrichment:           dict[str, Any],
    sport:                str,
    team:                 str,
    opponent:             str,
    wow_side:             str | None,
    wow_independent_prob: float | None   = None,
    wow_calibrated_lb:    float | None   = None,
    market_no_vig:        float | None   = None,
) -> AnalystIntelligenceResult:
    """
    Run the full External Analyst Intelligence layer for one moneyline row.

    Parameters
    ----------
    row                  : The candidate row (sport, team, opponent, event_id, etc.)
    enrichment           : Full enrichment dict (may include pre-supplied picks)
    sport                : e.g. "MLB", "NBA"
    team                 : Candidate team name (our side)
    opponent             : Opponent team name
    wow_side             : WOW core model's favored side ("home" | "away")
    wow_independent_prob : WOW independent probability (for ledger capture)
    wow_calibrated_lb    : WOW calibrated lower bound (for ledger capture)
    market_no_vig        : Market no-vig probability at capture (for ledger)

    Returns AnalystIntelligenceResult with full observability.
    NEVER raises — all failures produce DATA_UNOBTAINABLE opinions.
    """
    result = AnalystIntelligenceResult()
    result.direct_probability_weight = 0.0   # ALWAYS
    notes: list[str] = []

    event_date = (
        row.get("event_date") or row.get("slate_date") or
        enrichment.get("event_date") or None
    )
    event_id = row.get("event_id")

    # ── 1. Collect from all registered sources ──────────────────────────────
    all_opinions: list[AnalystOpinion] = []

    for source in _SOURCES:
        result.sources_consulted.append(source.source_name)
        try:
            opinions = source.fetch(
                sport      = sport,
                team       = team,
                opponent   = opponent,
                event_date = event_date,
                enrichment = enrichment,
            )
            # Enforce governance: direct_probability_weight must be 0.0
            for op in opinions:
                op.direct_probability_weight = 0.0
                if event_id and not op.event_id:
                    op.event_id = event_id
            all_opinions.extend(opinions)

            # Track failed sources
            failed = [
                op for op in opinions
                if op.source_status == AnalystSourceStatus.DATA_UNOBTAINABLE
            ]
            if failed and len(failed) == len(opinions):
                result.sources_failed.append(source.source_name)

        except Exception as exc:
            # Source failure is non-fatal
            result.sources_failed.append(source.source_name)
            notes.append(f"SOURCE_ERROR:{source.source_name}:{exc!s:.80}")

    result.opinions = all_opinions

    # ── 2. Deduplicate by source/analyst family ─────────────────────────────
    independent, all_opinions_tagged = deduplicate_opinions(all_opinions)
    result.opinions             = all_opinions_tagged
    result.independent_opinions = independent
    notes.append(
        f"deduplication: total={len(all_opinions_tagged)} "
        f"independent={len(independent)} "
        f"syndicated={len(all_opinions_tagged)-len(independent)}"
    )

    # ── 3. Contradiction analysis ───────────────────────────────────────────
    result.contradiction_report = run_contradiction_analysis(
        independent_opinions = independent,
        wow_side             = wow_side,
        wow_independent_prob = wow_independent_prob,
    )

    # ── 4. Verified factual claims (empty at this stage — require independent
    #       verification through stronger sources before model entry) ─────────
    # The analyst claims are in thesis_tags.unverified_narrative.
    # Operators who independently verify a claim should pass it via
    # enrichment["failure_path_matrix"] — the analyst layer does NOT inject
    # anything into failure_path_matrix directly.
    result.verified_factual_claims = []
    notes.append(
        "verified_factual_claims=[] — analyst claims require independent "
        "verification before entering failure_path_matrix or sport model"
    )

    # ── 5. Ledger recording (best-effort) ───────────────────────────────────
    try:
        from gate_engine.moneyline.external_analyst.ledger import log_analyst_opinion
        for op in independent:
            if op.source_status == AnalystSourceStatus.RETRIEVED:
                log_analyst_opinion(
                    opinion              = op,
                    wow_side             = wow_side,
                    wow_independent_prob = wow_independent_prob,
                    wow_calibrated_lb    = wow_calibrated_lb,
                    market_no_vig        = market_no_vig,
                )
    except Exception as exc:
        notes.append(f"LEDGER_WRITE_ERROR:{exc!s:.60}")

    result.acquisition_notes = notes
    return result
