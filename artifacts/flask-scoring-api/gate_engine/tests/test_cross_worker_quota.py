"""
gate_engine/tests/test_cross_worker_quota.py

Cross-worker Odds API quota state regression tests (Task #67).

Simulates separate gunicorn worker/module instances by mocking the Postgres
layer so no live DB is required. Verifies:

  T-CWQ-01  Worker A writes → Worker B reads identical non-null values
  T-CWQ-02  Worker B reads the same quota_warning flag that Worker A set
  T-CWQ-03  Newer-wins: stale concurrent write does not overwrite a fresher row
  T-CWQ-04  Paid and free tiers are tracked independently
  T-CWQ-05  DB unavailable → process memory returned with data_source=process_memory_fallback
  T-CWQ-06  quota-status endpoint makes zero upstream Odds API calls
  T-CWQ-07  No API key value or fragment is stored in the persisted quota dict
  T-CWQ-08  request_cost is persisted and fetched via pg_odds_quota module
  T-CWQ-09  persist_quota_update returns False (not raises) on connection failure
  T-CWQ-10  fetch_quota_snapshot returns {} (not raises) on connection failure
  T-CWQ-11  data_source=postgres_cross_worker when Postgres row present
  T-CWQ-12  data_source=empty when both local and remote are empty
  T-CWQ-13  quota-status response always includes data_source and degraded fields
  T-CWQ-14  _odds_quota_update cost_str=None stores request_cost=None cleanly
  T-CWQ-15  _odds_quota_update cost_str non-numeric stores request_cost=None
"""
import sys
import os
import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers — pull helpers from app without re-importing
# ---------------------------------------------------------------------------

def _app():
    if "app" in sys.modules:
        return sys.modules["app"]
    import app as _a
    return _a


def _reset_store(mod):
    with mod._ODDS_QUOTA_LOCK:
        mod._ODDS_QUOTA_STORE.clear()


# ---------------------------------------------------------------------------
# T-CWQ-01 / T-CWQ-02  Worker A writes → Worker B reads
# ---------------------------------------------------------------------------

class TestCrossWorkerReadWrite:

    def setup_method(self):
        self.mod = _app()
        _reset_store(self.mod)

    def teardown_method(self):
        _reset_store(self.mod)

    def _make_remote_row(self, tier, remaining, used, warning, cost=None):
        """Build a fake Postgres row as fetch_quota_snapshot would return it."""
        return {
            "requests_remaining": remaining,
            "requests_used":      used,
            "quota_warning":      warning,
            "request_cost":       cost,
            "updated_at":         "2026-08-14T20:00:00Z",
            "source":             "postgres_cross_worker",
        }

    def test_worker_b_reads_values_written_by_worker_a(self):
        """T-CWQ-01: Worker B (empty local store) sees Worker A's postgres row."""
        remote = {"paid": self._make_remote_row("paid", 450, 50, False, 1.0)}

        with patch("gate_engine.pg_odds_quota.fetch_quota_snapshot", return_value=remote):
            # Temporarily clear PYTEST_CURRENT_TEST so cross-worker path executes
            with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": ""}, clear=False):
                os.environ.pop("PYTEST_CURRENT_TEST", None)
                snapshot, source = self.mod._odds_quota_snapshot_cross_worker()

        assert "paid" in snapshot
        assert snapshot["paid"]["requests_remaining"] == 450
        assert snapshot["paid"]["requests_used"] == 50

    def test_worker_b_reads_quota_warning_from_worker_a(self):
        """T-CWQ-02: quota_warning=True written by Worker A is visible to Worker B."""
        remote = {"paid": self._make_remote_row("paid", 10, 990, True)}

        with patch("gate_engine.pg_odds_quota.fetch_quota_snapshot", return_value=remote):
            with patch.dict(os.environ, {}, clear=False):
                env_bak = os.environ.pop("PYTEST_CURRENT_TEST", None)
                try:
                    snapshot, source = self.mod._odds_quota_snapshot_cross_worker()
                finally:
                    if env_bak is not None:
                        os.environ["PYTEST_CURRENT_TEST"] = env_bak

        assert snapshot["paid"]["quota_warning"] is True


# ---------------------------------------------------------------------------
# T-CWQ-03  Newer-wins UPSERT
# ---------------------------------------------------------------------------

