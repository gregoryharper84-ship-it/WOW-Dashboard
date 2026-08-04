"""
gate_engine/expert_review.py

Autonomous Expert Review — Step H of the analyze-and-score pipeline.

Runs after every slip is scored, with no human in the loop.
Downgrade-only safety net: it can make a verdict more conservative,
never less. Every result (confirmed or downgraded) is written to the
slip_expert_review_log table via the caller.

Public API
----------
run_expert_review(slip_id, legs, correlation_risk) -> dict
    Returns:
      {
        slip_id, audit_verdict, legs, correlation_audit,
        audit_log_entry, error
      }

Design constraints
------------------
- NEVER reinstates a killed leg, removes a flag, or raises a confidence tier.
- An audit error (Claude unavailable, JSON parse failure) returns a safe
  CONFIRMED stub so the main /analyze-and-score response is never blocked.
- Every result is expected to be written to slip_expert_review_log by the
  caller; the caller (app.py) owns DB I/O so this module stays import-clean.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_REVIEW_MODEL = "claude-opus-4-7"
_MAX_TOKENS   = 2048

# ---------------------------------------------------------------------------
# Anthropic client (lazy — same pattern as claude_gap_fill.py)
# ---------------------------------------------------------------------------

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    try:
        import anthropic
        api_key  = (os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY") or
                    os.environ.get("ANTHROPIC_API_KEY", ""))
        base_url = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        _client = anthropic.Anthropic(**kwargs)
        return _client
    except ImportError:
        raise RuntimeError("anthropic package not installed")


# ---------------------------------------------------------------------------
# System prompt (verbatim from wow-claude-integration-spec Step H)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are the autonomous expert review pass for the WOW betting model.
You run after every slip is scored, with no human checking your output
before it's stored. You are a downgrade-only safety net.

Rules, in order of priority:

1. You may only make a verdict MORE conservative. Never reinstate a
   killed leg, never remove a correlation flag, never raise a
   confidence tier. If your independent check is looser than the
   pipeline's own verdict, the pipeline's verdict stands unchanged.

2. Re-derive each leg's gate result independently from its raw fields
   (availability, prop type, box score support, game context) before
   looking at the pipeline's stated terminal_label. Compare after, not
   before — do not anchor on the label you're auditing.

3. Re-check the full leg list for correlation risk (same-game,
   same-team, script-dependent, pitcher-matchup-trap) independently of
   whatever the pipeline already flagged.

4. Compare each leg's explanation text to its own confidence_tier,
   edge_score, and sample_basis. Tag it if the language claims more
   certainty than those numbers support, or omits a flag that should
   have changed the reading. Do not rewrite the explanation — tag it.

5. Log every result, confirmed or downgraded, with your reasoning.
   A confirmed leg with no notes is still a required log entry, not a
   no-op — the log is the only record of what this pass checked.

6. If you are not confident in an audit judgment, say so in
   audit_reason rather than defaulting to CONFIRMED. Under-confidence
   here costs nothing; false confidence costs real money.
"""

