from types import SimpleNamespace

from kalshi_weather_v2.market_microstructure_recorder import capture_market_microstructure_batch


class FakeQuery:
    def __init__(self, table):
        self.table = table
        self.filters = []
        self.insert_row = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def limit(self, _value):
        return self

    def insert(self, row):
        self.insert_row = dict(row)
        return self

    def execute(self):
        if self.insert_row is not None:
            key = self.insert_row["microstructure_snapshot_id"]
            self.table[key] = dict(self.insert_row)
            return SimpleNamespace(data=[dict(self.insert_row)])
        rows = list(self.table.values())
        for key, value in self.filters:
            rows = [row for row in rows if row.get(key) == value]
        return SimpleNamespace(data=[dict(row) for row in rows[:1]])


class FakeClient:
    def __init__(self):
        self.rows = {}

    def table(self, name):
        assert name == "wow_kalshi_weather_market_microstructure_snapshots"
        return FakeQuery(self.rows)


class FakeHttp:
    def __init__(self):
        self.closed = False

    def get_json(self, url, _headers):
        if url.endswith("/orderbook"):
            return {
                "orderbook_fp": {
                    "yes_dollars": [["0.61", "12.00"]],
                    "no_dollars": [["0.37", "8.00"]],
                }
            }
        return {
            "market": {
                "ticker": "KXTEMPMIAH-TEST",
                "event_ticker": "KXTEMPMIAH-EVENT",
                "status": "active",
                "updated_time": "2026-10-05T21:00:00Z",
                "volume_fp": "14.50",
                "open_interest_fp": "7.00",
            }
        }

    def close(self):
        self.closed = True


def test_recorder_persists_market_state_without_prediction_dependency():
    client = FakeClient()
    http = FakeHttp()
    result = capture_market_microstructure_batch(
        client=client,
        tickers=("KXTEMPMIAH-TEST",),
        retrieved_at="2026-10-05T21:01:00Z",
        series_by_ticker={"KXTEMPMIAH-TEST": "KXTEMPMIAH"},
        http=http,
    )

    assert result.attempted == 1
    assert result.written == 1
    assert result.failures == ()
    row = next(iter(client.rows.values()))
    assert row["prediction_id"] is None
    assert row["series_ticker"] == "KXTEMPMIAH"
    assert row["yes_best_bid"] == 0.61
    assert row["no_best_bid"] == 0.37
    assert row["yes_best_ask"] == 0.63
    assert row["no_best_ask"] == 0.39
    assert row["yes_bid_size"] == 12.0
    assert row["no_bid_size"] == 8.0
    assert row["yes_ask_size"] == 8.0
    assert row["no_ask_size"] == 12.0
    assert row["volume"] == 14.5
    assert row["open_interest"] == 7.0
    assert row["weather_probability_input_allowed"] is False
    assert row["can_execute"] is False
    assert http.closed is False


def test_recorder_failure_is_row_isolated():
    class PartialHttp(FakeHttp):
        def get_json(self, url, headers):
            if "BROKEN" in url:
                raise RuntimeError("provider failure")
            return super().get_json(url, headers)

    client = FakeClient()
    result = capture_market_microstructure_batch(
        client=client,
        tickers=("KXTEMPMIAH-TEST", "BROKEN"),
        retrieved_at="2026-10-05T21:01:00Z",
        http=PartialHttp(),
    )

    assert result.attempted == 2
    assert result.written == 1
    assert result.failures == ("BROKEN:RuntimeError",)
    assert len(client.rows) == 1


def test_recorder_empty_batch_is_clean_noop_without_http_contract():
    result = capture_market_microstructure_batch(
        client=FakeClient(),
        tickers=(),
        retrieved_at="2026-10-05T21:01:00Z",
        http=object(),
    )
    assert result.attempted == 0
    assert result.written == 0
    assert result.failures == ()
    assert result.can_execute is False
