from datetime import datetime, timedelta, timezone

from ncaaf_closing_capture import (
    ClosingCandidate,
    ClosingQuote,
    capture_closing_lines,
)
from ncaaf_trust import CLVGrade


class FakeProvider:
    def __init__(self, quote=None, error=None):
        self.quote = quote
        self.error = error

    def fetch(self, candidate):
        if self.error:
            raise self.error
        return self.quote


class FakeStore:
    def __init__(self, pending=None, started=None):
        self.pending = list(pending or [])
        self.started = list(started or [])
        self.writes = []
        self.no_close = []

    def pending_pregame(self, *, as_of, window_minutes):
        return self.pending

    def unresolved_recent_started(self, *, as_of, lookback_minutes):
        return self.started

    def write_close(self, candidate, quote, closing_no_vig, clv_grade):
        self.writes.append((candidate, quote, closing_no_vig, clv_grade))

    def mark_no_close(self, candidate, *, as_of):
        self.no_close.append((candidate, as_of))


def _candidate(now, *, entry_no_vig=0.55):
    return ClosingCandidate(
        prediction_id="pred-1",
        event_id="NCAAF-1",
        team="Alpha State",
        opponent="Beta Tech",
        event_start_time=now + timedelta(minutes=8),
        entry_no_vig=entry_no_vig,
    )


def _quote(now, **overrides):
    payload = dict(
        event_id="NCAAF-1",
        team="Alpha State",
        opponent="Beta Tech",
        selection_price_american=-150,
        opposing_price_american=130,
        retrieved_at=now - timedelta(minutes=1),
        source="TEST_READ_ONLY_FEED",
    )
    payload.update(overrides)
    return ClosingQuote(**payload)


def test_capture_exact_fresh_close_and_grade_clv():
    now = datetime.now(timezone.utc)
    candidate = _candidate(now, entry_no_vig=0.50)
    store = FakeStore(pending=[candidate])
    result = capture_closing_lines(FakeProvider(_quote(now)), store, as_of=now)
    assert result.status == "PASS"
    assert result.quotes_captured == 1
    assert result.can_execute is False
    assert len(store.writes) == 1
    _, _, closing_no_vig, grade = store.writes[0]
    assert 0 < closing_no_vig < 1
    assert grade == CLVGrade.BEAT_CLOSE


def test_identity_mismatch_fails_closed_without_write():
    now = datetime.now(timezone.utc)
    candidate = _candidate(now)
    store = FakeStore(pending=[candidate])
    result = capture_closing_lines(
        FakeProvider(_quote(now, event_id="WRONG-EVENT")),
        store,
        as_of=now,
    )
    assert result.status == "PARTIAL"
    assert result.identity_failures == 1
    assert result.quotes_captured == 0
    assert store.writes == []


def test_stale_quote_fails_closed_without_write():
    now = datetime.now(timezone.utc)
    candidate = _candidate(now)
    store = FakeStore(pending=[candidate])
    result = capture_closing_lines(
        FakeProvider(_quote(now, retrieved_at=now - timedelta(minutes=6))),
        store,
        as_of=now,
        max_quote_age_minutes=5,
    )
    assert result.status == "PARTIAL"
    assert result.stale_quote_failures == 1
    assert result.quotes_captured == 0


def test_post_start_quote_is_rejected():
    now = datetime.now(timezone.utc)
    candidate = _candidate(now)
    store = FakeStore(pending=[candidate])
    result = capture_closing_lines(
        FakeProvider(_quote(now, retrieved_at=candidate.event_start_time + timedelta(seconds=1))),
        store,
        as_of=candidate.event_start_time + timedelta(seconds=2),
        max_quote_age_minutes=5,
    )
    assert result.identity_failures == 1
    assert result.quotes_captured == 0


def test_provider_failure_is_visible_not_synthetic():
    now = datetime.now(timezone.utc)
    store = FakeStore(pending=[_candidate(now)])
    result = capture_closing_lines(FakeProvider(error=RuntimeError("feed down")), store, as_of=now)
    assert result.provider_failures == 1
    assert result.quotes_captured == 0
    assert store.writes == []


def test_started_row_without_close_is_marked_no_close():
    now = datetime.now(timezone.utc)
    started = ClosingCandidate(
        prediction_id="pred-2",
        event_id="NCAAF-2",
        team="Gamma",
        opponent="Delta",
        event_start_time=now - timedelta(minutes=2),
        entry_no_vig=0.52,
    )
    store = FakeStore(started=[started])
    result = capture_closing_lines(FakeProvider(error=RuntimeError("unused")), store, as_of=now)
    assert result.no_close_marked == 1
    assert len(store.no_close) == 1
    assert result.can_execute is False