class TestNewerWinsUpsert:

    def test_newer_wins_where_clause_present_in_sql(self):
        """T-CWQ-03: The UPSERT SQL must contain a newer-wins WHERE guard."""
        import gate_engine.pg_odds_quota as oq
        import inspect
        src = inspect.getsource(oq.persist_quota_update)
        assert "wow_odds_quota_state.updated_at <= EXCLUDED.updated_at" in src, (
            "persist_quota_update must have a newer-wins WHERE clause in the ON CONFLICT block"
        )

    def test_newer_wins_clause_is_in_on_conflict_block(self):
        """T-CWQ-03b: WHERE clause appears after ON CONFLICT DO UPDATE SET."""
        import gate_engine.pg_odds_quota as oq
        import inspect
        src = inspect.getsource(oq.persist_quota_update)
        on_conflict_idx = src.find("ON CONFLICT")
        where_idx = src.find("wow_odds_quota_state.updated_at <= EXCLUDED.updated_at")
        assert on_conflict_idx != -1
        assert where_idx > on_conflict_idx


# ---------------------------------------------------------------------------
# T-CWQ-04  Paid and free tiers independent
# ---------------------------------------------------------------------------

class TestTierIsolation:

    def setup_method(self):
        self.mod = _app()
        _reset_store(self.mod)

    def teardown_method(self):
        _reset_store(self.mod)

    def test_paid_and_free_tracked_independently_across_worker_boundary(self):
        """T-CWQ-04: Both tiers appear in merged snapshot from separate workers."""
        remote = {
            "paid": {
                "requests_remaining": 200, "requests_used": 800,
                "quota_warning": False, "request_cost": 2.0,
                "updated_at": "2026-08-14T20:01:00Z", "source": "postgres_cross_worker",
            },
            "free": {
                "requests_remaining": 5, "requests_used": 495,
                "quota_warning": True, "request_cost": 1.0,
                "updated_at": "2026-08-14T20:01:00Z", "source": "postgres_cross_worker",
            },
        }
        with patch("gate_engine.pg_odds_quota.fetch_quota_snapshot", return_value=remote):
            env_bak = os.environ.pop("PYTEST_CURRENT_TEST", None)
            try:
                snapshot, source = self.mod._odds_quota_snapshot_cross_worker()
            finally:
                if env_bak is not None:
                    os.environ["PYTEST_CURRENT_TEST"] = env_bak

        assert snapshot["paid"]["requests_remaining"] == 200
        assert snapshot["paid"]["quota_warning"] is False
        assert snapshot["free"]["requests_remaining"] == 5
        assert snapshot["free"]["quota_warning"] is True


# ---------------------------------------------------------------------------
# T-CWQ-05  DB unavailable → fallback with degraded flag
# ---------------------------------------------------------------------------

class TestDbUnavailableFallback:

    def setup_method(self):
        self.mod = _app()
        _reset_store(self.mod)

    def teardown_method(self):
        _reset_store(self.mod)

    def test_fallback_to_process_memory_when_db_unavailable(self):
        """T-CWQ-05: When Postgres returns {}, local store is used with fallback source."""
        # Seed local store
        self.mod._odds_quota_update("paid", "300", "700")
        with patch("gate_engine.pg_odds_quota.fetch_quota_snapshot", return_value={}):
            env_bak = os.environ.pop("PYTEST_CURRENT_TEST", None)
            try:
                snapshot, source = self.mod._odds_quota_snapshot_cross_worker()
            finally:
                if env_bak is not None:
                    os.environ["PYTEST_CURRENT_TEST"] = env_bak

        assert snapshot["paid"]["requests_remaining"] == 300
        assert source == "process_memory_fallback"

    def test_quota_status_endpoint_reports_degraded_when_db_unavailable(self):
        """T-CWQ-05b: degraded=true appears in endpoint response when DB is down."""
        import app as mod
        client = mod.app.test_client()
        mod.os.environ.setdefault("SCORING_API_KEY", "test-key-cwq")
        api_key = mod.os.environ.get("SCORING_API_KEY", "test-key-cwq")

        # Seed local so snapshot is non-empty (fallback path, not empty path)
        _reset_store(mod)
        mod._odds_quota_update("paid", "400", "600")

        with patch("gate_engine.pg_odds_quota.fetch_quota_snapshot", return_value={}):
            env_bak = os.environ.pop("PYTEST_CURRENT_TEST", None)
            try:
                resp = client.get(
                    "/wow/odds/quota-status",
                    headers={"X-API-Key": api_key},
                )
            finally:
                if env_bak is not None:
                    os.environ["PYTEST_CURRENT_TEST"] = env_bak

        _reset_store(mod)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["degraded"] is True
        assert data["data_source"] == "process_memory_fallback"


# ---------------------------------------------------------------------------
# T-CWQ-06  quota-status makes zero upstream Odds API calls
# ---------------------------------------------------------------------------

