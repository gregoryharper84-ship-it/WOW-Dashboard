"""Read-only WOW V17 runtime restoration status.

Reports ``RUNTIME_RESTORED`` for a priority sport when the deployed runtime
demonstrably executes the reviewed commit, and a live request terminated through
``V17_TERMINAL_REDUCER`` with a scored row and an immutable prediction receipt.

``MODEL_CERTIFIED`` is deliberately not reported here. It is read straight from
the governed verdict rows by ``v17/model_certification_verdicts.sql``: the NFL
calibration-health ledger, the basketball certification row and the NCAAF
artifact row. Re-deriving those gates in Python drifts silently; the SQL reads
the verdict the database already recorded.

Nothing here writes, promotes, merges, deploys or certifies; every report it
returns carries ``can_execute=false``.

Live provenance cannot be forged: a successful ``live:`` ``ReadResult`` is
rejected unless a reader built it with a module-private token, so a supplied
literal such as ``render_web_sha="c0ffee1"`` cannot establish restoration.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any

from v17.team_event_capability_manifest import normalize_team_event_sport

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
LIVE_SOURCE_PREFIX = "live:"
PRIORITY_SPORTS: tuple[str, ...] = ("NFL", "NBA", "NHL", "NCAAF")
TERMINAL_MODEL_FAILURES = frozenset(
    {"MODEL_UNAVAILABLE", "MODEL_SCORER_FAILED", "MODEL_INPUTS_INSUFFICIENT", "MODEL_OUTPUT_INVALID"}
)
Transport = Callable[[str], tuple[int, Any]]
_LIVE_ATTESTATION = object()


@dataclass(frozen=True)
class ReadResult:
    """One observed value and the provenance needed to judge it."""

    ok: bool
    value: Any
    source: str
    captured_at: str
    error: str | None = None
    _live_attested: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not _text(self.source) or not _text(self.captured_at):
            raise ValueError("EVIDENCE_SOURCE_AND_CAPTURED_AT_REQUIRED")
        if self.ok and is_live_source(self.source) and not self._live_attested:
            raise PermissionError("LIVE_EVIDENCE_MUST_BE_PRODUCED_BY_A_READER")

    @classmethod
    def unobserved(cls, *, source: str, captured_at: str, error: str) -> "ReadResult":
        return cls(False, None, source, captured_at, str(error))

    @classmethod
    def _attested_live(cls, value: Any, *, source: str, captured_at: str, attestation: object) -> "ReadResult":
        if attestation is not _LIVE_ATTESTATION:
            raise PermissionError("LIVE_EVIDENCE_MUST_BE_PRODUCED_BY_A_READER")
        if not is_live_source(source):
            raise ValueError("LIVE_EVIDENCE_SOURCE_MUST_BE_LIVE_PREFIXED")
        return cls(True, value, source, captured_at, _live_attested=True)


def is_live_source(source: str) -> bool:
    return str(source or "").startswith(LIVE_SOURCE_PREFIX)


def _require_priority_sport(value: str) -> str:
    sport = normalize_team_event_sport(value)
    if sport not in PRIORITY_SPORTS:
        raise ValueError(f"unsupported restoration verifier sport: {value}")
    return sport


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _int(value: Any) -> int | None:
    return value if type(value) is int else None


def _blockers(items: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({str(item) for item in items if item}))


def _read(
    transport: Transport,
    url: str,
    source: str,
    captured_at: str,
    extract: Callable[[Any], Any],
    missing: str,
) -> ReadResult:
    try:
        status, payload = transport(url)
    except Exception as exc:  # noqa: BLE001 - transport failure is evidence
        return ReadResult.unobserved(
            source=source, captured_at=captured_at, error=f"TRANSPORT_FAILED:{type(exc).__name__}"
        )
    if status != 200:
        return ReadResult.unobserved(source=source, captured_at=captured_at, error=f"HTTP_{status}")
    value = extract(payload)
    if value is None:
        return ReadResult.unobserved(source=source, captured_at=captured_at, error=missing)
    return ReadResult._attested_live(
        value, source=source, captured_at=captured_at, attestation=_LIVE_ATTESTATION
    )


def read_github_main_sha(transport: Transport, *, repo: str, captured_at: str) -> ReadResult:
    """Read the protected ``main`` commit SHA."""
    return _read(
        transport,
        f"https://api.github.com/repos/{repo}/commits/main",
        f"{LIVE_SOURCE_PREFIX}github:{repo}@main",
        captured_at,
        lambda payload: _text(payload.get("sha")) if isinstance(payload, Mapping) else None,
        "COMMIT_SHA_MISSING",
    )


def read_render_service_commit(transport: Transport, *, service_id: str, captured_at: str) -> ReadResult:
    """Read the commit currently served by one Render service."""

    def extract(payload: Any) -> str | None:
        first = payload[0] if isinstance(payload, Sequence) and payload else None
        deploy = first.get("deploy") if isinstance(first, Mapping) else None
        commit = deploy.get("commit") if isinstance(deploy, Mapping) else None
        return _text(commit.get("id")) if isinstance(commit, Mapping) else None

    return _read(
        transport,
        f"https://api.render.com/v1/services/{service_id}/deploys?limit=1",
        f"{LIVE_SOURCE_PREFIX}render:service:{service_id}",
        captured_at,
        extract,
        "DEPLOY_COMMIT_MISSING",
    )


def read_runtime_terminal_receipt(
    transport: Transport, *, origin: str, path: str, captured_at: str
) -> ReadResult:
    """Read one live governed request receipt."""
    base, rel = str(origin or "").rstrip("/"), str(path or "").strip()
    if not base or not rel.startswith("/"):
        raise ValueError("RUNTIME_ORIGIN_AND_ABSOLUTE_PATH_REQUIRED")

    def extract(payload: Any) -> Any:
        if not isinstance(payload, Mapping):
            return None
        if payload.get("can_execute") is not False:
            raise PermissionError("GOVERNANCE_VIOLATION: runtime receipt must report can_execute=false")
        return dict(payload)

    return _read(
        transport,
        base + rel,
        f"{LIVE_SOURCE_PREFIX}runtime:{base}{rel}",
        captured_at,
        extract,
        "RECEIPT_SHAPE_INVALID",
    )


def assess_runtime(
    sport: str,
    *,
    github_main: ReadResult,
    render_web: ReadResult,
    render_worker: ReadResult,
    runtime_receipt: ReadResult,
) -> dict[str, Any]:
    """Decide whether the deployed runtime is demonstrably restored."""
    key = _require_priority_sport(sport)
    evidence = {
        "GITHUB_MAIN": github_main,
        "RENDER_WEB": render_web,
        "RENDER_WORKER": render_worker,
        "RUNTIME_RECEIPT": runtime_receipt,
    }
    for label, result in evidence.items():
        if not isinstance(result, ReadResult):
            raise ValueError(f"EVIDENCE_NOT_A_READ_RESULT:{label}")

    unreadable = [label for label, result in evidence.items() if not result.ok]
    non_live = [label for label, result in evidence.items() if result.ok and not is_live_source(result.source)]
    blockers = [f"{label}_UNREAD:{evidence[label].error or 'UNSPECIFIED'}" for label in unreadable]
    blockers += [f"{label}_PROVENANCE_NOT_LIVE" for label in non_live]

    main_sha, web_sha, worker_sha = (
        _text(evidence[label].value) if evidence[label].ok else None
        for label in ("GITHUB_MAIN", "RENDER_WEB", "RENDER_WORKER")
    )

    receipt = runtime_receipt.value if runtime_receipt.ok else None
    reported_sha = authority = disposition = receipt_id = scored_rows = None
    if isinstance(receipt, Mapping):
        if receipt.get("can_execute") is not False:
            raise PermissionError("GOVERNANCE_VIOLATION: runtime receipt must report can_execute=false")
        reported_sha, authority = _text(receipt.get("deployed_commit")), _text(receipt.get("terminal_authority"))
        disposition, receipt_id = _text(receipt.get("disposition")), _text(receipt.get("prediction_receipt_id"))
        scored_rows = _int(receipt.get("scored_rows"))
        scored_rows = scored_rows if scored_rows is not None and scored_rows >= 0 else None
    elif runtime_receipt.ok:
        blockers.append("RUNTIME_RECEIPT_SHAPE_INVALID")

    # Comparisons only mean something once every source was read live.
    if not unreadable and not non_live:
        if not (main_sha and web_sha and worker_sha):
            blockers.append("RUNTIME_SHA_EVIDENCE_INCOMPLETE")
        else:
            blockers += [
                name
                for name, failed in (
                    ("RENDER_WEB_SHA_MISMATCH", web_sha != main_sha),
                    ("RENDER_WORKER_SHA_MISMATCH", worker_sha != main_sha),
                )
                if failed
            ]
        if isinstance(receipt, Mapping):
            blockers += [
                name
                for name, failed in (
                    ("RUNTIME_TERMINAL_AUTHORITY_MISMATCH", authority != TERMINAL_AUTHORITY),
                    ("RUNTIME_IMMUTABLE_RECEIPT_MISSING", not receipt_id),
                    ("RUNTIME_NO_SCORED_ROW", not scored_rows),
                    ("RUNTIME_REPORTED_SHA_MISMATCH", bool(reported_sha and main_sha and reported_sha != main_sha)),
                )
                if failed
            ]
            if disposition in TERMINAL_MODEL_FAILURES:
                blockers.append(f"RUNTIME_TERMINAL_MODEL_FAILURE:{disposition}")

    final = _blockers(blockers)
    status = "RUNTIME_UNVERIFIED" if (unreadable or non_live) else ("RUNTIME_HOLD" if final else "RUNTIME_RESTORED")
    return {
        "sport": key,
        "status": status,
        "github_main_sha": main_sha,
        "render_web_sha": web_sha,
        "render_worker_sha": worker_sha,
        "runtime_reported_sha": reported_sha,
        "terminal_authority": authority,
        "disposition": disposition,
        "scored_rows": scored_rows,
        "prediction_receipt_id": receipt_id,
        "evidence_sources": sorted(result.source for result in evidence.values()),
        "blockers": list(final),
        "can_execute": CAN_EXECUTE,
    }


def report_digest(reports: Sequence[Mapping[str, Any]]) -> str:
    """Stable digest over a normalised report, safe to attach as evidence."""
    ordered = sorted(reports, key=lambda report: report["sport"])
    return hashlib.sha256(json.dumps(ordered, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


__all__ = [
    "CAN_EXECUTE",
    "PRIORITY_SPORTS",
    "ReadResult",
    "TERMINAL_AUTHORITY",
    "Transport",
    "assess_runtime",
    "is_live_source",
    "read_github_main_sha",
    "read_render_service_commit",
    "read_runtime_terminal_receipt",
    "report_digest",
]
