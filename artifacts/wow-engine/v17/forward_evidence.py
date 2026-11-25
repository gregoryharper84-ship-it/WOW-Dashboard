"""Shared prospective forward-evidence lifecycle for research candidates.

One mechanism for every sport that refits research candidates but never
captured them before the event (NCAAF first; NHL, NCAAB, soccer adapters):

1. Freeze: the first eligible candidate becomes the EVALUATION registration
   for its sport/league/market. Later refits never move it, so the
   certification cohort stays on one exact artifact. A challenger needs its
   own registration row.
2. Capture: score upcoming events with the frozen artifact and insert an
   immutable pregame row. The database sets created_at from its own clock
   and rejects rows at or after kickoff, rows for another artifact, and
   probability vectors that are not normalized inside (0, 1).
3. Settle: record official outcomes for captured events after kickoff.
4. Grade: ``wow_v17_forward_evidence_graded`` joins the latest pregame
   capture to its settlement; the receipt carries a scorecard.

Evidence only. Nothing here certifies, promotes, publishes, ranks or
executes, and no terminal label reads these tables. One event's failure is
counted by code and never stops the others; a sport's failure is returned in
its receipt and never raises into maintenance. can_execute is always false.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol, Sequence

CAN_EXECUTE = False
REGISTRATION_TABLE = "wow_v17_forward_evidence_registrations"
CAPTURE_TABLE = "wow_v17_forward_evidence_captures"
SETTLEMENT_TABLE = "wow_v17_forward_evidence_settlements"
GRADED_VIEW = "wow_v17_forward_evidence_graded"
CANDIDATE_TABLE = "wow_d1_candidate_artifacts"
FREEZE_POLICY = "FIRST_ELIGIBLE_CANDIDATE_FROZEN_V1"
OUTCOME_KEYS = {
    "BINARY_HOME_AWAY": ("AWAY", "HOME"),
    "THREE_WAY_1X2": ("AWAY", "DRAW", "HOME"),
}
DEFAULT_HORIZON_DAYS = 8
SETTLEMENT_GRACE = timedelta(hours=3)
SETTLEMENT_LOOKBACK = timedelta(days=21)
MAX_EVENTS_PER_RUN = 400
_INERT = {
    "probability_publishable": False,
    "rank_eligible": False,
    "automatic_certification": False,
    "automatic_promotion": False,
    "can_execute": False,
}


class ForwardEvidenceBlocked(RuntimeError):
    """A typed, expected stop. ``code`` is safe to log and report."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class EventIdentity:
    official_event_id: str
    event_start_time_utc: datetime
    home_participant: str
    away_participant: str
    identity_provider: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Forecast:
    probabilities: Mapping[str, float]
    features: Mapping[str, Any]
    source_history_digest: str


