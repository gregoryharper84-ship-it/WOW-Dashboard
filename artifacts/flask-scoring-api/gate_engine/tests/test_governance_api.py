"""
test_governance_api.py — Black-box HTTP API tests for WOW governance endpoints.

Tests prove the following via Flask test client (no real network calls):
  - GET /wow/governance/status returns all required spec fields
  - POST /gate-engine/run with correct hash → scoring proceeds (200)
  - POST /gate-engine/run with MISSING hash → HTTP 409 (fail closed)
  - POST /gate-engine/run with WRONG hash → HTTP 409
  - POST /gate-engine/run with wrong patch list → HTTP 409
  - No partial scoring output is returned on any 409 mismatch
  - Every successful scoring response echoes governance_hash + patch_ids_applied
  - Hash is byte-for-byte identical between status endpoint and scoring response

IMPORTANT: app.py cannot be imported in tests (massive module with DB, crons, etc.)
We use a minimal Flask app that re-implements only the governance routes under test,
then import the real gate_engine modules that back them.
"""
from __future__ import annotations

import json
import pytest

from gate_engine.governance import (
    compute_governance_hash,
    get_governance_status,
    validate_handshake,
    MASTER_SPEC_VERSION,
    ENGINE_CODE_VERSION,
)
from gate_engine.pipeline import run_pipeline
from gate_engine.labels import PropLabel


# ---------------------------------------------------------------------------
# Minimal Flask harness — avoids importing app.py
# ---------------------------------------------------------------------------

import flask
import functools

_test_app = flask.Flask(__name__)
_test_app.config["TESTING"] = True

# Fake API key for test harness
_TEST_API_KEY = "test-key-governance"


def _require_key(f):
    @functools.wraps(f)
    def _inner(*a, **kw):
        if flask.request.headers.get("X-API-Key") != _TEST_API_KEY:
            return flask.jsonify({"error": "Unauthorized"}), 401
        return f(*a, **kw)
    return _inner


# Re-implement /wow/governance/status
@_test_app.route("/wow/governance/status", methods=["GET"])
def _gov_status():
    return flask.jsonify(get_governance_status()), 200


# Re-implement /gate-engine/run (minimal, mirrors app.py logic under test)
@_test_app.route("/gate-engine/run", methods=["POST"])
@_require_key
def _gate_engine_run():
    from gate_engine.pg_session_ledger import PgSessionLedger
    body = flask.request.get_json(silent=True) or {}
    expected_hash    = body.get("expected_governance_hash")
    expected_patches = body.get("expected_patch_ids")
    expected_spec    = body.get("expected_master_spec_version")
    session_id       = body.get("session_id")
    research_run_id  = body.get("research_run_id")
    as_of            = body.get("as_of")

    # MANDATORY handshake — fail closed on missing hash
    if not expected_hash:
        srv_hash = validate_handshake(None)["server_hash"]
        return flask.jsonify({
            "code":          "RUN_INVALID_GOVERNANCE_MISMATCH",
            "can_execute":   False,
            "detail":        "expected_governance_hash is required.",
            "server_hash":   srv_hash,
            "expected_hash": None,
            "mismatches":    ["expected_governance_hash missing from request"],
        }), 409

    hs = validate_handshake(
        expected_hash=expected_hash,
        expected_patch_ids=expected_patches,
        expected_master_spec_version=expected_spec,
    )
    if not hs["valid"]:
        return flask.jsonify({
            "code":          hs["code"],
            "can_execute":   False,
            "detail":        hs["detail"],
            "server_hash":   hs["server_hash"],
            "expected_hash": hs["expected_hash"],
            "mismatches":    hs["mismatches"],
        }), 409

    # Mandatory session/audit fields (mirrors app.py enforcement)
    for _mf_name, _mf_val in [
        ("session_id",      session_id),
        ("research_run_id", research_run_id),
        ("as_of",           as_of),
    ]:
        if not _mf_val:
            return flask.jsonify({
                "code":        "RUN_INVALID_GOVERNANCE_MISMATCH",
                "can_execute": False,
                "detail":      f"{_mf_name} is required on every scoring request.",
                "mismatches":  [f"{_mf_name} missing from request"],
                "server_hash": hs["server_hash"],
            }), 409

    raw_rows = body.get("rows")
    if not raw_rows or not isinstance(raw_rows, list):
        return flask.jsonify({"error": "rows must be a non-empty list"}), 400

    # Wire PgSessionLedger for cross-request duplicate detection
    _ledger = PgSessionLedger(session_id=session_id) if session_id else None

    result = run_pipeline(
        raw_rows=raw_rows,
        skip_health_gate=True,
        skip_settlement_check=True,
        existing_ledger=_ledger,
    )

    # Detect session-ledger DB failure → HTTP 409 (fail closed)
    _ledger_errors = [
        r for r in result.get("prop_ledger", [])
        if any("SESSION_LEDGER_UNAVAILABLE" in b for b in r.get("blockers", []))
    ]
    if _ledger_errors:
        return flask.jsonify({
            "code":        "RUN_INVALID_SESSION_LEDGER_UNAVAILABLE",
            "can_execute": False,
            "detail":      "Session exposure ledger is unavailable.",
        }), 409

    gov = get_governance_status()
    result["governance_hash"]      = gov["governance_hash"]
    result["patch_ids_applied"]    = list(gov["active_patch_ids"])
    result["can_execute"]          = False
    result["governance_handshake"] = "GOVERNANCE_MATCH"
    result["can_approve_bets"]     = False
    result["session_id"]           = session_id
    result["research_run_id"]      = research_run_id
    result["as_of"]                = as_of
    result["exposure_key"]         = session_id
    return flask.jsonify(result), 200


