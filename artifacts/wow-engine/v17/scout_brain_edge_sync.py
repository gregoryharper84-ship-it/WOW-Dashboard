"""Secretless Scout Brain persistence through the governed Supabase Edge Function.

The caller must be the exact protected-main Scout persistence GitHub workflow.
GitHub OIDC is short-lived; no Supabase database or service-role credential is
stored in GitHub. The Edge Function re-validates governance and writes only
research-layer state with can_execute=false.

Large team/event candidates are fragmented by current market-evidence rows
before upload. Heavy historical/stale evidence arrays remain recoverable in the
immutable workflow artifact and are represented in the persisted compact
snapshot by count + SHA-256 digest. This keeps each Edge request bounded while
preserving exact current evidence rows and a verifiable pointer to omitted bulk.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from v17.github_actions_oidc_client import GitHubOIDCMintError, mint_github_actions_oidc

DEFAULT_URL = "https://iczfhsmjrrafhvcpmqhr.supabase.co/functions/v1/wow-v17-scout-brain-persist"
DEFAULT_BATCH_CANDIDATES = 25
DEFAULT_BATCH_BYTES = 256 * 1024
DEFAULT_FRAGMENT_BYTES = 64 * 1024
CANDIDATE_LANES = ("team_event_candidates", "prop_candidates")
HEADER_KEYS = (
    "research_run_id",
    "run_id",
    "status",
    "can_execute",
    "source_blockers",
    "delivery",
    "generated_at",
    "schema_version",
)
HEAVY_SNAPSHOT_FIELDS = ("market_evidence_historical", "market_evidence_stale")


def _int_env(name: str, default: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def persist_url() -> str:
    return str(os.environ.get("WOW_SCOUT_PERSIST_URL") or DEFAULT_URL).strip()


def handoff_header(handoff: dict[str, Any]) -> dict[str, Any]:
    """Minimal run identity + governance context required by the Edge writer."""
    header = {k: handoff.get(k) for k in HEADER_KEYS if k in handoff}
    header["can_execute"] = handoff.get("can_execute")
    return header


def candidate_rows(handoff: dict[str, Any]) -> list[dict[str, Any]]:
    model = handoff.get("model_handoff")
    model = model if isinstance(model, dict) else {}
    rows: list[dict[str, Any]] = []
    for lane in CANDIDATE_LANES:
        for row in model.get(lane) or []:
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _evidence_list(row: dict[str, Any]) -> list[dict[str, Any]]:
    value = row.get("market_evidence")
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict) and value:
        return [value]
    return []


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _stable_candidate_id(row: dict[str, Any], identity_evidence: dict[str, Any]) -> str:
    raw = "|".join(
        "" if value is None else str(value)
        for value in (
            row.get("sport_key"),
            row.get("official_event_id"),
            row.get("route"),
            identity_evidence.get("market_key"),
            identity_evidence.get("description"),
            identity_evidence.get("outcome_name"),
            identity_evidence.get("point"),
        )
    )
    return f"scout_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def _compact_base(row: dict[str, Any]) -> dict[str, Any]:
    base = {k: v for k, v in row.items() if k != "market_evidence" and k not in HEAVY_SNAPSHOT_FIELDS}
    omitted: dict[str, Any] = {}
    for field in HEAVY_SNAPSHOT_FIELDS:
        if field not in row:
            continue
        value = row.get(field)
        encoded = _canonical_bytes(value)
        omitted[field] = {
            "count": len(value) if isinstance(value, list) else (1 if value is not None else 0),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "bytes": len(encoded),
        }
    if omitted:
        base["persistence_omitted_evidence"] = omitted
    return base


def _fragment_size(fragment: dict[str, Any]) -> int:
    return len(json.dumps(fragment, separators=(",", ":")).encode("utf-8"))


def candidate_fragments(row: dict[str, Any], *, max_fragment_bytes: int) -> list[dict[str, Any]]:
    """Split one candidate's current evidence into bounded transport fragments."""
    evidences = _evidence_list(row)
    identity = evidences[0] if evidences else {}
    cid = _stable_candidate_id(row, identity)
    base = _compact_base(row)
    fixed = {
        **base,
        "persistence_candidate_id": cid,
        "persistence_identity_evidence": identity,
        "persistence_original_evidence_count": len(evidences),
    }

    chunks: list[list[dict[str, Any]]] = []
    if not evidences:
        chunks = [[]]
    else:
        chunk: list[dict[str, Any]] = []
        for evidence in evidences:
            probe = {
                **fixed,
                "persistence_fragment_index": 0,
                "persistence_fragment_count": 1,
                "persistence_first_fragment": True,
                "persistence_last_fragment": True,
                "market_evidence": chunk + [evidence],
            }
            if chunk and _fragment_size(probe) > max_fragment_bytes:
                chunks.append(chunk)
                chunk = []
            chunk.append(evidence)
        if chunk:
            chunks.append(chunk)

    out: list[dict[str, Any]] = []
    count = len(chunks)
    for index, evidence_chunk in enumerate(chunks):
        out.append(
            {
                **fixed,
                "persistence_fragment_index": index,
                "persistence_fragment_count": count,
                "persistence_first_fragment": index == 0,
                "persistence_last_fragment": index == count - 1,
                "market_evidence": evidence_chunk,
            }
        )
    return out


