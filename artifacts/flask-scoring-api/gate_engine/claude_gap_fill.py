"""
gate_engine/claude_gap_fill.py

Server-side Claude gap-fill for the /analyze-and-score pipeline.

Called per leg when:
  - resolution_status != "resolved"  (player ID couldn't be resolved)
  - data_gaps is non-empty           (market, injury, game_log missing)

Hard rule (enforced in the prompt): Claude reports what it finds or says
"unavailable". It never fabricates a stat value. An unresolvable leg
blocks that leg from scoring — fail-safe, not fail-silent.

Two public functions:
  resolve_gaps(gap_requests)  — resolves player ID / injury / schedule gaps
  estimate_hit_probability()  — Claude Poisson/logistic for unsupported sports
                                 (called by hit_probability.py, Task 4)
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

_GAP_FILL_MODEL = "claude-opus-4-7"
_MAX_TOKENS     = 1024


# ---------------------------------------------------------------------------
# Anthropic client (lazy, same pattern as app.py)
# ---------------------------------------------------------------------------

_anthropic_client = None


def _get_client():
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client
    try:
        import anthropic
        api_key = (
            os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY") or
            os.environ.get("ANTHROPIC_API_KEY", "")
        )
        base_url = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        _anthropic_client = anthropic.Anthropic(**kwargs)
        return _anthropic_client
    except ImportError:
        raise RuntimeError("anthropic package not installed")


# ---------------------------------------------------------------------------
# Gap-fill: player resolution + injury + schedule
# ---------------------------------------------------------------------------

_GAP_FILL_PROMPT = """\
You are a sports data research assistant helping resolve incomplete player/game data.

You have been given a list of data gaps for a player prop bet. For each gap, search \
your knowledge and report what you find. If you cannot find reliable information, say \
"unavailable" — do not guess or fabricate.

HARD RULE: You must NOT estimate or invent any statistical value (game log numbers, \
averages, hit rates). Report only factual data you are confident about. An "unavailable" \
answer is always better than a fabricated one.

For each gap in the request, resolve it if possible and return a JSON object.

Player: {player_name}
Sport: {sport}
Gaps to resolve: {gaps}

Return a JSON object with this exact structure:
{{
  "resolved": {{
    "player_id": "<canonical ID if known, else null>",
    "player_name_canonical": "<correct full name if OCR was wrong, else null>",
    "team": "<current team abbreviation if known, else null>",
    "injury_status": "<active|questionable|out|probable|unknown>",
    "injury_source": "<URL or publication if known, else null>",
    "game_confirmed": <true|false|null>,
    "opponent": "<opponent team abbreviation if known, else null>",
    "notes": "<one sentence of relevant context, or null>"
  }},
  "still_missing": ["<gap_name>", ...],
  "confidence": "<low|medium|high>",
  "sources": ["<URL or publication name>", ...]
}}

Only include fields in "resolved" that you actually found information for. \
Use null for any field you could not confirm. \
List in "still_missing" any gaps you could not resolve.
"""


def resolve_gaps(gap_requests: list[dict]) -> list[dict]:
    """
    Resolve player/injury/schedule gaps for multiple legs.

    Each gap_request:
      { leg_id, player_name, sport, gaps: ["injury_status", "player_id_resolution", ...] }

    Returns list of:
      {
        leg_id, resolved: {...}, still_missing: [...],
        confidence: "low|medium|high", sources: [...], error: str|None
      }
    """
    if not gap_requests:
        return []

    try:
        client = _get_client()
    except RuntimeError as exc:
        return [_error_result(r["leg_id"], str(exc)) for r in gap_requests]

    results = []
    for req in gap_requests:
        leg_id      = req.get("leg_id", "")
        player_name = req.get("player_name", "")
        sport       = req.get("sport", "")
        gaps        = req.get("gaps", [])

        if not gaps:
            results.append({
                "leg_id":       leg_id,
                "resolved":     {},
                "still_missing": [],
                "confidence":   "high",
                "sources":      [],
                "error":        None,
            })
            continue

        prompt = _GAP_FILL_PROMPT.format(
            player_name=player_name,
            sport=sport,
            gaps=", ".join(gaps),
        )

        try:
            message = client.messages.create(
                model=_GAP_FILL_MODEL,
                max_tokens=_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text.strip() if message.content else "{}"
            parsed = _parse_json(raw)
            results.append({
                "leg_id":        leg_id,
                "resolved":      parsed.get("resolved", {}),
                "still_missing": parsed.get("still_missing", gaps),
                "confidence":    parsed.get("confidence", "low"),
                "sources":       parsed.get("sources", []),
                "error":         None,
            })
        except Exception as exc:
            logger.warning("claude_gap_fill.resolve_gaps: leg_id=%s error=%s", leg_id, exc)
            results.append(_error_result(leg_id, str(exc)))

    return results


# ---------------------------------------------------------------------------
# Probability estimation (NBA/WNBA/other counting stats)
# ---------------------------------------------------------------------------

_PROB_PROMPT = """\
You are a sports analytics assistant computing hit probability for a player prop bet.

Player: {player_name}
Sport: {sport}
Prop: {prop} {side} {line}
Last {n_games} games: {game_log}
Season average (λ): {season_avg}
Market no-vig probability: {no_vig_prob}
Injury status: {injury_status}