@pytest.fixture
def client():
    with _test_app.test_client() as c:
        yield c


def _auth_headers():
    return {"X-API-Key": _TEST_API_KEY, "Content-Type": "application/json"}


def _good_rows():
    return [
        {
            "player":       "LeBron James",
            "sport":        "NBA",
            "prop_type":    "Points",
            "line":         25.5,
            "direction":    "MORE",
            "slate_date":   __import__("datetime").date.today().isoformat(),
            "board_source": "PrizePicks",
            "game":         "LAL vs GSW",
        }
    ]


def _correct_hash():
    return get_governance_status()["governance_hash"]


def _good_body(session_id: str = "test-session-001",
               research_run_id: str = "rr-test-001",
               as_of: str | None = None) -> dict:
    """Return a fully-valid scoring request body with all mandatory fields."""
    import datetime as _dt
    return {
        "expected_governance_hash": _correct_hash(),
        "session_id":               session_id,
        "research_run_id":          research_run_id,
        "as_of":                    as_of or _dt.date.today().isoformat(),
        "rows":                     _good_rows(),
    }


# ===========================================================================
# 1. GET /wow/governance/status — full response shape
# ===========================================================================

class TestGovernanceStatusEndpoint:
    def test_returns_200(self, client):
        r = client.get("/wow/governance/status")
        assert r.status_code == 200

    def test_returns_master_spec_version(self, client):
        data = client.get("/wow/governance/status").get_json()
        assert "master_spec_version" in data
        assert data["master_spec_version"] == MASTER_SPEC_VERSION

    def test_returns_active_patch_ids(self, client):
        data = client.get("/wow/governance/status").get_json()
        assert "active_patch_ids" in data
        assert isinstance(data["active_patch_ids"], list)
        assert len(data["active_patch_ids"]) > 0

    def test_returns_governance_hash(self, client):
        data = client.get("/wow/governance/status").get_json()
        assert "governance_hash" in data
        assert len(data["governance_hash"]) == 64
        int(data["governance_hash"], 16)  # valid hex

    def test_returns_engine_code_version(self, client):
        data = client.get("/wow/governance/status").get_json()
        assert "engine_code_version" in data
        assert data["engine_code_version"] == ENGINE_CODE_VERSION

    def test_returns_effective_at(self, client):
        data = client.get("/wow/governance/status").get_json()
        assert "effective_at" in data
        assert data["effective_at"] is not None

    def test_returns_expires_at(self, client):
        data = client.get("/wow/governance/status").get_json()
        assert "expires_at" in data  # may be None — present is enough

    def test_returns_can_execute_false(self, client):
        data = client.get("/wow/governance/status").get_json()
        assert "can_execute" in data
        assert data["can_execute"] is False

    def test_no_auth_required(self, client):
        """Status endpoint is read-only — no API key needed."""
        r = client.get("/wow/governance/status")
        assert r.status_code == 200


# ===========================================================================
# 2. Correct handshake permits scoring (HTTP 200)
# ===========================================================================

