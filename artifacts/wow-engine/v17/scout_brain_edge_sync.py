"""Secretless Scout Brain persistence through the governed Supabase Edge Function.

The caller must be the exact protected-main Scout persistence GitHub workflow.
GitHub OIDC is short-lived; no Supabase database or service-role credential is
stored in GitHub. The Edge Function re-validates governance and writes only
research-layer state with can_execute=false.

The handoff is uploaded in bounded batches (BEGIN / APPEND* / FINALIZE) because
a full nightly slate does not fit in one Edge Function worker: posting it whole
exceeded the worker resource limit and lost the run. Batching changes only the
transport. The Edge Function re-validates governance on every batch, no row is
upgraded, and a batch that fails raises rather than letting the run report a
completion it did not reach.
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
DEFAULT_BATCH_CANDIDATES = 25
DEFAULT_BATCH_BYTES = 256 * 1024
CANDIDATE_LANES = ("team_event_candidates", "prop_candidates")


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
    """Run identity and governance context, without the candidate lanes.

    Every phase carries this so the Edge Function can re-validate governance
    and stamp run identity without ever receiving the whole slate.
    """
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


def batches(
    rows: list[dict[str, Any]],
    *,
    max_candidates: int,
    max_bytes: int,
) -> Iterator[list[dict[str, Any]]]:
    """Split candidates into batches bounded by both count and serialized size.

    Team/event rows carry a list of market-evidence rows and prop rows carry
    one, so a count-only bound would still produce batches that differ in
    weight by an order of magnitude. A batch always carries at least one row,
    so an oversized single candidate still makes progress rather than stalling.
    """
    batch: list[dict[str, Any]] = []
    size = 0
    for row in rows:
        row_size = len(json.dumps(row, separators=(",", ":")).encode("utf-8"))
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

    _post(url, {**header, "persist_phase": "BEGIN"})

    uploaded = 0
    batch_count = 0
    for batch in batches(
        rows,
        max_candidates=_int_env("WOW_SCOUT_PERSIST_BATCH_SIZE", DEFAULT_BATCH_CANDIDATES),
        max_bytes=_int_env("WOW_SCOUT_PERSIST_BATCH_BYTES", DEFAULT_BATCH_BYTES),
    ):
        _post(url, {**header, "persist_phase": "APPEND", "candidates": batch})
        uploaded += len(batch)
        batch_count += 1

    if uploaded != len(rows):
        raise RuntimeError("SCOUT_EDGE_PERSIST_BATCH_RECONCILIATION_FAILED")

    payload = _post(url, {**header, "persist_phase": "FINALIZE"})

    receipt = {k: v for k, v in payload.items() if k != "ok"}
    receipt["uploaded_candidate_rows"] = uploaded
    receipt["upload_batches"] = batch_count
    if receipt.get("candidate_count") != uploaded:
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
