"""Automatic NCAAF closing-line capture for forward trust evaluation.

This module is research/evaluation infrastructure only. It reads an exact
pre-game two-way moneyline quote from a configured read-only market feed and
persists the freshest valid snapshot before event start. It never places,
routes, modifies, or cancels an order.

Recommended scheduler cadence: every 5 minutes.

Required runtime environment for the production adapter:
  SUPABASE_URL
  SUPABASE_SERVICE_KEY
  WOW_NCAAF_MARKET_FEED_URL
Optional:
  WOW_NCAAF_MARKET_FEED_TOKEN

The market feed must return JSON shaped like:
{
  "event_id": "...",
  "team": "...",
  "opponent": "...",
  "selection_price_american": -145,
  "opposing_price_american": 125,
  "retrieved_at": "2026-08-29T18:55:00Z",
  "source": "READ_ONLY_PROVIDER"
}

Missing provider configuration fails closed and creates no synthetic close.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from typing import Optional, Protocol

import httpx

from ncaaf_trust import CLVGrade, grade_clv, two_way_no_vig

CAN_EXECUTE = False
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = True


@dataclass(frozen=True)
class ClosingCandidate:
    prediction_id: str
    event_id: str
    team: str
    opponent: str
    event_start_time: datetime
    entry_no_vig: Optional[float]


@dataclass(frozen=True)
class ClosingQuote:
    event_id: str
    team: str
    opponent: str
    selection_price_american: int
    opposing_price_american: int
    retrieved_at: datetime
    source: str


@dataclass(frozen=True)
class CaptureResult:
    status: str
    candidates_checked: int
    quotes_captured: int
    no_close_marked: int
    provider_failures: int
    identity_failures: int
    stale_quote_failures: int
    can_execute: bool = False


class ClosingQuoteProvider(Protocol):
    def fetch(self, candidate: ClosingCandidate) -> ClosingQuote:
        ...


class ClosingStore(Protocol):
    def pending_pregame(self, *, as_of: datetime, window_minutes: int) -> list[ClosingCandidate]:
        ...

    def unresolved_recent_started(self, *, as_of: datetime, lookback_minutes: int) -> list[ClosingCandidate]:
        ...

    def write_close(self, candidate: ClosingCandidate, quote: ClosingQuote, closing_no_vig: float, clv_grade: CLVGrade) -> None:
        ...

    def mark_no_close(self, candidate: ClosingCandidate, *, as_of: datetime) -> None:
        ...


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _same_text(a: str, b: str) -> bool:
    return " ".join(str(a).casefold().split()) == " ".join(str(b).casefold().split())


def validate_closing_quote(
    candidate: ClosingCandidate,
    quote: ClosingQuote,
    *,
    as_of: datetime,
    max_quote_age_minutes: int = 5,
) -> None:
    """Enforce exact event/participant identity and pregame freshness."""
    now = as_of.astimezone(timezone.utc)
    if quote.event_id != candidate.event_id:
        raise ValueError("CLOSE_EVENT_IDENTITY_MISMATCH")
    if not _same_text(quote.team, candidate.team) or not _same_text(quote.opponent, candidate.opponent):
        raise ValueError("CLOSE_PARTICIPANT_IDENTITY_MISMATCH")
    if quote.retrieved_at >= candidate.event_start_time:
        raise ValueError("CLOSE_QUOTE_NOT_PREGAME")
    age = (now - quote.retrieved_at).total_seconds() / 60.0
    if age < -1:
        raise ValueError("CLOSE_QUOTE_TIMESTAMP_IN_FUTURE")
    if age > max_quote_age_minutes:
        raise ValueError("CLOSE_QUOTE_STALE")
    two_way_no_vig(quote.selection_price_american, quote.opposing_price_american)


def capture_closing_lines(
    provider: ClosingQuoteProvider,
    store: ClosingStore,
    *,
    as_of: Optional[datetime] = None,
    window_minutes: int = 15,
    max_quote_age_minutes: int = 5,
    no_close_lookback_minutes: int = 20,
) -> CaptureResult:
    """Capture the freshest exact two-way quote just before event start.

    Repeated scheduler calls are expected. The store must upsert by prediction
    id so a fresher valid pregame quote replaces an older candidate close.
    After event start, a row with no captured close is marked
    NO_CLOSE_AVAILABLE and is not silently excluded from calibration.
    """
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    candidates = store.pending_pregame(as_of=now, window_minutes=window_minutes)

    captured = 0
    provider_failures = 0
    identity_failures = 0
    stale_failures = 0

    for candidate in candidates:
        try:
            quote = provider.fetch(candidate)
        except Exception:
            provider_failures += 1
            continue
        try:
            validate_closing_quote(candidate, quote, as_of=now, max_quote_age_minutes=max_quote_age_minutes)
        except ValueError as exc:
            if "STALE" in str(exc):
                stale_failures += 1
            else:
                identity_failures += 1
            continue

        closing_no_vig, _, _ = two_way_no_vig(
            quote.selection_price_american,
            quote.opposing_price_american,
        )
        clv = grade_clv(candidate.entry_no_vig, closing_no_vig)
        store.write_close(candidate, quote, closing_no_vig, clv)
        captured += 1

    no_close_marked = 0
    for candidate in store.unresolved_recent_started(as_of=now, lookback_minutes=no_close_lookback_minutes):
        store.mark_no_close(candidate, as_of=now)
        no_close_marked += 1

    status = "PASS" if provider_failures == identity_failures == stale_failures == 0 else "PARTIAL"
    if not candidates and no_close_marked == 0:
        status = "NO_ELIGIBLE_EVENTS"
    return CaptureResult(
        status=status,
        candidates_checked=len(candidates),
        quotes_captured=captured,
        no_close_marked=no_close_marked,
        provider_failures=provider_failures,
        identity_failures=identity_failures,
        stale_quote_failures=stale_failures,
        can_execute=False,
    )


class HttpJsonClosingQuoteProvider:
    """Read-only exact-event market feed adapter.

    No default provider is embedded in WOW. Production configuration must name
    the approved feed URL explicitly. This prevents a silent provider change
    from becoming market evidence.
    """

    def __init__(self, url: str, token: Optional[str] = None, timeout_seconds: float = 8.0):
        if not url:
            raise ValueError("WOW_NCAAF_MARKET_FEED_URL is required")
        self.url = url
        self.token = token
        self.timeout_seconds = timeout_seconds

    def fetch(self, candidate: ClosingCandidate) -> ClosingQuote:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=False) as client:
            response = client.get(
                self.url,
                params={
                    "sport": "NCAAF",
                    "market": "MONEYLINE",
                    "event_id": candidate.event_id,
                    "selection": candidate.team,
                    "opponent": candidate.opponent,
                },
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("NCAAF closing feed returned non-object JSON")
        return ClosingQuote(
            event_id=str(payload["event_id"]),
            team=str(payload["team"]),
            opponent=str(payload["opponent"]),
            selection_price_american=int(payload["selection_price_american"]),
            opposing_price_american=int(payload["opposing_price_american"]),
            retrieved_at=_aware(str(payload["retrieved_at"])),
            source=str(payload.get("source") or "CONFIGURED_READ_ONLY_FEED"),
        )


class SupabaseClosingStore:
    def __init__(self, client):
        self.client = client

    @staticmethod
    def _candidate(row: dict) -> ClosingCandidate:
        return ClosingCandidate(
            prediction_id=str(row["ncaaf_prediction_id"]),
            event_id=str(row["official_event_id"]),
            team=str(row["team"]),
            opponent=str(row["opponent"]),
            event_start_time=_aware(str(row["event_start_time"])),
            entry_no_vig=float(row["no_vig_probability"]) if row.get("no_vig_probability") is not None else None,
        )

    def _already_has_close_ids(self, prediction_ids: list[str]) -> set[str]:
        if not prediction_ids:
            return set()
        result = (
            self.client.table("wow_ncaaf_outcomes")
            .select("ncaaf_prediction_id,closing_snapshot_timestamp,clv_grade")
            .in_("ncaaf_prediction_id", prediction_ids)
            .execute()
        )
        return {
            str(row["ncaaf_prediction_id"])
            for row in (result.data or [])
            if row.get("closing_snapshot_timestamp") is not None or row.get("clv_grade") == CLVGrade.NO_CLOSE_AVAILABLE.value
        }

    def pending_pregame(self, *, as_of: datetime, window_minutes: int) -> list[ClosingCandidate]:
        cutoff = as_of + timedelta(minutes=window_minutes)
        result = (
            self.client.table("wow_ncaaf_predictions")
            .select("ncaaf_prediction_id,official_event_id,team,opponent,event_start_time,no_vig_probability")
            .gt("event_start_time", as_of.isoformat())
            .lte("event_start_time", cutoff.isoformat())
            .order("event_start_time")
            .execute()
        )
        return [self._candidate(row) for row in (result.data or [])]

    def unresolved_recent_started(self, *, as_of: datetime, lookback_minutes: int) -> list[ClosingCandidate]:
        lower = as_of - timedelta(minutes=lookback_minutes)
        result = (
            self.client.table("wow_ncaaf_predictions")
            .select("ncaaf_prediction_id,official_event_id,team,opponent,event_start_time,no_vig_probability")
            .gte("event_start_time", lower.isoformat())
            .lte("event_start_time", as_of.isoformat())
            .order("event_start_time")
            .execute()
        )
        rows = result.data or []
        already = self._already_has_close_ids([str(row["ncaaf_prediction_id"]) for row in rows])
        return [self._candidate(row) for row in rows if str(row["ncaaf_prediction_id"]) not in already]

    def write_close(self, candidate: ClosingCandidate, quote: ClosingQuote, closing_no_vig: float, clv_grade: CLVGrade) -> None:
        clv_value = None if candidate.entry_no_vig is None else closing_no_vig - candidate.entry_no_vig
        payload = {
            "ncaaf_prediction_id": candidate.prediction_id,
            "closing_price_american": quote.selection_price_american,
            "closing_opposing_price_american": quote.opposing_price_american,
            "closing_no_vig": closing_no_vig,
            "closing_snapshot_timestamp": quote.retrieved_at.isoformat(),
            "clv": clv_value,
            "clv_grade": clv_grade.value,
        }
        self.client.table("wow_ncaaf_outcomes").upsert(payload, on_conflict="ncaaf_prediction_id").execute()

    def mark_no_close(self, candidate: ClosingCandidate, *, as_of: datetime) -> None:
        payload = {
            "ncaaf_prediction_id": candidate.prediction_id,
            "closing_snapshot_timestamp": as_of.isoformat(),
            "clv": None,
            "clv_grade": CLVGrade.NO_CLOSE_AVAILABLE.value,
        }
        self.client.table("wow_ncaaf_outcomes").upsert(payload, on_conflict="ncaaf_prediction_id").execute()


def run_from_environment() -> CaptureResult:
    """Production scheduler entrypoint; fails closed on missing configuration."""
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    feed_url = os.getenv("WOW_NCAAF_MARKET_FEED_URL", "").strip()
    feed_token = os.getenv("WOW_NCAAF_MARKET_FEED_TOKEN", "").strip() or None
    if not supabase_url or not supabase_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY are required")
    if not feed_url:
        raise RuntimeError("WOW_NCAAF_MARKET_FEED_URL is required; synthetic closing lines are prohibited")

    from supabase import create_client

    store = SupabaseClosingStore(create_client(supabase_url, supabase_key))
    provider = HttpJsonClosingQuoteProvider(feed_url, token=feed_token)
    return capture_closing_lines(provider, store)


if __name__ == "__main__":
    result = run_from_environment()
    print({
        "status": result.status,
        "candidates_checked": result.candidates_checked,
        "quotes_captured": result.quotes_captured,
        "no_close_marked": result.no_close_marked,
        "provider_failures": result.provider_failures,
        "identity_failures": result.identity_failures,
        "stale_quote_failures": result.stale_quote_failures,
        "can_execute": False,
    })