def fragmented_rows(rows: list[dict[str, Any]], *, max_fragment_bytes: int) -> list[dict[str, Any]]:
    fragments: list[dict[str, Any]] = []
    for row in rows:
        fragments.extend(candidate_fragments(row, max_fragment_bytes=max_fragment_bytes))
    return fragments


def batches(
    rows: list[dict[str, Any]],
    *,
    max_candidates: int,
    max_bytes: int,
) -> Iterator[list[dict[str, Any]]]:
    """Split already-bounded fragments into request batches by count + bytes."""
    batch: list[dict[str, Any]] = []
    size = 0
    for row in rows:
        row_size = _fragment_size(row)
        if batch and (len(batch) >= max_candidates or size + row_size > max_bytes):
            yield batch
            batch, size = [], 0
        batch.append(row)
        size += row_size
    if batch:
        yield batch


def _post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        token = mint_github_actions_oidc()
    except GitHubOIDCMintError as exc:
        raise RuntimeError(str(exc)) from exc

    req = Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
            code = body.get("code") if isinstance(body, dict) else None
        except Exception:
            code = None
        raise RuntimeError(code or f"SCOUT_EDGE_PERSIST_HTTP_{exc.code}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("SCOUT_EDGE_PERSIST_TRANSPORT_FAILED") from exc

    if not isinstance(body, dict) or body.get("ok") is not True or body.get("can_execute") is not False:
        raise RuntimeError(str(body.get("code") if isinstance(body, dict) else "SCOUT_EDGE_PERSIST_RESPONSE_INVALID"))
    return body


def sync(input_path: str, receipt_path: str) -> dict:
    handoff = json.loads(Path(input_path).read_text(encoding="utf-8"))
    if handoff.get("can_execute") is not False:
        raise RuntimeError("SCOUT_HANDOFF_GOVERNANCE_INVALID")

    url = persist_url()
    header = handoff_header(handoff)
    rows = candidate_rows(handoff)
    batch_bytes = _int_env("WOW_SCOUT_PERSIST_BATCH_BYTES", DEFAULT_BATCH_BYTES)
    fragment_bytes = _int_env("WOW_SCOUT_PERSIST_FRAGMENT_BYTES", DEFAULT_FRAGMENT_BYTES)
    header_bytes = len(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    effective_batch_bytes = max(16 * 1024, batch_bytes - header_bytes - 4096)
    effective_fragment_bytes = max(8 * 1024, min(fragment_bytes, effective_batch_bytes) - 2048)
    fragments = fragmented_rows(rows, max_fragment_bytes=effective_fragment_bytes)

    _post(url, {**header, "persist_phase": "BEGIN"})

    uploaded_candidates = 0
    uploaded_fragments = 0
    batch_count = 0
    for batch in batches(
        fragments,
        max_candidates=_int_env("WOW_SCOUT_PERSIST_BATCH_SIZE", DEFAULT_BATCH_CANDIDATES),
        max_bytes=effective_batch_bytes,
    ):
        expected_candidates = sum(1 for row in batch if row.get("persistence_first_fragment") is True)
        result = _post(url, {**header, "persist_phase": "APPEND", "candidates": batch})
        if int(result.get("batch_candidate_count", -1)) != expected_candidates:
            raise RuntimeError("SCOUT_EDGE_PERSIST_FRAGMENT_RECONCILIATION_FAILED")
        uploaded_candidates += expected_candidates
        uploaded_fragments += len(batch)
        batch_count += 1

    if uploaded_candidates != len(rows):
        raise RuntimeError("SCOUT_EDGE_PERSIST_BATCH_RECONCILIATION_FAILED")

    payload = _post(url, {**header, "persist_phase": "FINALIZE"})

    receipt = {k: v for k, v in payload.items() if k != "ok"}
    receipt["uploaded_candidate_rows"] = uploaded_candidates
    receipt["uploaded_fragments"] = uploaded_fragments
    receipt["upload_batches"] = batch_count
    receipt["transport_fragment_bytes"] = effective_fragment_bytes
    if receipt.get("candidate_count") != uploaded_candidates:
        raise RuntimeError("SCOUT_EDGE_PERSIST_RECEIPT_RECONCILIATION_FAILED")
    Path(receipt_path).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    receipt = sync(args.input, args.receipt)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
