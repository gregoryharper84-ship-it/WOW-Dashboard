"""Governed read-only prior Scout dossier hydration.

Nightly Scout uses this boundary to compare the current evidence dossier with
the latest persisted dossier for the same stable candidate. The request carries
compact identity only; no database credential or model authority is exposed.
Lookup failure is explicit and never blocks current evidence discovery.
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from v17.github_actions_oidc_client import GitHubOIDCMintError, mint_github_actions_oidc

DEFAULT_URL = "https://iczfhsmjrrafhvcpmqhr.supabase.co/functions/v1/wow-v17-scout-brain-persist"
MAX_CANDIDATES = 500


class PriorDossierReadError(RuntimeError):
    """Typed failure for prior-dossier lookup without weakening current Scout."""


def prior_url() -> str:
    return str(os.environ.get("WOW_SCOUT_PRIOR_DOSSIER_URL") or DEFAULT_URL).strip()


def _market_identity(candidate: dict[str, Any]) -> dict[str, Any]:
    evidence = candidate.get("market_evidence")
    row = evidence if isinstance(evidence, dict) else {}
    return {
        key: row.get(key)
        for key in ("market_key", "description", "outcome_name", "point")
        if row.get(key) is not None
    }


def compact_previous_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    model = payload.get("model_handoff") if isinstance(payload.get("model_handoff"), dict) else {}
    rows: list[dict[str, Any]] = []
    for lane in ("team_event_candidates", "prop_candidates"):
        for index, candidate in enumerate(model.get(lane, []) or [], 1):
            if not isinstance(candidate, dict):
                continue
            compact = {
                "lane": lane,
                "source_index": index,
                "sport_key": candidate.get("sport_key"),
                "official_event_id": candidate.get("official_event_id"),
                "route": candidate.get("route") or candidate.get("controlling_specialist_route"),
            }
            if lane == "prop_candidates":
                compact["market_evidence"] = _market_identity(candidate)
            rows.append(compact)
    if len(rows) > MAX_CANDIDATES:
        raise PriorDossierReadError("PRIOR_DOSSIER_REQUEST_TOO_LARGE")
    return rows


def _post(url: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        token = mint_github_actions_oidc()
    except GitHubOIDCMintError as exc:
        raise PriorDossierReadError(str(exc)) from exc
    request = Request(
        url,
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            body_out = json.loads(exc.read().decode("utf-8"))
            code = body_out.get("code") if isinstance(body_out, dict) else None
        except Exception:
            code = None
        raise PriorDossierReadError(code or f"PRIOR_DOSSIER_HTTP_{exc.code}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise PriorDossierReadError("PRIOR_DOSSIER_TRANSPORT_FAILED") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("ok") is not True
        or payload.get("persist_phase") != "READ_PREVIOUS"
        or payload.get("can_execute") is not False
        or payload.get("prediction_authority") is not False
    ):
        raise PriorDossierReadError(
            str(payload.get("code") if isinstance(payload, dict) else "PRIOR_DOSSIER_RESPONSE_INVALID")
        )
    return payload


def _annotate_unavailable(payload: dict[str, Any], code: str) -> dict[str, Any]:
    out = deepcopy(payload)
    model = out.get("model_handoff") if isinstance(out.get("model_handoff"), dict) else {}
    for lane in ("team_event_candidates", "prop_candidates"):
        updated = []
        for candidate in model.get(lane, []) or []:
            if not isinstance(candidate, dict):
                continue
            row = dict(candidate)
            row["prior_dossier_lookup_status"] = "UNAVAILABLE"
            row["prior_dossier_lookup_code"] = code
            updated.append(row)
        model[lane] = updated
    out["model_handoff"] = model
    out["prior_dossier_hydration"] = {
        "status": "UNAVAILABLE",
        "code": code,
        "prediction_authority": False,
        "can_execute": False,
    }
    return out


def hydrate_prior_dossiers(
    payload: dict[str, Any],
    *,
    post_fn: Any = None,
    url: str | None = None,
) -> dict[str, Any]:
    if payload.get("can_execute") is not False:
        raise ValueError("SCOUT_HANDOFF_CAN_EXECUTE_MUST_BE_FALSE")
    current_run = str(payload.get("research_run_id") or payload.get("run_id") or "").strip()
    if not current_run:
        return _annotate_unavailable(payload, "RESEARCH_RUN_ID_MISSING")

    compact = compact_previous_candidates(payload)
    if not compact:
        out = deepcopy(payload)
        out["prior_dossier_hydration"] = {
            "status": "NO_CANDIDATES",
            "candidate_count": 0,
            "prediction_authority": False,
            "can_execute": False,
        }
        return out

    sender = post_fn or _post
    body = {
        "persist_phase": "READ_PREVIOUS",
        "research_run_id": current_run,
        "run_id": str(payload.get("run_id") or current_run),
        "previous_candidates": compact,
        "can_execute": False,
    }
    try:
        response = sender(url or prior_url(), body)
    except PriorDossierReadError as exc:
        return _annotate_unavailable(payload, str(exc))
    except Exception as exc:
        return _annotate_unavailable(payload, f"PRIOR_DOSSIER_READ_EXCEPTION:{type(exc).__name__}")

    results = response.get("candidates") if isinstance(response.get("candidates"), list) else []
    if len(results) != len(compact):
        return _annotate_unavailable(payload, "PRIOR_DOSSIER_RECONCILIATION_FAILED")

    by_key: dict[tuple[str, int], dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("lane") or ""), int(item.get("source_index") or 0))
        by_key[key] = item

    out = deepcopy(payload)
    model = out.get("model_handoff") if isinstance(out.get("model_handoff"), dict) else {}
    status_counts: dict[str, int] = {}
    for lane in ("team_event_candidates", "prop_candidates"):
        updated = []
        for index, candidate in enumerate(model.get(lane, []) or [], 1):
            if not isinstance(candidate, dict):
                continue
            row = dict(candidate)
            result = by_key.get((lane, index))
            if result is None:
                row["prior_dossier_lookup_status"] = "UNAVAILABLE"
                row["prior_dossier_lookup_code"] = "PRIOR_DOSSIER_ROW_MISSING"
                status = "UNAVAILABLE"
            else:
                status = str(result.get("lookup_status") or "UNAVAILABLE")
                row["prior_dossier_lookup_status"] = status
                row["prior_candidate_id"] = result.get("candidate_id")
                row["previous_research_run_id"] = result.get("previous_research_run_id")
                row["previous_dossier_recorded_at"] = result.get("previous_recorded_at")
                prior = result.get("prior_dossier")
                if status == "FOUND" and isinstance(prior, dict):
                    row["_prior_scout_dossier"] = prior
            status_counts[status] = status_counts.get(status, 0) + 1
            updated.append(row)
        model[lane] = updated
    out["model_handoff"] = model
    out["prior_dossier_hydration"] = {
        "status": "COMPLETE",
        "candidate_count": len(compact),
        "status_counts": status_counts,
        "prediction_authority": False,
        "can_execute": False,
    }
    return out


__all__ = [
    "PriorDossierReadError",
    "compact_previous_candidates",
    "hydrate_prior_dossiers",
]