Task: compute P(hit) for this prop — the probability the player CLEARS the line.

For counting stats (points, rebounds, assists, strikeouts): use a Poisson model:
  P(X ≥ line) = 1 - CDF(floor(line - 1), λ)
  where λ = season average (or L{n_games} average if season avg unavailable).

For binary / low-line props: use logistic adjustment:
  start from no_vig_prob, adjust ±0.03 per full L5 hit-rate deviation from 0.50.

Show your work briefly (which method, what λ or inputs you used).
Report any significant caveats (small sample, injury risk, etc.).

Return a JSON object:
{{
  "hit_probability": <float 0.0-1.0>,
  "model_used": "<poisson_l{n_games}|logistic_no_vig|insufficient_data>",
  "lambda_used": <float|null>,
  "calibration_note": "<one sentence>",
  "work": "<brief calculation shown>"
}}
"""


def estimate_hit_probability(
    player_name: str,
    sport: str,
    prop_type: str,
    side: str,
    line: float,
    game_log: list[float],
    no_vig_prob: Optional[float],
    injury_status: str = "active",
) -> dict:
    """
    Compute hit probability for NBA/WNBA/other props where no Python formula
    exists yet. Uses Claude with Poisson or logistic model as appropriate.

    Returns:
      {
        hit_probability: float | None,
        model_used: str,
        calibration_note: str,
        error: str | None,
      }
    """
    if not game_log:
        return {
            "hit_probability":  None,
            "model_used":       "insufficient_data",
            "calibration_note": "No game log available — cannot compute probability",
            "error":            None,
        }

    n = len(game_log)
    season_avg = round(sum(game_log) / n, 2) if game_log else None

    prompt = _PROB_PROMPT.format(
        player_name=player_name,
        sport=sport,
        prop=prop_type,
        side=side,
        line=line,
        game_log=game_log[:10],
        n_games=min(n, 10),
        season_avg=season_avg,
        no_vig_prob=no_vig_prob if no_vig_prob is not None else "unavailable",
        injury_status=injury_status,
    )

    try:
        client = _get_client()
        message = client.messages.create(
            model=_GAP_FILL_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw    = message.content[0].text.strip() if message.content else "{}"
        parsed = _parse_json(raw)

        prob = parsed.get("hit_probability")
        if prob is not None:
            try:
                prob = float(prob)
                prob = max(0.0, min(1.0, prob))
            except (ValueError, TypeError):
                prob = None

        return {
            "hit_probability":  round(prob, 4) if prob is not None else None,
            "model_used":       parsed.get("model_used", "claude_estimate"),
            "calibration_note": parsed.get("calibration_note", ""),
            "error":            None,
        }

    except Exception as exc:
        logger.warning("claude_gap_fill.estimate_hit_probability: %s", exc)
        return {
            "hit_probability":  None,
            "model_used":       "claude_error",
            "calibration_note": "Claude probability estimate failed",
            "error":            str(exc),
        }


# ---------------------------------------------------------------------------
# Explanation generation (plain-English rationale per leg)
# ---------------------------------------------------------------------------

_EXPLAIN_PROMPT = """\
You are a sports betting analyst writing a brief rationale for a prop bet evaluation.

Player: {player_name}
Prop: {prop} {side} {line} ({platform})
Terminal label: {terminal_label}
Confidence tier: {confidence_tier}
Edge score: {edge_score}
L10 hit rate: {l10_hit_rate}
Key gate results: {gate_summary}
Flags: {flags}

Write a 2-3 sentence plain-English explanation of why this prop received this \
evaluation. Reference the specific data points (hit rate, edge, gate results) \
that drove the decision. Use accessible language — no internal jargon or \
code names. Do not make a betting recommendation.

Return only the explanation text, no JSON wrapper.
"""


def generate_explanation(
    player_name: str,
    prop_type: str,
    side: str,
    line: float,
    platform: str,
    terminal_label: str,
    confidence_tier: str,
    edge_score: Optional[float],
    l10_hit_rate: Optional[str],
    gate_summary: list[dict],
    flags: list[str],
) -> str:
    """Generate a 2-3 sentence plain-English rationale for a leg's score."""
    # Summarise gate results to a compact string for the prompt
    gate_lines = [
        f"{g['gate']}: {g['result']}"
        for g in (gate_summary or [])[:6]
    ]
    gate_str = "; ".join(gate_lines) if gate_lines else "not available"

    prompt = _EXPLAIN_PROMPT.format(
        player_name=player_name,
        prop=prop_type,
        side=side,
        line=line,
        platform=platform,
        terminal_label=terminal_label,
        confidence_tier=confidence_tier,
        edge_score=edge_score if edge_score is not None else "N/A",
        l10_hit_rate=l10_hit_rate or "N/A",
        gate_summary=gate_str,
        flags=", ".join(flags) if flags else "none",
    )

    try:
        client = _get_client()
        message = client.messages.create(
            model=_GAP_FILL_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip() if message.content else ""
    except Exception as exc:
        logger.warning("claude_gap_fill.generate_explanation: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error_result(leg_id: str, error: str) -> dict:
    return {
        "leg_id":        leg_id,
        "resolved":      {},
        "still_missing": [],
        "confidence":    "low",
        "sources":       [],
        "error":         error,
    }


def _parse_json(text: str) -> dict:
    import json, re
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