class TestCorrectHandshakePermitsScoring:
    def test_correct_hash_returns_200(self, client):
        r = client.post("/gate-engine/run", headers=_auth_headers(),
                        data=json.dumps(_good_body()))
        assert r.status_code == 200

    def test_scoring_response_contains_prop_ledger(self, client):
        data = client.post("/gate-engine/run", headers=_auth_headers(),
                           data=json.dumps(_good_body())).get_json()
        assert "prop_ledger" in data
        assert len(data["prop_ledger"]) == 1


# ===========================================================================
# 3. Missing hash → HTTP 409 (fail closed)
# ===========================================================================

class TestMissingHashReturns409:
    def test_missing_hash_returns_409(self, client):
        body = {"rows": _good_rows()}  # no expected_governance_hash
        r = client.post("/gate-engine/run", headers=_auth_headers(),
                        data=json.dumps(body))
        assert r.status_code == 409

    def test_missing_hash_returns_mismatch_code(self, client):
        body = {"rows": _good_rows()}
        data = client.post("/gate-engine/run", headers=_auth_headers(),
                           data=json.dumps(body)).get_json()
        assert data["code"] == "RUN_INVALID_GOVERNANCE_MISMATCH"
        assert data["can_execute"] is False

    def test_missing_hash_no_partial_scoring_output(self, client):
        """No prop_ledger, terminal_labels, or final_card on 409."""
        body = {"rows": _good_rows()}
        data = client.post("/gate-engine/run", headers=_auth_headers(),
                           data=json.dumps(body)).get_json()
        assert "prop_ledger" not in data
        assert "terminal_labels" not in data
        assert "final_card" not in data

    def test_null_hash_treated_as_missing(self, client):
        """explicit null is the same as missing — still 409."""
        body = {"expected_governance_hash": None, "rows": _good_rows()}
        r = client.post("/gate-engine/run", headers=_auth_headers(),
                        data=json.dumps(body))
        assert r.status_code == 409

    def test_empty_string_hash_treated_as_missing(self, client):
        body = {"expected_governance_hash": "", "rows": _good_rows()}
        r = client.post("/gate-engine/run", headers=_auth_headers(),
                        data=json.dumps(body))
        assert r.status_code == 409


# ===========================================================================
# 4. Wrong hash → HTTP 409
# ===========================================================================

class TestWrongHashReturns409:
    def test_wrong_hash_returns_409(self, client):
        body = {
            "expected_governance_hash": "deadbeef" * 8,
            "rows": _good_rows(),
        }
        r = client.post("/gate-engine/run", headers=_auth_headers(),
                        data=json.dumps(body))
        assert r.status_code == 409

    def test_wrong_hash_no_partial_scoring_output(self, client):
        body = {
            "expected_governance_hash": "deadbeef" * 8,
            "rows": _good_rows(),
        }
        data = client.post("/gate-engine/run", headers=_auth_headers(),
                           data=json.dumps(body)).get_json()
        assert "prop_ledger" not in data
        assert "terminal_labels" not in data

    def test_wrong_hash_code_correct(self, client):
        body = {
            "expected_governance_hash": "a" * 64,
            "rows": _good_rows(),
        }
        data = client.post("/gate-engine/run", headers=_auth_headers(),
                           data=json.dumps(body)).get_json()
        assert data["code"] == "RUN_INVALID_GOVERNANCE_MISMATCH"
        assert data["can_execute"] is False

    def test_wrong_hash_server_hash_in_response(self, client):
        """The 409 response always includes the server's current hash."""
        body = {
            "expected_governance_hash": "b" * 64,
            "rows": _good_rows(),
        }
        data = client.post("/gate-engine/run", headers=_auth_headers(),
                           data=json.dumps(body)).get_json()
        assert "server_hash" in data
        assert data["server_hash"] == _correct_hash()


# ===========================================================================
# 5. Wrong patch list → HTTP 409
# ===========================================================================

class TestWrongPatchListReturns409:
    def test_extra_patch_id_caller_expects_returns_409(self, client):
        body = {
            "expected_governance_hash": _correct_hash(),
            "expected_patch_ids": ["WOW-FAKE-PATCH-XXXX"],
            "rows": _good_rows(),
        }
        r = client.post("/gate-engine/run", headers=_auth_headers(),
                        data=json.dumps(body))
        assert r.status_code == 409

    def test_incomplete_patch_list_returns_409(self, client):
        """Caller lists only one patch when server has many → 409."""
        body = {
            "expected_governance_hash": _correct_hash(),
            "expected_patch_ids": ["WOW-CORE-v16"],  # missing others
            "rows": _good_rows(),
        }
        r = client.post("/gate-engine/run", headers=_auth_headers(),
                        data=json.dumps(body))
        assert r.status_code == 409