class TestQuotaStatusNoUpstreamCalls:

    def test_quota_status_makes_no_odds_api_requests(self):
        """T-CWQ-06: GET /wow/odds/quota-status must never call the upstream Odds API."""
        import app as mod
        client = mod.app.test_client()
        mod.os.environ.setdefault("SCORING_API_KEY", "test-key-cwq06")
        api_key = mod.os.environ.get("SCORING_API_KEY", "test-key-cwq06")

        with patch("requests.get") as mock_get:
            resp = client.get(
                "/wow/odds/quota-status",
                headers={"X-API-Key": api_key},
            )
        # requests.get should never have been called by the quota-status route
        for call in mock_get.call_args_list:
            url = call.args[0] if call.args else call.kwargs.get("url", "")
            assert "api.the-odds-api.com" not in str(url), (
                f"quota-status made an upstream Odds API call: {url}"
            )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# T-CWQ-07  No API key stored in persisted quota
# ---------------------------------------------------------------------------

class TestNoSecretPersistence:

    def test_persist_quota_update_args_contain_no_key_values(self):
        """T-CWQ-07: persist_quota_update is never called with API key strings."""
        import gate_engine.pg_odds_quota as oq
        captured = []

        def _fake_persist(tier, remaining, used, warning, cost=None, conn_string=None):
            captured.append({"tier": tier, "remaining": remaining,
                             "used": used, "warning": warning, "cost": cost})
            return True

        import app as mod
        _reset_store(mod)
        paid_key = os.environ.get("ODDS_API_PAID_KEY", "secret-paid-key-value")
        free_key  = os.environ.get("ODDS_API_FREE_KEY",  "secret-free-key-value")

        with patch("gate_engine.pg_odds_quota.persist_quota_update", side_effect=_fake_persist):
            env_bak = os.environ.pop("PYTEST_CURRENT_TEST", None)
            try:
                mod._odds_quota_update("paid", "100", "900", "1")
            finally:
                if env_bak is not None:
                    os.environ["PYTEST_CURRENT_TEST"] = env_bak
        _reset_store(mod)

        for record in captured:
            for v in record.values():
                assert paid_key not in str(v), "Paid API key fragment stored in quota record"
                assert free_key not in str(v), "Free API key fragment stored in quota record"


# ---------------------------------------------------------------------------
# T-CWQ-08  request_cost persisted and fetched
# ---------------------------------------------------------------------------

class TestRequestCostPersistence:

    def test_cost_str_stored_in_local_quota_store(self):
        """T-CWQ-08a: request_cost parsed from cost_str and stored in _ODDS_QUOTA_STORE."""
        import app as mod
        _reset_store(mod)
        mod._odds_quota_update("paid", "400", "600", "2")
        with mod._ODDS_QUOTA_LOCK:
            assert mod._ODDS_QUOTA_STORE["paid"]["request_cost"] == 2.0
        _reset_store(mod)

    def test_cost_included_in_persist_call(self):
        """T-CWQ-08b: persist_quota_update receives request_cost when provided."""
        import app as mod
        _reset_store(mod)
        captured_cost = []

        def _fake_persist(tier, remaining, used, warning, cost=None, conn_string=None):
            captured_cost.append(cost)
            return True

        with patch("gate_engine.pg_odds_quota.persist_quota_update", side_effect=_fake_persist):
            env_bak = os.environ.pop("PYTEST_CURRENT_TEST", None)
            try:
                mod._odds_quota_update("free", "100", "400", "3")
            finally:
                if env_bak is not None:
                    os.environ["PYTEST_CURRENT_TEST"] = env_bak
        _reset_store(mod)

        assert len(captured_cost) == 1
        assert captured_cost[0] == 3.0

    def test_request_cost_in_fetch_quota_snapshot_schema(self):
        """T-CWQ-08c: fetch_quota_snapshot SELECT includes request_cost column."""
        import gate_engine.pg_odds_quota as oq
        import inspect
        src = inspect.getsource(oq.fetch_quota_snapshot)
        assert "request_cost" in src


# ---------------------------------------------------------------------------
# T-CWQ-09 / T-CWQ-10  fail-open guarantees
# ---------------------------------------------------------------------------

class TestFailOpenGuarantees:

    def test_persist_fails_open_on_bad_dsn(self):
        """T-CWQ-09: persist_quota_update returns False, never raises, on bad DSN."""
        import gate_engine.pg_odds_quota as oq
        result = oq.persist_quota_update(
            "paid", 100, 900, False, 1.0,
            conn_string="postgresql://bad:bad@127.0.0.1:1/nope",
        )
        assert result is False

    def test_fetch_fails_open_on_bad_dsn(self):
        """T-CWQ-10: fetch_quota_snapshot returns {}, never raises, on bad DSN."""
        import gate_engine.pg_odds_quota as oq
        result = oq.fetch_quota_snapshot(
            conn_string="postgresql://bad:bad@127.0.0.1:1/nope"
        )
        assert result == {}

    def test_persist_fails_open_without_database_url(self, monkeypatch):
        """T-CWQ-09b: persist_quota_update returns False when DATABASE_URL missing."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        import gate_engine.pg_odds_quota as oq
        assert oq.persist_quota_update("paid", 50, 950, True) is False

    def test_fetch_fails_open_without_database_url(self, monkeypatch):
        """T-CWQ-10b: fetch_quota_snapshot returns {} when DATABASE_URL missing."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        import gate_engine.pg_odds_quota as oq
        assert oq.fetch_quota_snapshot() == {}


