"""Secretless Scout Brain persistence through the governed Supabase Edge Function.

The caller must be the exact protected-main Scout persistence GitHub workflow.
GitHub OIDC is short-lived; no Supabase database or service-role credential is
stored in GitHub. The Edge Function re-validates governance and writes only
research-layer state with can_execute=false.

Uploads are sliced so that no single request carries an unbounded amount of
work. Bounding whole candidates was not enough: a team/event candidate holds
every bookmaker x market x outcome row for its event, and one such candidate in
run 35131443035 was 1,135,529 bytes across 1,952 evidence rows, with the largest
reaching 5,291,090 bytes across 2,704. Because a batch always emitted at least
one candidate, that candidate was posted whole under both the 256 KiB and the
64 KiB bound and exhausted the worker each time. Slicing applies the byte and
row bounds to the request itself, evidence included.

Slicing is transport only. The Edge Function re-validates governance on every
request, no row is upgraded, and a candidate is counted once, on its final
slice, rather than once per slice.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from v17.github_actions_oidc_client import GitHubOIDCMintError, mint_github_actions_oidc

DEFAULT_URL = "https://iczfhsmjrrafhvcpmqhr.supabase.co/functions/v1/wow-v17-scout-brain-persist"
DEFAULT_REQUEST_BYTES = 256 * 1024
DEFAULT_EVIDENCE_ROWS = 150
CANDIDATE_LANES = ("team_event_candidates", "prop_candidates")
TEAM_EVENT_ROUTE = "LLP_TEAM_BETTING_ENGINE"


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


def _size(payload: Any) -> int:
    return len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def handoff_header(handoff: dict[str, Any]) -> dict[str, Any]:
    """Run identity and governance context, without the candidate lanes."""
    header = {k: v for k, v in handoff.items() if k != "model_handoff"}
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


def is_team_event(row: dict[str, Any]) -> bool:
    route = str(row.get("route") or row.get("controlling_specialist_route") or "")
    return route == TEAM_EVENT_ROUTE


def evidence_list(row: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = row.get("market_evidence")
    if isinstance(evidence, list):
        return [item for item in evidence if isinstance(item, dict) and item]
    if isinstance(evidence, dict) and evidence:
        return [evidence]
    return []


def candidate_metadata(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k != "market_evidence"}


def candidate_slices(
    row: dict[str, Any],
    *,
    max_evidence_rows: int,
    max_bytes: int,
) -> Iterator[dict[str, Any]]:
    """Yield one or more wire entries for a candidate.

    A prop-shaped row carries a single evidence object and is never sliced, so
    its `market_evidence` keeps the object shape the Edge Function's existing
    identity algorithm depends on. A team/event row is sliced, and its identity
    is derived server-side from event fields instead of the first evidence row.
    """
    meta = candidate_metadata(row)
    evidence = evidence_list(row)
    total = len(evidence)

    if not is_team_event(row):
        entry = dict(meta)
        entry["market_evidence"] = row.get("market_evidence")
        entry["evidence_slice"] = {"index": 0, "final": True, "offset": 0, "count": total, "total": total}
        yield entry
        return

    meta_bytes = _size(meta)
    index = 0
    offset = 0
    while offset < total or (total == 0 and index == 0):
        chunk: list[dict[str, Any]] = []
        size = meta_bytes
        while offset < total and len(chunk) < max_evidence_rows:
            row_size = _size(evidence[offset])
            if chunk and size + row_size > max_bytes:
                break
            chunk.append(evidence[offset])
            size += row_size
            offset += 1
        entry = dict(meta)
        entry["market_evidence"] = chunk
        entry["evidence_slice"] = {
            "index": index,
            "final": offset >= total,
            "offset": offset - len(chunk),
            "count": len(chunk),
            "total": total,
        }
        yield entry
        index += 1
        if total == 0:
            return


def requests_for(
    rows: list[dict[str, Any]],
    *,
    max_evidence_rows: int,
    max_bytes: int,
) -> Iterator[list[dict[str, Any]]]:
    """Pack candidate slices into APPEND requests bounded by rows and bytes.

    Packing keeps a prop-heavy slate from becoming one request per candidate
    while still bounding the work any single request asks the worker to do.
    """
    batch: list[dict[str, Any]] = []
    size = 0
    rows_in_batch = 0
    for row in rows:
        for entry in candidate_slices(row, max_evidence_rows=max_evidence_rows, max_bytes=max_bytes):
            entry_size = _size(entry)
            entry_rows = len(evidence_list(entry))
            if batch and (size + entry_size > max_bytes or rows_in_batch + entry_rows > max_evidence_rows):
                yield batch
                batch, size, rows_in_batch = [], 0, 0
            batch.append(entry)
            size += entry_size
            rows_in_batch += entry_rows
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
        with urlopen(req, timeout=120) as response:
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
    expected_evidence = sum(len(evidence_list(row)) for row in rows)

    _post(url, {**header, "persist_phase": "BEGIN"})

    finalized = 0
    uploaded_evidence = 0
    request_count = 0
    for batch in requests_for(
        rows,
        max_evidence_rows=_int_env("WOW_SCOUT_PERSIST_EVIDENCE_ROWS", DEFAULT_EVIDENCE_ROWS),
        max_bytes=_int_env("WOW_SCOUT_PERSIST_REQUEST_BYTES", DEFAULT_REQUEST_BYTES),
    ):
        _post(url, {**header, "persist_phase": "APPEND", "candidates": batch})
        request_count += 1
        for entry in batch:
            slice_meta = entry.get("evidence_slice") or {}
            uploaded_evidence += len(evidence_list(entry))
            if slice_meta.get("final") is True:
                finalized += 1
        print(
            f"append request={request_count} entries={len(batch)} "
            f"evidence_rows={sum(len(evidence_list(e)) for e in batch)} "
            f"finalized_candidates={finalized}/{len(rows)}",
            flush=True,
        )

    if finalized != len(rows):
        raise RuntimeError("SCOUT_EDGE_PERSIST_CANDIDATE_RECONCILIATION_FAILED")
    if uploaded_evidence != expected_evidence:
        raise RuntimeError("SCOUT_EDGE_PERSIST_EVIDENCE_RECONCILIATION_FAILED")

    payload = _post(url, {**header, "persist_phase": "FINALIZE"})

    receipt = {k: v for k, v in payload.items() if k != "ok"}
    receipt["uploaded_candidate_rows"] = finalized
    receipt["uploaded_evidence_rows"] = uploaded_evidence
    receipt["upload_requests"] = request_count
    if receipt.get("candidate_count") != finalized:
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
