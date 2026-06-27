"""
contract_intake.py  —  Claude-based contract rule audit
WOW v16 Kalshi Exchange Layer

Claude is the AUDIT assistant — not the execution engine.
Tasks delegated to Claude:
  1. Contract rule audit (wording / settlement / ambiguity)
  2. Market bucket classification recommendation
  3. Failure-path generation (YES fails, NO fails, hidden traps)
  4. Test fixture generation
  5. Post-trade review summary

HARD RULE:
  Claude output is advisory only. Terminal labels must be confirmed by
  edge_engine.evaluate() with live orderbook data.
  Claude cannot produce: KALSHI_FINAL_APPROVED or KALSHI_PLAYABLE_LIMIT_ONLY.
  It can only return: WATCH / SCOUT / REJECT labels or "NEEDS_PRICE_ANALYSIS".
"""
from __future__ import annotations

import os
import json
from typing import Any

# ---------------------------------------------------------------------------
# System prompt for the contract audit assistant
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are auditing Kalshi prediction-market contracts for WOW v16 Clean Core.
Use the active WOW-PATCH-2026-06-27-KALSHI-EXCHANGE-LAYER.

For each contract, return a JSON object with exactly these fields:
{
  "contract_wording":        string,
  "settlement_source":       string | null,
  "resolution_clarity_grade": "A" | "B" | "C" | "D" | "F",
  "ambiguity_risk":          "LOW" | "MEDIUM" | "HIGH",
  "data_availability":       "AVAILABLE" | "PARTIAL" | "UNAVAILABLE",
  "market_bucket":           "TRUSTED_TEST" | "WATCH" | "TEST_ONLY" | "SCOUT" | "REJECT",
  "failure_paths_yes":       [string],
  "failure_paths_no":        [string],
  "tradable_before_price":   boolean,
  "required_data":           [string],
  "terminal_label":          one of EXACTLY: KALSHI_WATCH | KALSHI_SCOUT | KALSHI_REJECT_BAD_RULES | KALSHI_REJECT_UNCALIBRATED | KALSHI_DATA_UNOBTAINABLE | NEEDS_PRICE_ANALYSIS,
  "reasoning":               string
}

Rules:
- You may NOT output KALSHI_FINAL_APPROVED or KALSHI_PLAYABLE_LIMIT_ONLY (those require live price/orderbook)
- Do not recommend trades or say "likely" without probability
- Do not approve anything without price, order book, fees, spread, and liquidity
- If resolution source is missing or unclear → KALSHI_REJECT_BAD_RULES
- If model probability cannot be assigned yet → KALSHI_REJECT_UNCALIBRATED
- Output valid JSON only — no markdown, no prose"""


def audit_contract(
    title:                str,
    settlement_condition: str | None = None,
    resolution_source:    str | None = None,
    category:             str        = "other",
    ticker:               str        = "",
    extra_context:        str        = "",
) -> dict[str, Any]:
    """
    Send a contract to Claude for a rule audit.

    Returns a structured audit result. Falls back to a LOCAL rule-based
    result if ANTHROPIC_API_KEY is not set or the call fails.
    """
    from . import settlement_risk as _sr

    # Try Claude first
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        try:
            return _claude_audit(
                api_key            = api_key,
                title              = title,
                settlement_condition = settlement_condition,
                resolution_source  = resolution_source,
                category           = category,
                ticker             = ticker,
                extra_context      = extra_context,
            )
        except Exception as exc:
            # Graceful fallback to local rule-based audit
            return {
                **_local_audit(title, settlement_condition, resolution_source, category, ticker),
                "claude_error": str(exc),
                "source": "local_fallback",
            }

    # No API key — use local rule-based audit
    return {
        **_local_audit(title, settlement_condition, resolution_source, category, ticker),
        "source": "local_rules",
    }


def _claude_audit(
    api_key:              str,
    title:                str,
    settlement_condition: str | None,
    resolution_source:    str | None,
    category:             str,
    ticker:               str,
    extra_context:        str,
) -> dict[str, Any]:
    """Call Claude claude-3-5-sonnet for the contract audit."""
    import anthropic  # type: ignore

    client = anthropic.Anthropic(api_key=api_key)

    user_content = f"""Audit this Kalshi contract:

Ticker: {ticker or 'N/A'}
Title: {title}
Category: {category}
Settlement condition: {settlement_condition or 'Not provided'}
Resolution source: {resolution_source or 'Not provided'}
{f'Additional context: {extra_context}' if extra_context else ''}