class SportAdapter(Protocol):
    sport: str
    league: str
    market_family: str
    model_family: str
    outcome_space: str

    def eligible_candidates(self, db: Any, now: datetime) -> Sequence[Mapping[str, Any]]: ...
    def verify_candidate(self, candidate: Mapping[str, Any], now: datetime) -> None: ...
    def upcoming_events(self, db: Any, now: datetime, horizon: timedelta) -> Sequence[EventIdentity]: ...
    def load_context(self, db: Any, now: datetime) -> Any: ...
    def score(self, event: EventIdentity, context: Any, candidate: Mapping[str, Any], now: datetime) -> Forecast: ...
    def settlements(self, db: Any, pending: Sequence[Mapping[str, Any]]) -> Sequence[Mapping[str, Any]]: ...


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _aware(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ForwardEvidenceBlocked("FORWARD_EVIDENCE_TIME_NOT_TIMEZONE_AWARE")
    return parsed.astimezone(timezone.utc)


def _rows(response: Any) -> list[Mapping[str, Any]]:
    data = getattr(response, "data", None)
    if not isinstance(data, list) or not all(isinstance(row, Mapping) for row in data):
        raise ForwardEvidenceBlocked("FORWARD_EVIDENCE_READ_INVALID")
    return data


def _code(exc: BaseException, default: str) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code.strip() and len(code) <= 120:
        code = code.strip()
        # A bare SQLSTATE (for example 42P01 before the migration exists).
        if len(code) == 5 and code.isalnum() and code.upper() == code:
            return f"FORWARD_EVIDENCE_DB_{code}"
        return code
    return default


def _is_duplicate(exc: BaseException) -> bool:
    text = f"{getattr(exc, 'code', '')} {exc}"
    return "23505" in text or "duplicate key" in text.lower()


def validate_probabilities(outcome_space: str, probabilities: Mapping[str, Any]) -> dict[str, float]:
    """Mirror of the database capture guard; reject before any write."""
    keys = OUTCOME_KEYS.get(outcome_space)
    if keys is None:
        raise ForwardEvidenceBlocked("FORWARD_EVIDENCE_OUTCOME_SPACE_INVALID")
    if not isinstance(probabilities, Mapping) or tuple(sorted(probabilities)) != keys:
        raise ForwardEvidenceBlocked("FORWARD_EVIDENCE_PROBABILITY_KEYS_INVALID")
    values: dict[str, float] = {}
    for key in keys:
        raw = probabilities[key]
        if type(raw) not in (int, float) or not math.isfinite(float(raw)) or not 0 < float(raw) < 1:
            raise ForwardEvidenceBlocked("FORWARD_EVIDENCE_PROBABILITY_DOMAIN_INVALID")
        values[key] = float(raw)
    if abs(sum(values.values()) - 1.0) > 1e-6:
        raise ForwardEvidenceBlocked("FORWARD_EVIDENCE_PROBABILITIES_NOT_NORMALIZED")
    return values


def ensure_registration(db: Any, adapter: SportAdapter, now: datetime) -> Mapping[str, Any]:
    """Return the frozen EVALUATION registration, creating it once."""

    def read() -> list[Mapping[str, Any]]:
        return _rows(
            db.table(REGISTRATION_TABLE).select("*")
            .eq("sport", adapter.sport).eq("league", adapter.league)
            .eq("market_family", adapter.market_family).eq("cohort_role", "EVALUATION")
            .limit(2).execute()
        )

    existing = read()
    if len(existing) > 1:
        raise ForwardEvidenceBlocked("FORWARD_EVIDENCE_REGISTRATION_AMBIGUOUS")
    if existing:
        row = existing[0]
        if row.get("model_family") != adapter.model_family or row.get("outcome_space") != adapter.outcome_space:
            raise ForwardEvidenceBlocked("FORWARD_EVIDENCE_REGISTRATION_FAMILY_MISMATCH")
        return row

    chosen = None
    for candidate in adapter.eligible_candidates(db, now):
        try:
            adapter.verify_candidate(candidate, now)
        except Exception:  # noqa: BLE001 - an invalid candidate is skipped, never frozen
            continue
        chosen = candidate
        break
    if chosen is None:
        raise ForwardEvidenceBlocked("FORWARD_EVIDENCE_NO_ELIGIBLE_CANDIDATE")
    row = {
        "sport": adapter.sport,
        "league": adapter.league,
        "market_family": adapter.market_family,
        "cohort_role": "EVALUATION",
        "outcome_space": adapter.outcome_space,
        "model_family": adapter.model_family,
        "candidate_table": CANDIDATE_TABLE,
        "candidate_id": str(chosen.get("candidate_id")),
        "model_artifact_version": str(chosen.get("model_artifact_version")),
        "artifact_checksum": str(chosen.get("artifact_checksum")),
        "training_dataset_hash": chosen.get("training_dataset_hash"),
        "freeze_policy": FREEZE_POLICY,
        "probability_publishable": False,
        "can_execute": False,
    }
    try:
        db.table(REGISTRATION_TABLE).insert(row).execute()
    except Exception as exc:  # noqa: BLE001
        if not _is_duplicate(exc):  # a concurrent run froze first; re-read below
            raise ForwardEvidenceBlocked("FORWARD_EVIDENCE_REGISTRATION_WRITE_FAILED") from exc
    existing = read()
    if len(existing) != 1:
        raise ForwardEvidenceBlocked("FORWARD_EVIDENCE_REGISTRATION_READBACK_FAILED")
    return existing[0]


def load_frozen_candidate(db: Any, registration: Mapping[str, Any]) -> Mapping[str, Any]:
    rows = _rows(
        db.table(CANDIDATE_TABLE).select("*")
        .eq("candidate_id", registration["candidate_id"]).limit(2).execute()
    )
    if len(rows) != 1:
        raise ForwardEvidenceBlocked("FORWARD_EVIDENCE_FROZEN_CANDIDATE_NOT_FOUND")
    candidate = rows[0]
    if (candidate.get("artifact_checksum") != registration.get("artifact_checksum")
            or candidate.get("model_artifact_version") != registration.get("model_artifact_version")):
        raise ForwardEvidenceBlocked("FORWARD_EVIDENCE_FROZEN_CANDIDATE_IDENTITY_MISMATCH")
    return candidate


def capture_row(registration: Mapping[str, Any], candidate: Mapping[str, Any], event: EventIdentity,
                forecast: Forecast, outcome_space: str) -> dict[str, Any]:
    # The row carries the identity of the artifact that actually scored; the
    # database guard rejects it unless that equals the frozen registration.
    if (candidate.get("artifact_checksum") != registration.get("artifact_checksum")
            or candidate.get("model_artifact_version") != registration.get("model_artifact_version")):
        raise ForwardEvidenceBlocked("FORWARD_EVIDENCE_FROZEN_CANDIDATE_IDENTITY_MISMATCH")
    probabilities = validate_probabilities(outcome_space, forecast.probabilities)
    digest = str(forecast.source_history_digest or "")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ForwardEvidenceBlocked("FORWARD_EVIDENCE_HISTORY_DIGEST_INVALID")
    features = dict(forecast.features)
    if not features:
        raise ForwardEvidenceBlocked("FORWARD_EVIDENCE_FEATURES_MISSING")
    return {
        "registration_id": registration["registration_id"],
        "sport": registration["sport"],
        "league": registration["league"],
        "official_event_id": event.official_event_id,
        "event_start_time_utc": event.event_start_time_utc.isoformat(),
        "home_participant": event.home_participant,
        "away_participant": event.away_participant,
        "identity_provider": event.identity_provider,
        "model_artifact_version": candidate["model_artifact_version"],
        "artifact_checksum": candidate["artifact_checksum"],
        "probabilities": probabilities,
        "features": features,
        "feature_payload_sha256": _sha256_json({"features": features, "history": digest}),
        "source_history_digest": digest,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def _pending_settlements(db: Any, adapter: SportAdapter, registration: Mapping[str, Any],
                         now: datetime) -> list[Mapping[str, Any]]:
    captured = _rows(
        db.table(CAPTURE_TABLE).select("official_event_id,event_start_time_utc")
        .eq("registration_id", registration["registration_id"])
        .lt("event_start_time_utc", (now - SETTLEMENT_GRACE).isoformat())
        .gt("event_start_time_utc", (now - SETTLEMENT_LOOKBACK).isoformat())
        .limit(5000).execute()
    )
    by_event: dict[str, Mapping[str, Any]] = {}
    for row in captured:
        by_event.setdefault(str(row["official_event_id"]), row)
    if not by_event:
        return []
    settled = _rows(
        db.table(SETTLEMENT_TABLE).select("official_event_id")
        .eq("sport", adapter.sport).eq("league", adapter.league)
        .in_("official_event_id", sorted(by_event)).execute()
    )
    done = {str(row["official_event_id"]) for row in settled}
    return [row for event_id, row in sorted(by_event.items()) if event_id not in done]


def scorecard(db: Any, registration: Mapping[str, Any]) -> dict[str, Any]:
    rows = _rows(
        db.table(GRADED_VIEW).select("official_event_id,brier,log_loss,outcome_probability,probabilities,outcome")
        .eq("registration_id", registration["registration_id"]).limit(10000).execute()
    )
    n = len(rows)
    card: dict[str, Any] = {"graded_n": n, "can_execute": False}
    if n:
        card["brier"] = sum(float(row["brier"]) for row in rows) / n
        card["log_loss"] = sum(float(row["log_loss"]) for row in rows) / n
        card["mean_outcome_probability"] = sum(float(row["outcome_probability"]) for row in rows) / n
    return card


def run_forward_evidence(
    db: Any,
    adapter: SportAdapter,
    *,
    now: datetime | None = None,
    horizon: timedelta = timedelta(days=DEFAULT_HORIZON_DAYS),
    capture: bool = True,
    capture_skip_reason: str | None = None,
) -> dict[str, Any]:
    """Run one sport end to end. Never raises; the receipt carries the outcome."""
    now = _aware(now or datetime.now(timezone.utc))
    receipt: dict[str, Any] = {
        "schema_version": "V17_FORWARD_EVIDENCE_RECEIPT_V1",
        "sport": adapter.sport,
        "league": adapter.league,
        "market_family": adapter.market_family,
        "model_family": adapter.model_family,
        "as_of_utc": now.isoformat(),
        "status": "STARTED",
        "events_considered": 0,
        "captured": 0,
        "duplicate": 0,
        "blocked_by_code": {},
        "settled": 0,
        "settlement_blocked_by_code": {},
        **_INERT,
    }
    blocked: dict[str, int] = receipt["blocked_by_code"]
    settle_blocked: dict[str, int] = receipt["settlement_blocked_by_code"]
    try:
        registration = ensure_registration(db, adapter, now)
        receipt["registration_id"] = registration["registration_id"]
        receipt["frozen_artifact_checksum"] = registration["artifact_checksum"]
        receipt["frozen_model_artifact_version"] = registration["model_artifact_version"]
        if capture:
            candidate = load_frozen_candidate(db, registration)
            try:
                adapter.verify_candidate(candidate, now)
            except Exception as exc:  # noqa: BLE001
                raise ForwardEvidenceBlocked(_code(exc, "FORWARD_EVIDENCE_FROZEN_CANDIDATE_INVALID")) from exc
            events = list(adapter.upcoming_events(db, now, horizon))[:MAX_EVENTS_PER_RUN]
            receipt["events_considered"] = len(events)
            context = adapter.load_context(db, now) if events else None
            for event in events:
                try:
                    if not now < event.event_start_time_utc <= now + horizon:
                        raise ForwardEvidenceBlocked("FORWARD_EVIDENCE_EVENT_OUTSIDE_WINDOW")
                    forecast = adapter.score(event, context, candidate, now)
                    row = capture_row(registration, candidate, event, forecast, adapter.outcome_space)
                except Exception as exc:  # noqa: BLE001 - isolate each event
                    key = _code(exc, f"FORWARD_EVIDENCE_SCORE_{type(exc).__name__.upper()}")
                    blocked[key] = blocked.get(key, 0) + 1
                    continue
                try:
                    db.table(CAPTURE_TABLE).insert(row).execute()
                    receipt["captured"] += 1
                except Exception as exc:  # noqa: BLE001
                    if _is_duplicate(exc):
                        receipt["duplicate"] += 1
                    else:
                        blocked["FORWARD_EVIDENCE_CAPTURE_WRITE_FAILED"] = (
                            blocked.get("FORWARD_EVIDENCE_CAPTURE_WRITE_FAILED", 0) + 1)
        else:
            receipt["capture_skipped_reason"] = capture_skip_reason or "CAPTURE_DISABLED_BY_CALLER"

        for row in adapter.settlements(db, _pending_settlements(db, adapter, registration, now)):
            if row.get("blocked_code"):
                key = str(row["blocked_code"])
                settle_blocked[key] = settle_blocked.get(key, 0) + 1
                continue
            try:
                db.table(SETTLEMENT_TABLE).insert({**row, "sport": adapter.sport,
                                                   "league": adapter.league, "can_execute": False}).execute()
                receipt["settled"] += 1
            except Exception as exc:  # noqa: BLE001
                key = "FORWARD_EVIDENCE_SETTLEMENT_DUPLICATE" if _is_duplicate(exc) else "FORWARD_EVIDENCE_SETTLEMENT_WRITE_FAILED"
                settle_blocked[key] = settle_blocked.get(key, 0) + 1
        receipt["scorecard"] = scorecard(db, registration)
        receipt["status"] = "FORWARD_EVIDENCE_UPDATED"
    except ForwardEvidenceBlocked as exc:
        receipt["status"] = "BLOCKED"
        receipt["code"] = exc.code
    except Exception as exc:  # noqa: BLE001 - never raise into maintenance
        receipt["status"] = "FAILED"
        receipt["code"] = _code(exc, f"FORWARD_EVIDENCE_{type(exc).__name__.upper()}")
    receipt.update(_INERT)
    return receipt
