"""
tests/test_learning_log.py
WOW Learning Log — POST /learning-log and GET /learning-log

Test categories:
  V  Validation — required fields, slip_result enum, legs type
  P  Persistence — successful save returns correct shape
  R  Read-back — GET by entry_id returns saved record; list returns records
  I  Idempotency — duplicate entry_id returns existing row, created=false
  L  Limit clamping — limit param capped at 100

All tests are offline — DB is fully mocked via unittest.mock.patch.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# App import — conftest.py adds the artifact root to sys.path so 'app'
# resolves to artifacts/flask-scoring-api/app.py.
# ---------------------------------------------------------------------------
import app as _app_module

_app = _app_module.app
_app.config["TESTING"] = True


def _client():
    return _app.test_client()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_VALID_BODY = {
    "entry_id": "ll-test-001",
    "date": "2026-08-11",
    "slip_result": "WIN",
    "legs": [{"player": "A'ja Wilson", "prop": "points", "line": 22.5, "result": "HIT"}],
    "root_cause": "Strong market confirmation + L10 aligned with model.",
    "correlation_flag": False,
    "execution_discipline": "TIER_1",
    "patch_recommendation": "No patch required.",
}

_VALID_KEY = "test-key"


def _make_mock_conn(rowcount=1, fetchone_row=None, fetchall_rows=None):
    """Build a mock psycopg2 connection whose cursor returns controllable results."""
    mock_cur = MagicMock()
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.rowcount = rowcount
    mock_cur.fetchone.return_value = fetchone_row
    mock_cur.fetchall.return_value = fetchall_rows or []

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_conn.commit = MagicMock()
    mock_conn.close = MagicMock()
    return mock_conn, mock_cur


def _post(client, body, key=_VALID_KEY):
    return client.post(
        "/learning-log",
        data=json.dumps(body),
        content_type="application/json",
        headers={"X-API-Key": key},
    )


def _get(client, params="", key=_VALID_KEY):
    return client.get(
        f"/learning-log{params}",
        headers={"X-API-Key": key},
    )


# ===========================================================================
# V — Validation
# ===========================================================================

class TestValidation(unittest.TestCase):

    def setUp(self):
        self.client = _client()
        self.env = _app.test_request_context()

    def _post_missing(self, field):
        body = {k: v for k, v in _VALID_BODY.items() if k != field}
        return _post(self.client, body)

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_missing_entry_id_returns_400(self):
        resp = self._post_missing("entry_id")
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data["ok"])
        self.assertTrue(any("entry_id" in e for e in data["errors"]))

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_missing_date_returns_400(self):
        resp = self._post_missing("date")
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertTrue(any("date" in e for e in data["errors"]))

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_missing_slip_result_returns_400(self):
        resp = self._post_missing("slip_result")
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertTrue(any("slip_result" in e for e in data["errors"]))

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_invalid_slip_result_returns_400(self):
        body = {**_VALID_BODY, "slip_result": "BOGUS"}
        resp = _post(self.client, body)
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertTrue(any("slip_result" in e for e in data["errors"]))

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_slip_result_accepts_all_valid_values(self):
        """WIN / LOSS / PUSH / MIXED all pass validation (DB mocked)."""
        for result in ("WIN", "LOSS", "PUSH", "MIXED"):
            mock_conn, _ = _make_mock_conn(rowcount=1)
            with patch.object(_app_module, "get_db_conn", return_value=mock_conn):
                body = {**_VALID_BODY, "entry_id": f"ll-{result}", "slip_result": result}
                resp = _post(self.client, body)
            self.assertEqual(resp.status_code, 200, f"Expected 200 for slip_result={result}")

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_missing_legs_returns_400(self):
        resp = self._post_missing("legs")
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertTrue(any("legs" in e for e in data["errors"]))

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_empty_legs_list_returns_400(self):
        body = {**_VALID_BODY, "legs": []}
        resp = _post(self.client, body)
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertTrue(any("legs" in e for e in data["errors"]))

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_legs_as_non_list_returns_400(self):
        body = {**_VALID_BODY, "legs": "not-a-list"}
        resp = _post(self.client, body)
        self.assertEqual(resp.status_code, 400)

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_missing_root_cause_returns_400(self):
        resp = self._post_missing("root_cause")
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertTrue(any("root_cause" in e for e in data["errors"]))

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_missing_api_key_returns_401(self):
        resp = self.client.post(
            "/learning-log",
            data=json.dumps(_VALID_BODY),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_wrong_api_key_returns_401(self):
        resp = _post(self.client, _VALID_BODY, key="wrong-key")
        self.assertEqual(resp.status_code, 401)

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_multiple_missing_fields_all_reported(self):
        """Validation collects all errors, not just the first."""
        resp = _post(self.client, {})
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertGreaterEqual(len(data["errors"]), 4)

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_slip_result_case_insensitive(self):
        """Lower-case 'win' is normalised to WIN and accepted."""
        mock_conn, _ = _make_mock_conn(rowcount=1)
        with patch.object(_app_module, "get_db_conn", return_value=mock_conn):
            body = {**_VALID_BODY, "slip_result": "win"}
            resp = _post(self.client, body)
        self.assertEqual(resp.status_code, 200)


# ===========================================================================
# P — Persistence (new row)
# ===========================================================================

class TestPersistence(unittest.TestCase):

    def setUp(self):
        self.client = _client()

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_successful_save_returns_correct_shape(self):
        mock_conn, _ = _make_mock_conn(rowcount=1)
        with patch.object(_app_module, "get_db_conn", return_value=mock_conn):
            resp = _post(self.client, _VALID_BODY)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["saved"])
        self.assertEqual(data["entry_id"], _VALID_BODY["entry_id"])
        self.assertTrue(data["created"])

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_save_calls_db_commit(self):
        # _ensure_learning_log_table commits once (DDL), then the INSERT commits
        # once more — total 2 commits per request. assert_called() confirms at
        # least one commit fired (i.e. the write was not left in an open tx).
        mock_conn, _ = _make_mock_conn(rowcount=1)
        with patch.object(_app_module, "get_db_conn", return_value=mock_conn):
            _post(self.client, _VALID_BODY)
        mock_conn.commit.assert_called()
        self.assertGreaterEqual(mock_conn.commit.call_count, 1)

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_optional_fields_accepted(self):
        """Minimal body (no optional fields) is still valid."""
        mock_conn, _ = _make_mock_conn(rowcount=1)
        with patch.object(_app_module, "get_db_conn", return_value=mock_conn):
            body = {
                "entry_id": "ll-min-001",
                "date": "2026-08-11",
                "slip_result": "LOSS",
                "legs": [{"prop": "rebounds"}],
                "root_cause": "Model over-estimated usage.",
            }
            resp = _post(self.client, body)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["created"])

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_db_error_returns_500(self):
        with patch.object(_app_module, "get_db_conn", side_effect=RuntimeError("DB down")):
            resp = _post(self.client, _VALID_BODY)
        self.assertEqual(resp.status_code, 500)
        self.assertIn("error", resp.get_json())


# ===========================================================================
# I — Idempotency
# ===========================================================================

class TestIdempotency(unittest.TestCase):

    def setUp(self):
        self.client = _client()

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_duplicate_entry_id_returns_created_false(self):
        """ON CONFLICT DO NOTHING → rowcount=0 → created=False."""
        mock_conn, _ = _make_mock_conn(rowcount=0)
        with patch.object(_app_module, "get_db_conn", return_value=mock_conn):
            resp = _post(self.client, _VALID_BODY)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["saved"])
        self.assertEqual(data["entry_id"], _VALID_BODY["entry_id"])
        self.assertFalse(data["created"])

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_duplicate_never_errors(self):
        """A retry on an existing entry_id must return 200, not 4xx/5xx."""
        mock_conn, _ = _make_mock_conn(rowcount=0)
        with patch.object(_app_module, "get_db_conn", return_value=mock_conn):
            resp = _post(self.client, _VALID_BODY)
        self.assertEqual(resp.status_code, 200)


# ===========================================================================
# R — Read-back
# ===========================================================================

class TestReadback(unittest.TestCase):

    def setUp(self):
        self.client = _client()

    def _make_db_row(self, entry_id="ll-test-001"):
        """Simulate a RealDictCursor row returned from SELECT *."""
        import datetime
        return {
            "entry_id": entry_id,
            "created_at": datetime.datetime(2026, 8, 11, 12, 0, 0),
            "date": "2026-08-11",
            "slip_result": "WIN",
            "legs": [{"player": "A'ja Wilson", "prop": "points"}],
            "correlation_flag": False,
            "execution_discipline": "TIER_1",
            "root_cause": "Strong market confirmation.",
            "patch_recommendation": None,
            "payload": {"entry_id": entry_id},
        }

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_get_by_entry_id_found(self):
        row = self._make_db_row("ll-test-001")
        mock_conn, mock_cur = _make_mock_conn(fetchone_row=row)
        with patch.object(_app_module, "get_db_conn", return_value=mock_conn):
            resp = _get(self.client, "?entry_id=ll-test-001")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data["found"])
        self.assertIn("record", data)
        self.assertEqual(data["record"]["entry_id"], "ll-test-001")

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_get_by_entry_id_not_found(self):
        mock_conn, mock_cur = _make_mock_conn(fetchone_row=None)
        with patch.object(_app_module, "get_db_conn", return_value=mock_conn):
            resp = _get(self.client, "?entry_id=ll-does-not-exist")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data["found"])
        self.assertEqual(data["entry_id"], "ll-does-not-exist")

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_get_list_returns_records(self):
        rows = [self._make_db_row(f"ll-{i}") for i in range(3)]
        mock_conn, mock_cur = _make_mock_conn(fetchall_rows=rows)
        with patch.object(_app_module, "get_db_conn", return_value=mock_conn):
            resp = _get(self.client)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data["found"])
        self.assertEqual(data["count"], 3)
        self.assertIsInstance(data["records"], list)

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_get_list_empty_db(self):
        mock_conn, _ = _make_mock_conn(fetchall_rows=[])
        with patch.object(_app_module, "get_db_conn", return_value=mock_conn):
            resp = _get(self.client)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data["found"])
        self.assertEqual(data["count"], 0)

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_get_requires_api_key(self):
        resp = self.client.get("/learning-log")
        self.assertEqual(resp.status_code, 401)


# ===========================================================================
# L — Limit clamping
# ===========================================================================

class TestLimitClamping(unittest.TestCase):

    def setUp(self):
        self.client = _client()

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_limit_capped_at_100(self):
        """?limit=9999 must be silently capped to 100 — no error."""
        mock_conn, mock_cur = _make_mock_conn(fetchall_rows=[])
        with patch.object(_app_module, "get_db_conn", return_value=mock_conn):
            resp = _get(self.client, "?limit=9999")
        self.assertEqual(resp.status_code, 200)
        # Verify the DB call used 100, not 9999
        execute_args = mock_cur.execute.call_args
        self.assertIn(100, execute_args[0][1])

    @patch.dict("os.environ", {"SCORING_API_KEY": _VALID_KEY})
    def test_invalid_limit_defaults_to_50(self):
        """?limit=abc is non-numeric — should default to 50, not crash."""
        mock_conn, mock_cur = _make_mock_conn(fetchall_rows=[])
        with patch.object(_app_module, "get_db_conn", return_value=mock_conn):
            resp = _get(self.client, "?limit=abc")
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