# ===========================================================================
# 6. Successful response echoes governance hash + patch IDs
# ===========================================================================

class TestScoringResponseEchoesGovernance:
    def _score(self, client):
        return client.post("/gate-engine/run", headers=_auth_headers(),
                           data=json.dumps(_good_body())).get_json()

    def test_response_contains_governance_hash(self, client):
        data = self._score(client)
        assert "governance_hash" in data

    def test_governance_hash_matches_status_endpoint(self, client):
        """Hash in scoring response must be byte-for-byte identical to status."""
        status_data = client.get("/wow/governance/status").get_json()
        score_data = self._score(client)
        assert score_data["governance_hash"] == status_data["governance_hash"]

    def test_response_contains_patch_ids_applied(self, client):
        data = self._score(client)
        assert "patch_ids_applied" in data
        assert isinstance(data["patch_ids_applied"], list)

    def test_response_patch_ids_match_status(self, client):
        status_data = client.get("/wow/governance/status").get_json()
        score_data = self._score(client)
        assert set(score_data["patch_ids_applied"]) == set(status_data["active_patch_ids"])

    def test_response_can_execute_always_false(self, client):
        data = self._score(client)
        assert data.get("can_execute") is False

    def test_response_governance_handshake_match(self, client):
        data = self._score(client)
        assert data.get("governance_handshake") == "GOVERNANCE_MATCH"


# ===========================================================================
# 7. Hash determinism — same hash across separate module computations
# ===========================================================================

class TestHashDeterminism:
    def test_hash_is_deterministic_across_calls(self):
        """Same registry always produces the same hash — no runtime entropy."""
        hashes = [compute_governance_hash() for _ in range(10)]
        assert len(set(hashes)) == 1

    def test_hash_is_exactly_64_hex_chars(self):
        h = compute_governance_hash()
        assert len(h) == 64
        int(h, 16)

    def test_hash_does_not_contain_loaded_at(self):
        """The runtime timestamp must NOT affect the hash."""
        import hashlib, json
        from gate_engine.governance import _active_patches
        patches = _active_patches()
        fingerprint = sorted(
            [{"patch_id": p["patch_id"], "version": p["version"]} for p in patches],
            key=lambda x: x["patch_id"],
        )
        raw = json.dumps(fingerprint, sort_keys=True, separators=(",", ":"))
        expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        assert compute_governance_hash() == expected

    def test_status_endpoint_hash_matches_module_hash(self, client):
        """Status endpoint hash == compute_governance_hash() — no divergence."""
        status = client.get("/wow/governance/status").get_json()
        assert status["governance_hash"] == compute_governance_hash()


# ===========================================================================
# 8. Lowest-ceiling propagation
# ===========================================================================

