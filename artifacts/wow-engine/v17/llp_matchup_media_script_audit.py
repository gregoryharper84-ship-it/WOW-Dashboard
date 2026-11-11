"""Read-only pregame media narrative and unweighted game-script audit.

This is a research-quality/coverage diagnostic. User-provided source metadata,
timestamps and receipt identifiers are checked for consistency, NOT authenticated
against a publisher or independent ledger here. No sporting probability, model
grade or winner is produced or changed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlsplit

CAN_EXECUTE = False
STANCES = frozenset({"SUPPORTS_PICK", "CHALLENGES_PICK", "CONTEXT"})
FORBIDDEN_WEIGHTS = frozenset({
    "probability", "win_probability", "scenario_probability",
    "weight", "scenario_weight", "calibrated_probability", "lower_bound",
})


def _has_uncertified_numeric_key(record: Mapping[str, Any]) -> bool:
    """Flag scenario likelihood/odds aliases, not ordinary descriptive fields."""
    for key in record:
        if not isinstance(key, str):
            return True
        name = key.casefold().replace("-", "_")
        if any(term in name for term in (
            "prob", "odds", "weight", "likelihood", "chance", "win_pct",
        )):
            return True
    return False


def _txt(value: Any, *, max_length: int = 500) -> str:
    if not isinstance(value, str) or len(value) > max_length:
        return ""
    value = value.strip()
    return value if value and not any(ord(c) < 32 or ord(c) == 127 for c in value) else ""


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return t.astimezone(timezone.utc) if t.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _https_url(value: Any) -> str:
    url = _txt(value, max_length=2000)
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if (parts.scheme.lower() != "https" or not parts.hostname or parts.username
            or parts.password or any(c.isspace() for c in url)):
        return ""
    return url


def audit_script_robustness_and_media(
    selection: Mapping[str, Any],
    *,
    selected_side: str,
    home_team: str,
    away_team: str,
    selected_at_utc: str,
) -> dict[str, Any]:
    """Describe timestamp-safe scenario sensitivity and media evidence only.

    Three or more distinct, unweighted scenarios are needed to compare script
    sensitivity. Their relative counts are NOT estimated event frequencies.
    Valid media headlines are evidence metadata, NOT proof a publisher was
    independently fetched and NOT verified sporting facts.
    """
    when = _moment(selected_at_utc)
    preferred = selected_side.casefold()
    opponents = {home_team.casefold(), away_team.casefold()} - {preferred}
    counterpart = next(iter(opponents)) if len(opponents) == 1 else None
    warnings: list[str] = []
    script_summary: dict[str, Any] = {
        "assessment": "NOT_EVALUATED",
        "scenario_count": 0,
        "favored_selected_count": 0,
        "favored_opponent_count": 0,
        "basis": "UNWEIGHTED_RESEARCH_SCENARIOS_ONLY",
        "probability": None,
    }
    game_script = selection.get("game_script")
    scenarios = game_script.get("scenarios") if isinstance(game_script, Mapping) else None
    if not isinstance(scenarios, (list, tuple)) or not scenarios:
        warnings.append("GAME_SCRIPT_ROBUSTNESS_NOT_EVALUATED")
    else:
        accepted: list[Mapping[str, Any]] = []
        ids: set[str] = set()
        paths: set[str] = set()
        if len(scenarios) > 8:
            warnings.append("SCENARIO_LIMIT_TRUNCATED")
        for candidate in scenarios[:8]:
            if not isinstance(candidate, Mapping):
                warnings.append("SCENARIO_MALFORMED")
                continue
            identity = _txt(candidate.get("scenario_id"), max_length=100)
            path = _txt(candidate.get("path"), max_length=600)
            side = _txt(candidate.get("favored_side"), max_length=100)
            time = _moment(candidate.get("as_of_utc"))
            swing = candidate.get("swing_factors")
            if (not identity or identity in ids or not path or path.casefold() in paths
                or side.casefold() not in {home_team.casefold(), away_team.casefold()}
                or not isinstance(swing, (list, tuple)) or not swing
                or any(not _txt(v, max_length=150) for v in swing)):
                warnings.append("SCENARIO_INVALID_OR_DUPLICATE")
                continue
            if time is None or when is None or time > when:
                warnings.append("SCENARIO_FUTURE_OR_TIME_UNVERIFIED")
                continue
            if FORBIDDEN_WEIGHTS.intersection(candidate) or _has_uncertified_numeric_key(candidate):
                warnings.append("SCENARIO_UNCERTIFIED_NUMERIC_WEIGHT")
                continue
            ids.add(identity)
            paths.add(path.casefold())
            accepted.append(candidate)
        for candidate in accepted:
            side = _txt(candidate.get("favored_side"), max_length=100).casefold()
            if side == preferred:
                script_summary["favored_selected_count"] += 1
            elif side == counterpart:
                script_summary["favored_opponent_count"] += 1
        script_summary["scenario_count"] = len(accepted)
        if (len(accepted) < 3 or not script_summary["favored_selected_count"]
                or not script_summary["favored_opponent_count"]):
            warnings.append("SCENARIO_COVERAGE_INCOMPLETE")
        elif script_summary["favored_selected_count"] > script_summary["favored_opponent_count"]:
            script_summary["assessment"] = "SELECTED_SIDE_FAVORED_IN_MORE_DOCUMENTED_SCRIPTS"
        else:
            script_summary["assessment"] = "SCRIPT_SENSITIVE_OR_CONTRADICTORY"
        if not script_summary["assessment"] == "NOT_EVALUATED" and any(
            w.startswith("SCENARIO_") for w in warnings
        ):
            script_summary["assessment"] = "PARTIAL_SCRIPT_EVIDENCE"

    media = selection.get("matchup_media")
    headlines: list[dict[str, str]] = []
    storylines: list[dict[str, Any]] = []
    counter: dict[str, Any] | None = None
    if not isinstance(media, Mapping):
        warnings.append("MATCHUP_MEDIA_NOT_REVIEWED")
    else:
        raw_headlines = media.get("headlines")
        valid_ids: set[str] = set()
        if not isinstance(raw_headlines, (list, tuple)) or not raw_headlines:
            warnings.append("MATCHUP_HEADLINES_MISSING")
        else:
            for item in raw_headlines[:8]:
                if not isinstance(item, Mapping):
                    warnings.append("MEDIA_HEADLINE_MALFORMED")
                    continue
                hid = _txt(item.get("headline_id"), max_length=100)
                publisher = _txt(item.get("publisher"), max_length=150)
                title = _txt(item.get("title"), max_length=240)
                url = _https_url(item.get("url"))
                source_receipt = _txt(item.get("source_receipt_id"), max_length=200)
                posted = _moment(item.get("published_at_utc"))
                visible = _moment(item.get("available_to_user_at_utc"))
                stance = _txt(item.get("stance"), max_length=50)
                if (not hid or hid in valid_ids or not publisher or not title or not url
                    or not source_receipt or stance not in STANCES):
                    warnings.append("MEDIA_HEADLINE_IDENTITY_OR_PROVENANCE_INVALID")
                    continue
                if (posted is None or visible is None or when is None
                    or posted > visible or visible > when):
                    warnings.append("MEDIA_HEADLINE_FUTURE_OR_TIME_UNVERIFIED")
                    continue
                valid_ids.add(hid)
                headlines.append({
                    "headline_id": hid, "publisher": publisher, "title": title,
                    "url": url, "source_receipt_id": source_receipt,
                    "stance": stance, "published_at_utc": item["published_at_utc"],
                })
            if len(raw_headlines) > 8:
                warnings.append("MEDIA_HEADLINES_LIMIT_TRUNCATED")
            if not headlines:
                warnings.append("NO_ADMISSIBLE_PREGAME_HEADLINES")
            elif len({h["publisher"].casefold() for h in headlines}) < 2:
                warnings.append("MEDIA_SOURCE_DIVERSITY_INSUFFICIENT")
        raw_storylines = media.get("storylines")
        if not isinstance(raw_storylines, (list, tuple)) or not raw_storylines:
            warnings.append("MATCHUP_STORYLINES_MISSING")
        else:
            for item in raw_storylines[:6]:
                if not isinstance(item, Mapping):
                    warnings.append("STORYLINE_INVALID_OR_UNSOURCED")
                    continue
                claim = _txt(item.get("claim"), max_length=500)
                stance = _txt(item.get("stance"), max_length=50)
                sources = item.get("headline_ids")
                if (not claim or stance not in STANCES
                    or not isinstance(sources, (list, tuple)) or not sources
                    or any(not isinstance(ref, str) or ref not in valid_ids for ref in sources)):
                    warnings.append("STORYLINE_INVALID_OR_UNSOURCED")
                    continue
                supporting_stances = {
                    h["stance"] for h in headlines if h["headline_id"] in sources
                }
                if stance != "CONTEXT" and stance not in supporting_stances:
                    warnings.append("STORYLINE_STANCE_NOT_SUPPORTED_BY_SOURCE")
                    continue
                storylines.append({"claim": claim, "stance": stance,
                                   "headline_ids": list(dict.fromkeys(sources))})
            if len(raw_storylines) > 6:
                warnings.append("MATCHUP_STORYLINES_LIMIT_TRUNCATED")
            if not storylines:
                warnings.append("NO_ADMISSIBLE_PREGAME_STORYLINES")
        raw_counter = media.get("counter_narrative")
        if isinstance(raw_counter, Mapping):
            claim = _txt(raw_counter.get("claim"), max_length=500)
            refs = raw_counter.get("headline_ids")
            challenging_ids = {
                headline["headline_id"] for headline in headlines
                if headline["stance"] == "CHALLENGES_PICK"
            }
            if (claim and isinstance(refs, (list, tuple)) and refs
                and all(isinstance(hid, str) and hid in valid_ids for hid in refs)
                and any(hid in challenging_ids for hid in refs)):
                counter = {"claim": claim, "headline_ids": list(dict.fromkeys(refs))}
        if counter is None:
            warnings.append("COUNTER_NARRATIVE_UNSOURCED")
        if headlines and not any(x["stance"] == "CHALLENGES_PICK" for x in headlines):
            warnings.append("NO_INDEPENDENT_CHALLENGING_HEADLINE")
    return {
        "script_robustness": script_summary,
        "media_headlines": headlines,
        "media_storylines": storylines,
        "media_counter_narrative": counter,
        "media_evidence_status": "STRUCTURAL_PREGAME_PROVENANCE_ONLY",
        "research_warnings": list(dict.fromkeys(warnings)),
        "sporting_probability_computed": False,
        "fitted_model_modified": False,
        "can_execute": CAN_EXECUTE,
    }