Return JSON only."""

    msg = client.messages.create(
        model      = "claude-3-5-sonnet-20241022",
        max_tokens = 1024,
        system     = _SYSTEM_PROMPT,
        messages   = [{"role": "user", "content": user_content}],
    )

    raw = msg.content[0].text.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    result = json.loads(raw)
    result["source"]          = "claude"
    result["can_approve_bets"] = False
    result["ticker"]          = ticker
    return result


def _local_audit(
    title:                str,
    settlement_condition: str | None,
    resolution_source:    str | None,
    category:             str,
    ticker:               str,
) -> dict[str, Any]:
    """
    Rule-based fallback audit (no Claude).
    Less nuanced than Claude but deterministic.
    """
    from . import settlement_risk as _sr

    grade_result = _sr.grade_contract(
        title                = title,
        settlement_condition = settlement_condition,
        resolution_source    = resolution_source,
        category             = category,
        contract_ticker      = ticker,
    )

    risk  = grade_result["settlement_risk"]
    grade = grade_result["resolution_clarity_grade"]

    if risk == "REJECT":
        terminal = "KALSHI_REJECT_BAD_RULES"
    elif risk == "HIGH":
        terminal = "KALSHI_SCOUT"
    elif grade in ("A", "B", "C"):
        terminal = "NEEDS_PRICE_ANALYSIS"
    else:
        terminal = "KALSHI_SCOUT"

    return {
        "ticker":                  ticker,
        "contract_wording":        title,
        "settlement_source":       resolution_source,
        "resolution_clarity_grade": grade,
        "ambiguity_risk":          "HIGH" if risk == "REJECT" else ("MEDIUM" if risk == "HIGH" else "LOW"),
        "data_availability":       "PARTIAL" if not resolution_source else "AVAILABLE",
        "market_bucket":           "REJECT" if risk == "REJECT" else "SCOUT",
        "failure_paths_yes":       grade_result["failure_paths_yes"],
        "failure_paths_no":        grade_result["failure_paths_no"],
        "tradable_before_price":   risk not in ("REJECT", "HIGH"),
        "required_data":           ["model probability", "live orderbook", "settlement confirmation"],
        "terminal_label":          terminal,
        "reasoning":               grade_result["detail"],
        "can_approve_bets":        False,
    }


def post_trade_review(
    ticker:              str,
    model_probability:   float,
    entry_price:         float,
    closing_price:       float | None,
    result:              str | None,   # "YES" / "NO" / "VOID"
    adjusted_edge:       float,
    clv:                 float | None,
    notes:               str = "",
) -> dict[str, Any]:
    """
    Ask Claude to summarize a post-trade review for a settled contract.
    Falls back to local summary if Claude is unavailable.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    summary_prompt = f"""Post-trade review for Kalshi contract {ticker}:
- Model probability: {model_probability:.3f}
- Entry price: {entry_price:.3f}
- Closing price: {closing_price if closing_price is not None else 'N/A'}
- Result: {result or 'PENDING'}
- Adjusted edge at entry: {adjusted_edge:.3f}
- CLV (closing line value): {clv if clv is not None else 'N/A'}
- Notes: {notes or 'None'}

Answer these questions in JSON:
{{
  "calibrated": boolean,
  "clv_positive": boolean | null,
  "market_confirmed_thesis": boolean | null,
  "loss_type": "VARIANCE" | "BAD_DATA" | "BAD_PRICE" | "BAD_RULES" | "N/A",
  "bucket_upgrade_recommendation": "UPGRADE" | "HOLD" | "DOWNGRADE",
  "summary": string
}}"""

    if api_key:
        try:
            import anthropic  # type: ignore
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model      = "claude-3-5-sonnet-20241022",
                max_tokens = 512,
                messages   = [{"role": "user", "content": summary_prompt}],
            )
            raw = msg.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            result_data = json.loads(raw)
            result_data["source"] = "claude"
            result_data["can_approve_bets"] = False
            return result_data
        except Exception as exc:
            pass

    # Local fallback
    calibrated    = abs(model_probability - (closing_price or model_probability)) < 0.10
    clv_positive  = (clv or 0) > 0 if clv is not None else None
    return {
        "calibrated":                    calibrated,
        "clv_positive":                  clv_positive,
        "market_confirmed_thesis":       None,
        "loss_type":                     "N/A",
        "bucket_upgrade_recommendation": "HOLD",
        "summary":                       "Claude unavailable — local review only.",
        "source":                        "local_fallback",
        "can_approve_bets":              False,
    }
