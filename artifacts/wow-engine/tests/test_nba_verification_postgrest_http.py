"""Read-only, opt-in REAL PostgREST NBA verification prerequisite acceptance.

Requires explicit WOW_TEST_NBA_SCHEMA_STATE=old|ready and service credentials.
With no credentials: pytest skips. No migrations, INSERT, UPDATE, DELETE,
training, certification, or probability publication are attempted.
"""
from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import pytest

LEDGER = "wow_independent_verification_receipts"
CALIBRATOR = "wow_basketball_team_event_calibrators"
# Field names follow the canonical ledger (20261110_v17_independent_verification_receipts.sql),
# not the retired draft table: status and scope have different column names.
LEDGER_FIELDS = "receipt_id,independent_verification_status,can_execute,publishable_scope"
CALIBRATOR_FIELDS = "calibration_sha256,calibration_fit_end"
SCHEMA_STATE = "WOW_TEST_NBA_SCHEMA_STATE"
URL_ENV = "WOW_TEST_SUPABASE_URL"
KEY_ENV = "WOW_TEST_SUPABASE_SERVICE_KEY"


class NoRedirect(HTTPRedirectHandler):
    """Never forward privileged headers to another origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _project_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    hostname = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or not hostname.endswith(".supabase.co")
        or hostname == ".supabase.co"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise AssertionError("NBA_POSTGREST_PROJECT_URL_INVALID")
    return f"https://{hostname}"


def _headers(key: str) -> dict[str, str]:
    if not key or key.startswith("sb_publishable_"):
        raise AssertionError("NBA_POSTGREST_SERVICE_CREDENTIAL_MISSING_OR_UNSAFE")
    out = {"apikey": key, "Accept": "application/json"}
    if not key.startswith("sb_secret_"):
        # Only legacy service_role JWT keys use Bearer auth.
        out["Authorization"] = "Bearer " + key
    return out


def _read_only_select(
    base_url: str, key: str, table: str, columns: str, *, opener=None
) -> tuple[int, str | None, object]:
    base_url = _project_url(base_url)
    if not re.fullmatch(r"[a-z][a-z0-9_]*", table):
        raise AssertionError("NBA_POSTGREST_TABLE_INVALID")
    if not re.fullmatch(r"[a-z_]+(?:,[a-z_]+)*", columns):
        raise AssertionError("NBA_POSTGREST_FIELDS_INVALID")
    url = f"{base_url}/rest/v1/{table}?" + urlencode(
        {"select": columns, "limit": 1}
    )
    req = Request(url, headers=_headers(key), method="GET")
    client = opener if opener is not None else build_opener(NoRedirect())
    try:
        with client.open(req, timeout=15) as response:
            status = int(response.status)
            payload = response.read(4096)
    except HTTPError as error:
        status = error.code
        payload = error.read(4096)
    except (URLError, OSError, ValueError):
        # Do not expose URLs, request headers, raw DB messages, or secrets.
        raise AssertionError("NBA_POSTGREST_HTTP_TRANSPORT_FAILED") from None
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise AssertionError("NBA_POSTGREST_RESPONSE_NOT_JSON") from None
    code = data.get("code") if type(data) is dict else None
    code = code if type(code) is str and re.fullmatch(
        r"(?:[0-9A-Z]{5}|PGRST[0-9]{3})", code
    ) else None
    return status, code, data if status == 200 else None


def _require_old_schema(ledger, calibrator):
    ledger_status, ledger_code, _ = ledger
    cal_status, cal_code, _ = calibrator
    assert ledger_status in (400, 404) and ledger_code in (
        "42P01", "PGRST205"
    ), "NBA_VERIFICATION_LEDGER_MISSING_NOT_TYPED_AS_EXPECTED"
    assert cal_status in (400, 404) and cal_code in (
        "42703", "PGRST204"
    ), "NBA_VERIFICATION_CALIBRATOR_EVIDENCE_MISSING_NOT_TYPED_AS_EXPECTED"


def _require_ready_schema(ledger, calibrator):
    for status, code, data in (ledger, calibrator):
        assert status == 200 and code is None and type(data) is list, (
            "NBA_VERIFICATION_READY_SCHEMA_HTTP_READBACK_FAILED"
        )
    for row in ledger[2]:
        assert type(row) is dict
        assert row.get("can_execute") is False
        assert row.get("publishable_scope") == "NONE"


@pytest.mark.parametrize("state", ["old", "ready"])
def test_state_assertions_reject_wrong_payloads(state):
    ready = (200, None, [])
    old_ledger = (404, "PGRST205", None)
    old_cal = (400, "42703", None)
    if state == "old":
        _require_old_schema(old_ledger, old_cal)
        with pytest.raises(AssertionError):
            _require_old_schema(ready, ready)
    else:
        _require_ready_schema(ready, ready)
        with pytest.raises(AssertionError):
            _require_ready_schema(old_ledger, old_cal)


def test_service_key_header_selection_and_no_redirect():
    assert "Authorization" not in _headers("sb_secret_test")
    assert _headers("legacy-test")["Authorization"] == "Bearer legacy-test"
    with pytest.raises(AssertionError):
        _headers("sb_publishable_test")
    assert NoRedirect().redirect_request(None, None, 302, None, {}, "https://example.com") is None


def test_real_supabase_postgrest_prerequisite_state_read_only():
    state = os.getenv(SCHEMA_STATE, "").strip().lower()
    base = os.getenv(URL_ENV, "").strip()
    key = os.getenv(KEY_ENV, "").strip()
    if not (state and base and key):
        pytest.skip(
            "Set WOW_TEST_NBA_SCHEMA_STATE, WOW_TEST_SUPABASE_URL, "
            "WOW_TEST_SUPABASE_SERVICE_KEY for a real read-only HTTP test."
        )
    assert state in ("old", "ready"), "NBA_POSTGREST_EXPECTED_STATE_INVALID"
    ledger = _read_only_select(base, key, LEDGER, LEDGER_FIELDS)
    calibrator = _read_only_select(base, key, CALIBRATOR, CALIBRATOR_FIELDS)
    if state == "old":
        _require_old_schema(ledger, calibrator)
    else:
        _require_ready_schema(ledger, calibrator)