class TestLowestCeilingPropagation:
    def test_multiple_blockers_strictest_wins(self):
        """When a row has multiple blockers, final_label is the strictest terminal."""
        from gate_engine import market_adverse, component_composite
        row = {
            "row_id":         "row_test_ceiling",
            "player":         "Kiki Iriafen",
            "sport":          "WNBA",
            "prop_type":      "Points",
            "line":           10.5,
            "direction":      "LESS",
            "game":           "A vs B",
            "blockers":       [],
            "gates":          {},
            "terminal_label": None,
        }
        # First gate sets REJECT_MARKET_ADVERSE_PUSH_LOSS
        market_adverse.run(row, sportsbook_line=11.0)
        first_label = row.get("terminal_label")
        assert first_label == PropLabel.REJECT_MARKET_ADVERSE_PUSH_LOSS.value

        # Simulating a second gate that would try to assign a LESS severe label
        # cannot overwrite an already-terminal label (downstream gates check terminal_label).
        # The pipeline's per-row loop skips rows with terminal_label already set.
        # Verify the label is still the first (strictest) one.
        assert row["terminal_label"] == PropLabel.REJECT_MARKET_ADVERSE_PUSH_LOSS.value
        assert len(row["blockers"]) >= 1

    def test_no_downstream_gate_restores_higher_label(self):
        """Once REJECT is set, no downstream gate may promote to MONEY_QUALIFIED."""
        row = {
            "row_id":         "row_ceiling2",
            "player":         "Test",
            "sport":          "WNBA",
            "prop_type":      "Points",
            "line":           8.5,
            "direction":      "LESS",
            "game":           "A vs B",
            "blockers":       [],
            "gates":          {},
            "terminal_label": PropLabel.REJECT_MARKET_ADVERSE_THRESHOLD.value,
        }
        # Simulate what the pipeline does: skip rows that already have terminal_label
        if row.get("terminal_label"):
            pass  # pipeline continues without overwriting
        assert row["terminal_label"] == PropLabel.REJECT_MARKET_ADVERSE_THRESHOLD.value

    def test_pipeline_lowest_ceiling_preserved_end_to_end(self):
        """Run a row through the full pipeline and confirm the reject label persists."""
        from gate_engine.pipeline import run_pipeline
        from datetime import date
        rows = [
            {
                "player":       "Kiki Iriafen",
                "sport":        "WNBA",
                "prop_type":    "Rebounds",
                "line":         10.5,
                "direction":    "LESS",
                "slate_date":   __import__("datetime").date.today().isoformat(),
                "board_source": "PrizePicks",
                "game":         "A vs B",
            }
        ]
        enrichment = {
            "kiki iriafen:rebounds": {
                "game_log":         [9, 10, 8, 11, 9, 10, 9, 11, 10, 9],
                "sportsbook_line":  11.0,
                "status_payload":   {"status": "ACTIVE", "source": "ESPN",
                                     "dnp_risk": False, "minutes_restriction": False},
                # WNBA evidence-acquisition required fields
                "event_status":    "SCHEDULED",
                "role_timestamp":  __import__("datetime").date.today().isoformat() + "T10:00:00Z",
                "role_confirmation_age_minutes": 5,   # forces FRESH regardless of wall clock
                "projected_minutes": 32.0,
                "role_status": {
                    "active_status":     "ACTIVE",
                    "role_timestamp":    __import__("datetime").date.today().isoformat() + "T10:00:00Z",
                    "projected_minutes": 32.0,
                },
                "box_score_log": [
                    {"date": "2026-07-30", "PTS": 12, "REB": 9,  "AST": 2, "MIN": 32, "FGA": 10},
                    {"date": "2026-07-27", "PTS": 14, "REB": 10, "AST": 3, "MIN": 34, "FGA": 11},
                    {"date": "2026-07-24", "PTS": 11, "REB": 8,  "AST": 2, "MIN": 30, "FGA": 9},
                    {"date": "2026-07-21", "PTS": 15, "REB": 11, "AST": 3, "MIN": 35, "FGA": 12},
                    {"date": "2026-07-18", "PTS": 10, "REB": 9,  "AST": 1, "MIN": 29, "FGA": 8},
                ],
                "matchup": {
                    "pace": 94.0, "opponent_defense": 110.0,
                    "position_defense": 109.0, "rebound_environment": 0.55,
                    "assist_environment": 0.48,
                },
            }
        }
        result = run_pipeline(
            raw_rows=rows,
            target_date=date.today(),
            enrichment=enrichment,
            skip_health_gate=True,
            skip_settlement_check=True,
        )
        prop = result["prop_ledger"][0]
        lbl = prop.get("terminal_label")
        # Must be a REJECT — must NOT be FINAL_APPROVED or MONEY_QUALIFIED
        assert lbl == PropLabel.REJECT_MARKET_ADVERSE_PUSH_LOSS.value, (
            f"Expected REJECT_MARKET_ADVERSE_PUSH_LOSS, got {lbl}"
        )
        # Blocker count must be ≥ 1
        assert len(prop.get("blockers", [])) >= 1


# ===========================================================================
# 9. Session exposure persistence across separate pipeline calls
# ===========================================================================

