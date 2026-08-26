"""
Tests for the Odds API quota tracking helpers introduced in task #64.
Tests the _odds_quota_update / _odds_quota_snapshot helpers by importing
them directly from app — no live network calls made.
"""
import importlib
import sys
import pytest


# ---------------------------------------------------------------------------
# Import helpers directly from app without triggering the full Flask startup
# ---------------------------------------------------------------------------

def _get_quota_helpers():
    """
    Return (_odds_quota_update, _odds_quota_snapshot, _ODDS_QUOTA_THRESHOLD,
            _ODDS_QUOTA_STORE, _ODDS_QUOTA_LOCK) from app, resetting the store
    to a clean state first.
    """
    # Use already-imported app module if present (avoids re-import cost)
    if "app" in sys.modules:
        mod = sys.modules["app"]
    else:
        import app as mod  # noqa: F401

    # Reset the store to a known-empty state before each test group
    with mod._ODDS_QUOTA_LOCK:
        mod._ODDS_QUOTA_STORE.clear()

    return (
        mod._odds_quota_update,
        mod._odds_quota_snapshot,
        mod._ODDS_QUOTA_THRESHOLD,
        mod._ODDS_QUOTA_STORE,
        mod._ODDS_QUOTA_LOCK,
    )


# ---------------------------------------------------------------------------
# _odds_quota_update
# ---------------------------------------------------------------------------

class TestOddsQuotaUpdate:

    def setup_method(self):
        (
            self.update,
            self.snapshot,
            self.threshold,
            self.store,
            self.lock,
        ) = _get_quota_helpers()

    def test_above_threshold_no_warning(self):
        warning = self.update("paid", "500", "100")
        assert warning is False
        with self.lock:
            assert self.store["paid"]["quota_warning"] is False
            assert self.store["paid"]["requests_remaining"] == 500

    def test_exactly_at_threshold_no_warning(self):
        warning = self.update("paid", str(self.threshold), "50")
        assert warning is False

    def test_one_below_threshold_warning(self):
        warning = self.update("paid", str(self.threshold - 1), "951")
        assert warning is True
        with self.lock:
            assert self.store["paid"]["quota_warning"] is True

    def test_zero_remaining_warning(self):
        warning = self.update("free", "0", "500")
        assert warning is True

    def test_none_remaining_no_warning(self):
        # Header missing — cannot assess, treated as no warning
        warning = self.update("paid", None, None)
        assert warning is False
        with self.lock:
            assert self.store["paid"]["requests_remaining"] is None

    def test_non_numeric_remaining_no_warning(self):
        warning = self.update("paid", "N/A", "50")
        assert warning is False

    def test_free_and_paid_tracked_separately(self):
        self.update("paid", "200", "800")
        self.update("free", "10", "490")
        with self.lock:
            assert self.store["paid"]["requests_remaining"] == 200
            assert self.store["free"]["requests_remaining"] == 10
            assert self.store["free"]["quota_warning"] is True
            assert self.store["paid"]["quota_warning"] is False

    def test_updated_at_field_present(self):
        self.update("paid", "100", "900")
        with self.lock:
            assert "updated_at" in self.store["paid"]
            assert self.store["paid"]["updated_at"].endswith("Z")

    def test_subsequent_update_overwrites(self):
        self.update("paid", "100", "900")
        self.update("paid", "30", "970")
        with self.lock:
            assert self.store["paid"]["requests_remaining"] == 30
            assert self.store["paid"]["quota_warning"] is True

    def test_requests_used_stored(self):
        self.update("paid", "400", "600")
        with self.lock:
            assert self.store["paid"]["requests_used"] == 600


# ---------------------------------------------------------------------------
# _odds_quota_snapshot
# ---------------------------------------------------------------------------

class TestOddsQuotaSnapshot:

    def setup_method(self):
        (
            self.update,
            self.snapshot,
            self.threshold,
            self.store,
            self.lock,
        ) = _get_quota_helpers()

    def test_empty_store_returns_empty_dict(self):
        snap = self.snapshot()
        assert snap == {}

    def test_snapshot_is_a_copy(self):
        self.update("paid", "200", "800")
        snap = self.snapshot()
        snap["paid"]["requests_remaining"] = 9999
        with self.lock:
            assert self.store["paid"]["requests_remaining"] == 200

    def test_snapshot_contains_both_tiers(self):
        self.update("paid", "400", "600")
        self.update("free", "20", "480")
        snap = self.snapshot()
        assert "paid" in snap
        assert "free" in snap

    def test_snapshot_quota_warning_propagated(self):
        self.update("paid", "5", "995")
        snap = self.snapshot()
        assert snap["paid"]["quota_warning"] is True


# ---------------------------------------------------------------------------
# quota-status endpoint (Flask test client)
# ---------------------------------------------------------------------------

class TestQuotaStatusEndpoint:

    @pytest.fixture(autouse=True)
    def setup(self):
        import app as mod
        self.mod = mod
        self.client = mod.app.test_client()
        # Auth migrated to X-API-Key / SCORING_API_KEY (2026-08-14)
        mod.os.environ.setdefault("SCORING_API_KEY", "test-scoring-key-fallback")
        self._api_key = mod.os.environ.get("SCORING_API_KEY", "test-scoring-key-fallback")
        # Reset store
        with mod._ODDS_QUOTA_LOCK:
            mod._ODDS_QUOTA_STORE.clear()
        yield
        with mod._ODDS_QUOTA_LOCK:
            mod._ODDS_QUOTA_STORE.clear()

    def _headers(self):
        # Routes now use @require_api_key (X-API-Key / SCORING_API_KEY)
        return {"X-API-Key": self._api_key}

    def test_requires_auth(self):
        resp = self.client.get("/wow/odds/quota-status")
        assert resp.status_code == 401

    def test_empty_store_returns_200(self):
        resp = self.client.get("/wow/odds/quota-status", headers=self._headers())
        assert resp.status_code == 200
        data = resp.get_json()
        assert "quota_threshold" in data
        assert "quota_warning" in data
        assert "tiers" in data
        assert data["tiers"] == {}

    def test_populated_store_reflected(self):
        self.mod._odds_quota_update("paid", "30", "970")
        resp = self.client.get("/wow/odds/quota-status", headers=self._headers())
        data = resp.get_json()
        assert data["quota_warning"] is True
        assert data["tiers"]["paid"]["quota_warning"] is True
        assert data["tiers"]["paid"]["requests_remaining"] == 30

    def test_no_warning_when_above_threshold(self):
        self.mod._odds_quota_update("paid", "500", "500")
        resp = self.client.get("/wow/odds/quota-status", headers=self._headers())
        data = resp.get_json()
        assert data["quota_warning"] is False

    def test_note_field_present(self):
        resp = self.client.get("/wow/odds/quota-status", headers=self._headers())
        data = resp.get_json()
        assert "note" in data
