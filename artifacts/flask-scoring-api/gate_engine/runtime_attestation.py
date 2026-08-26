"""Runtime build attestation for release certification.

The deployment build writes ``runtime_build_info.json`` while repository Git
metadata is still available. Production workers only read that immutable file;
they never guess provenance from a hard-coded application label or rely on
runtime ``.git`` availability.

This module is observability-only. It never changes scoring, governance
outcomes, or execution controls.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_BUILD_INFO_PATH = Path(__file__).resolve().parents[1] / "runtime_build_info.json"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def load_build_info(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the immutable build-time provenance artifact.

    Missing or malformed evidence fails closed with ``build_attested=False``.
    """
    target = path or _BUILD_INFO_PATH
    fallback: dict[str, Any] = {
        "source_sha": None,
        "source_ref": None,
        "build_attested": False,
        "attestation_version": 1,
    }
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return fallback
    if not isinstance(payload, dict):
        return fallback

    sha = str(payload.get("source_sha") or "").strip().lower()
    ref = str(payload.get("source_ref") or "").strip()
    worktree_clean = payload.get("worktree_clean") is True
    declared_attested = payload.get("build_attested") is True
    attested = bool(_SHA_RE.fullmatch(sha) and ref and worktree_clean and declared_attested)

    return {
        "source_sha": sha if _SHA_RE.fullmatch(sha) else None,
        "source_ref": ref or None,
        "build_attested": attested,
        "attestation_version": payload.get("attestation_version", 1),
        "build_generated_at_utc": payload.get("build_generated_at_utc"),
        "worktree_clean": worktree_clean,
    }


def install_flask_attestation(flask_app, build_info: dict[str, Any] | None = None) -> dict[str, Any]:
    """Inject provenance into engine health/readiness JSON responses.

    Registration happens from Gunicorn ``post_fork`` before a worker serves its
    first request. The handler is deliberately limited to the two release-health
    endpoints.
    """
    info = dict(build_info or load_build_info())

    @flask_app.after_request
    def _attach_runtime_attestation(response):
        try:
            from flask import request

            if request.path not in {"/wow/engine/health", "/wow/engine/ready"}:
                return response
            payload = response.get_json(silent=True)
            if not isinstance(payload, dict):
                return response
            payload["source_sha"] = info.get("source_sha")
            payload["source_ref"] = info.get("source_ref")
            payload["build_attested"] = bool(info.get("build_attested"))
            payload["attestation_version"] = info.get("attestation_version", 1)
            response.set_data(json.dumps(payload, separators=(",", ":")))
            response.mimetype = "application/json"
        except Exception:
            # Observability must never turn a healthy endpoint into a 500. The
            # absence of injected evidence leaves certification fail-closed.
            pass
        return response

    return info