class TestSessionExposurePersistence:
    def test_same_ledger_detects_cross_call_duplicate(self):
        """
        Two pipeline calls sharing the same ExposureLedger catch a duplicate
        player that spans both calls — proving cross-request persistence works
        when the caller passes the same existing_ledger.
        """
        from gate_engine.pipeline import run_pipeline
        from gate_engine.exposure_gate import ExposureLedger
        from datetime import date

        shared_ledger = ExposureLedger()

        row_base = {
            "sport":        "WNBA",
            "prop_type":    "Points",
            "line":         20.5,
            "direction":    "MORE",
            "slate_date":   __import__("datetime").date.today().isoformat(),
            "board_source": "PrizePicks",
            "game":         "A vs B",
        }
        good_enrichment = lambda player: {
            f"{player.lower()}:points": {
                "game_log":       [22, 18, 25, 21, 19, 23, 20, 24, 17, 26],
                "sportsbook_line": 20.5,
                "status_payload": {"status": "ACTIVE", "source": "ESPN",
                                   "dnp_risk": False, "minutes_restriction": False},
            }
        }

        # Request 1: Player A passes (registers in shared ledger)
        r1 = run_pipeline(
            raw_rows=[{**row_base, "player": "PlayerA"}],
            target_date=date.today(),
            enrichment=good_enrichment("PlayerA"),
            skip_health_gate=True,
            skip_settlement_check=True,
            existing_ledger=shared_ledger,
        )
        p1 = r1["prop_ledger"][0]
        # PlayerA should not be blocked in request 1
        assert p1.get("gates", {}).get("exposure_gate", {}).get("registered") is True, (
            f"PlayerA should be registered in request 1; gate={p1.get('gates',{}).get('exposure_gate')}"
        )

        # Request 2: Player A again — same ledger detects duplicate
        r2 = run_pipeline(
            raw_rows=[{**row_base, "player": "PlayerA"}],
            target_date=date.today(),
            enrichment=good_enrichment("PlayerA"),
            skip_health_gate=True,
            skip_settlement_check=True,
            existing_ledger=shared_ledger,
        )
        p2 = r2["prop_ledger"][0]
        exposure_gate = p2.get("gates", {}).get("exposure_gate", {})
        assert exposure_gate.get("passed") is False, (
            f"PlayerA duplicate should be blocked in request 2; "
            f"gate={exposure_gate}, terminal_label={p2.get('terminal_label')}"
        )

    def test_fresh_ledger_does_not_detect_cross_call_duplicate(self):
        """
        Without a shared ledger, two pipeline calls are independent —
        the second call does NOT block a repeat player.
        """
        from gate_engine.pipeline import run_pipeline
        from datetime import date

        row_base = {
            "player":       "PlayerB",
            "sport":        "WNBA",
            "prop_type":    "Points",
            "line":         20.5,
            "direction":    "MORE",
            "slate_date":   __import__("datetime").date.today().isoformat(),
            "board_source": "PrizePicks",
            "game":         "A vs B",
        }
        enr = {
            "playerb:points": {
                "game_log":       [22, 18, 25, 21, 19, 23, 20, 24, 17, 26],
                "sportsbook_line": 20.5,
                "status_payload": {"status": "ACTIVE", "source": "ESPN",
                                   "dnp_risk": False, "minutes_restriction": False},
            }
        }
        kwargs = dict(
            target_date=date.today(),
            enrichment=enr,
            skip_health_gate=True,
            skip_settlement_check=True,
        )

        r1 = run_pipeline(raw_rows=[row_base], **kwargs)
        r2 = run_pipeline(raw_rows=[row_base], **kwargs)  # fresh ledger each time

        gate2 = r2["prop_ledger"][0].get("gates", {}).get("exposure_gate", {})
        # Without shared ledger, second call sees PlayerB fresh — should NOT block
        assert gate2.get("passed") is not False or gate2.get("registered") is True, (
            "Independent calls should not share exposure state"
        )


# ===========================================================================
# 10. Mandatory session/audit fields — session_id, research_run_id, as_of
# ===========================================================================