# ---------------------------------------------------------------------------
# T-CWQ-11 / T-CWQ-12  data_source values
# ---------------------------------------------------------------------------

class TestDataSourceField:

    def setup_method(self):
        self.mod = _app()
        _reset_store(self.mod)

    def teardown_method(self):
        _reset_store(self.mod)

    def _call_cross_worker(self, remote):
        with patch("gate_engine.pg_odds_quota.fetch_quota_snapshot", return_value=remote):
            env_bak = os.environ.pop("PYTEST_CURRENT_TEST", None)
            try:
                result = self.mod._odds_quota_snapshot_cross_worker()
            finally:
                if env_bak is not None:
                    os.environ["PYTEST_CURRENT_TEST"] = env_bak
        return result

    def test_data_source_postgres_when_remote_row_present(self):
        """T-CWQ-11: data_source='postgres_cross_worker' when DB contributed."""
        remote = {"paid": {
            "requests_remaining": 300, "requests_used": 700,
            "quota_warning": False, "request_cost": None,
            "updated_at": "2026-08-14T20:00:00Z", "source": "postgres_cross_worker",
        }}
        _, source = self._call_cross_worker(remote)
        assert source == "postgres_cross_worker"

    def test_data_source_empty_when_both_sources_empty(self):
        """T-CWQ-12: data_source='empty' when local store and remote are both empty."""
        _, source = self._call_cross_worker({})
        assert source == "empty"


# ---------------------------------------------------------------------------
# T-CWQ-13  quota-status response shape includes new fields
# ---------------------------------------------------------------------------

class TestQuotaStatusResponseShape:

    def setup_method(self):
        import app as mod
        self.mod = mod
        self.client = mod.app.test_client()
        mod.os.environ.setdefault("SCORING_API_KEY", "test-key-cwq13")
        self._key = mod.os.environ.get("SCORING_API_KEY", "test-key-cwq13")
        _reset_store(mod)

    def teardown_method(self):
        _reset_store(self.mod)

    def test_response_includes_data_source_and_degraded(self):
        """T-CWQ-13: quota-status always returns data_source and degraded fields."""
        resp = self.client.get(
            "/wow/odds/quota-status", headers={"X-API-Key": self._key}
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "data_source" in data, "data_source field missing from quota-status response"
        assert "degraded" in data, "degraded field missing from quota-status response"

    def test_degraded_false_when_empty(self):
        """T-CWQ-13b: degraded=False when store is empty (not a DB failure)."""
        resp = self.client.get(
            "/wow/odds/quota-status", headers={"X-API-Key": self._key}
        )
        data = resp.get_json()
        assert data["degraded"] is False
        assert data["data_source"] in ("process_memory_pytest", "empty")

    def test_existing_fields_preserved(self):
        """T-CWQ-13c: existing response shape fields are not removed."""
        resp = self.client.get(
            "/wow/odds/quota-status", headers={"X-API-Key": self._key}
        )
        data = resp.get_json()
        for field in ("quota_threshold", "quota_warning", "tiers", "note"):
            assert field in data, f"Required field '{field}' missing from response"


# ---------------------------------------------------------------------------
# T-CWQ-14 / T-CWQ-15  cost_str edge cases
# ---------------------------------------------------------------------------

class TestCostStrEdgeCases:

    def setup_method(self):
        self.mod = _app()
        _reset_store(self.mod)

    def teardown_method(self):
        _reset_store(self.mod)

    def test_cost_str_none_stores_none(self):
        """T-CWQ-14: cost_str=None → request_cost=None in store (no crash)."""
        self.mod._odds_quota_update("paid", "200", "800", None)
        with self.mod._ODDS_QUOTA_LOCK:
            assert self.mod._ODDS_QUOTA_STORE["paid"]["request_cost"] is None

    def test_cost_str_non_numeric_stores_none(self):
        """T-CWQ-15: Non-numeric cost_str (e.g. 'N/A') → request_cost=None."""
        self.mod._odds_quota_update("paid", "200", "800", "N/A")
        with self.mod._ODDS_QUOTA_LOCK:
            assert self.mod._ODDS_QUOTA_STORE["paid"]["request_cost"] is None