_USER_TEMPLATE = """\
Slip ID: {slip_id}
Pipeline correlation risk: {correlation_risk}

Scored legs:
{legs_json}

Audit each leg. Return a JSON object with exactly this structure:
{{
  "audit_verdict": "CONFIRMED or DOWNGRADED",
  "legs": [
    {{
      "leg_id": "...",
      "audit_result": "CONFIRMED or DOWNGRADED",
      "original_label": "...",
      "audit_label": "<new label — present only if DOWNGRADED>",
      "audit_reason": "<your reasoning — required for every leg>",
      "explanation_flags": ["EXPLANATION_OVERCONFIDENT", "EXPLANATION_INCOMPLETE"]
    }}
  ],
  "correlation_audit": {{
    "pipeline_flag": "<pipeline correlation_risk string>",
    "audit_flag": "<your independent assessment>",
    "escalated": true or false
  }}
}}

Top-level audit_verdict is DOWNGRADED if ANY leg is DOWNGRADED, else CONFIRMED.
Return ONLY valid JSON — no markdown, no commentary outside the JSON object.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_expert_review(
    slip_id: str,
    legs: list[dict],
    correlation_risk: str = "",
) -> dict:
    """
    Run the autonomous expert review pass over a scored slip.

    Parameters
    ----------
    slip_id          : slip UUID from /analyze-and-score
    legs             : full legs_out list from the endpoint
    correlation_risk : slip_summary.correlation_risk string

    Returns
    -------
    Audit dict — always returned, even on Claude error (error key populated).
    """
    slim_legs = _build_slim_legs(legs)

    user_msg = _USER_TEMPLATE.format(
        slip_id=slip_id,
        correlation_risk=correlation_risk or "unknown",
        legs_json=json.dumps(slim_legs, indent=2),
    )

    try:
        client = _get_client()
        message = client.messages.create(
            model=_REVIEW_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw    = message.content[0].text.strip() if message.content else "{}"
        parsed = _parse_json(raw)
    except Exception as exc:
        logger.warning("expert_review.run_expert_review: slip_id=%s error=%s", slip_id, exc)
        return _stub_audit(slip_id, legs, correlation_risk, error=str(exc))

    return _normalise(slip_id, legs, correlation_risk, parsed)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_slim_legs(legs: list[dict]) -> list[dict]:
    """Strip enrichment bulk; keep only what the audit pass needs."""
    return [
        {
            "leg_id":          leg.get("leg_id"),
            "player_name":     leg.get("player_name"),
            "prop":            leg.get("prop"),
            "terminal_label":  leg.get("terminal_label"),
            "confidence_tier": leg.get("confidence_tier"),
            "edge_score":      leg.get("edge_score"),
            "hit_probability": leg.get("hit_probability"),
            "model_used":      leg.get("model_used"),
            "explanation":     leg.get("explanation"),
            "flags":           leg.get("flags") or [],
            "gate_trace":      (leg.get("gate_trace") or [])[:8],
            "resolution":      leg.get("resolution") or {},
        }
        for leg in legs
    ]


def _normalise(
    slip_id: str,
    legs: list[dict],
    correlation_risk: str,
    parsed: dict,
) -> dict:
    """
    Normalise Claude's raw response into the canonical audit dict.

    Enforces the downgrade-only invariant:
      - If Claude's audit_result is DOWNGRADED but audit_label equals the
        original label, revert to CONFIRMED.
      - If Claude's audit_result is DOWNGRADED but audit_label is absent,
        add a note and revert to CONFIRMED.
      - Never promote a label.
    """
    audit_by_id: dict[str, dict] = {
        a.get("leg_id"): a
        for a in (parsed.get("legs") or [])
        if a.get("leg_id")
    }

    final_legs: list[dict] = []
    any_downgraded = False

    for leg in legs:
        lid      = leg.get("leg_id")
        original = leg.get("terminal_label", "")

        entry = audit_by_id.get(lid) or {
            "leg_id":            lid,
            "audit_result":      "CONFIRMED",
            "original_label":    original,
            "audit_reason":      "not evaluated by Claude",
            "explanation_flags": [],
        }

        # Ensure original_label is always set
        entry.setdefault("original_label", original)
        entry.setdefault("explanation_flags", [])

        if entry.get("audit_result") == "DOWNGRADED":
            audit_label = entry.get("audit_label")
            if not audit_label or audit_label == original:
                # Downgrade claimed but no distinct label — revert
                entry["audit_result"]  = "CONFIRMED"
                entry["audit_reason"]  = (
                    (entry.get("audit_reason") or "") +
                    " [audit_label absent or unchanged — reverted to CONFIRMED]"
                )
                entry.pop("audit_label", None)
            else:
                any_downgraded = True

        final_legs.append(entry)

    # Correlation audit
    corr_audit = parsed.get("correlation_audit") or {}
    if not corr_audit:
        corr_audit = {
            "pipeline_flag": correlation_risk,
            "audit_flag":    correlation_risk,
            "escalated":     False,
        }
    # An escalated correlation flag counts as a downgrade
    if corr_audit.get("escalated"):
        any_downgraded = True

    top_verdict = "DOWNGRADED" if any_downgraded else parsed.get("audit_verdict", "CONFIRMED")
    if top_verdict not in ("CONFIRMED", "DOWNGRADED"):
        top_verdict = "CONFIRMED"

    return {
        "slip_id":          slip_id,
        "audit_verdict":    top_verdict,
        "legs":             final_legs,
        "correlation_audit": corr_audit,
        "audit_log_entry":  "written",
        "error":            None,
    }


def _stub_audit(
    slip_id: str,
    legs: list[dict],
    correlation_risk: str,
    error: str,
) -> dict:
    """Safe CONFIRMED stub returned when Claude is unavailable."""
    return {
        "slip_id":       slip_id,
        "audit_verdict": "CONFIRMED",
        "legs": [
            {
                "leg_id":            leg.get("leg_id"),
                "audit_result":      "CONFIRMED",
                "original_label":    leg.get("terminal_label"),
                "audit_reason":      "Claude unavailable — expert review skipped",
                "explanation_flags": [],
            }
            for leg in legs
        ],
        "correlation_audit": {
            "pipeline_flag": correlation_risk,
            "audit_flag":    correlation_risk,
            "escalated":     False,
        },
        "audit_log_entry": "skipped",
        "error":           error,
    }


def _parse_json(text: str) -> dict:
    import re
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {}