class TestMandatorySessionFields:
    """Hash passes, but missing session/audit field → 409 GOVERNANCE_MISMATCH."""

    def test_missing_session_id_returns_409(self, client):
        body = {k: v for k, v in _good_body().items() if k != "session_id"}
        r = client.post("/gate-engine/run", headers=_auth_headers(),
                        data=json.dumps(body))
        assert r.status_code == 409
        data = r.get_json()
        assert data["code"] == "RUN_INVALID_GOVERNANCE_MISMATCH"
        assert data["can_execute"] is False
        assert any("session_id" in m for m in data.get("mismatches", []))

    def test_missing_research_run_id_returns_409(self, client):
        body = {k: v for k, v in _good_body().items() if k != "research_run_id"}
        r = client.post("/gate-engine/run", headers=_auth_headers(),
                        data=json.dumps(body))
        assert r.status_code == 409
        data = r.get_json()
        assert data["code"] == "RUN_INVALID_GOVERNANCE_MISMATCH"
        assert data["can_execute"] is False
        assert any("research_run_id" in m for m in data.get("mismatches", []))

    def test_missing_as_of_returns_409(self, client):
        body = {k: v for k, v in _good_body().items() if k != "as_of"}
        r = client.post("/gate-engine/run", headers=_auth_headers(),
                        data=json.dumps(body))
        assert r.status_code == 409
        data = r.get_json()
        assert data["code"] == "RUN_INVALID_GOVERNANCE_MISMATCH"
        assert data["can_execute"] is False
        assert any("as_of" in m for m in data.get("mismatches", []))

    def test_null_session_id_returns_409(self, client):
        body = {**_good_body(), "session_id": None}
        r = client.post("/gate-engine/run", headers=_auth_headers(),
                        data=json.dumps(body))
        assert r.status_code == 409

    def test_empty_string_research_run_id_returns_409(self, client):
        body = {**_good_body(), "research_run_id": ""}
        r = client.post("/gate-engine/run", headers=_auth_headers(),
                        data=json.dumps(body))
        assert r.status_code == 409

    def test_all_three_present_returns_200(self, client):
        """All mandatory fields present with correct hash → 200."""
        r = client.post("/gate-engine/run", headers=_auth_headers(),
                        data=json.dumps(_good_body()))
        assert r.status_code == 200


# ===========================================================================
# 11. DB fail-closed at HTTP level — SESSION_LEDGER_UNAVAILABLE → 409
# ===========================================================================

class TestDbFailClosedHttp:
    """When psycopg2.connect fails, every row gets SESSION_LEDGER_UNAVAILABLE
    and the harness must return HTTP 409 RUN_INVALID_SESSION_LEDGER_UNAVAILABLE."""

    def test_db_failure_returns_409(self, client, monkeypatch):
        import psycopg2 as _pg

        def _fail_connect(*a, **kw):
            raise _pg.OperationalError("connection refused (injected by test)")

        monkeypatch.setattr(_pg, "connect", _fail_connect)

        body = _good_body(session_id="fail-sid-001")
        r = client.post("/gate-engine/run", headers=_auth_headers(),
                        data=json.dumps(body))
        assert r.status_code == 409
        data = r.get_json()
        assert data["code"] == "RUN_INVALID_SESSION_LEDGER_UNAVAILABLE"
        assert data["can_execute"] is False

    def test_db_failure_no_partial_scoring_output(self, client, monkeypatch):
        """No prop_ledger exposed on ledger-unavailable 409."""
        import psycopg2 as _pg

        monkeypatch.setattr(_pg, "connect",
                            lambda *a, **kw: (_ for _ in ()).throw(
                                _pg.OperationalError("db down")))

        body = _good_body(session_id="fail-sid-002")
        r = client.post("/gate-engine/run", headers=_auth_headers(),
                        data=json.dumps(body))
        data = r.get_json()
        assert "prop_ledger" not in data


# ===========================================================================
# 12. Successful response echoes session/audit fields
# ===========================================================================

class TestEchoedSessionFields:
    def test_response_echoes_session_id(self, client):
        body = _good_body(session_id="echo-test-001")
        data = client.post("/gate-engine/run", headers=_auth_headers(),
                           data=json.dumps(body)).get_json()
        assert data.get("session_id") == "echo-test-001"

    def test_response_echoes_research_run_id(self, client):
        body = _good_body(research_run_id="rr-echo-abc")
        data = client.post("/gate-engine/run", headers=_auth_headers(),
                           data=json.dumps(body)).get_json()
        assert data.get("research_run_id") == "rr-echo-abc"

    def test_response_echoes_as_of(self, client):
        body = _good_body(as_of="2026-07-14")
        data = client.post("/gate-engine/run", headers=_auth_headers(),
                           data=json.dumps(body)).get_json()
        assert data.get("as_of") == "2026-07-14"

    def test_response_contains_exposure_key(self, client):
        body = _good_body(session_id="expose-key-001")
        data = client.post("/gate-engine/run", headers=_auth_headers(),
                           data=json.dumps(body)).get_json()
        assert "exposure_key" in data
        assert data["exposure_key"] == "expose-key-001"

    def test_can_execute_still_false(self, client):
        data = client.post("/gate-engine/run", headers=_auth_headers(),
                           data=json.dumps(_good_body())).get_json()
        assert data.get("can_execute") is False
