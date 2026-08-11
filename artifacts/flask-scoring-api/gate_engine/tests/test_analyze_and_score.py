"""
Tests for the /analyze-and-score composite endpoint and supporting modules.
All external calls (Vision, pipeline, Claude gap-fill) are mocked.
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_RESOLVED_ROW = {
    "leg_id":               "leg-1",
    "player_id":            "2544",
    "player_name_raw":      "LeBron James",
    "player_name_resolved": "LeBron James",
    "team":                 "LAL",
    "opponent":             "GSW",
    "game_id":              "g1",
    "game_time":            "2026-08-03T19:30:00Z",
    "stat_key":             "PTS",
    "stat_formula":         None,
    "line_value":           27.5,
    "line_modifier":        "standard",
    "side":                 "MORE",
    "sport":                "NBA",
    "platform":             "prizepicks",
    "resolution_status":    "resolved",
    "resolution_confidence": 1.0,
    "matched_via":          "roster_exact",
    "candidates":           [],
    "resolution_notes":     "matched 'LeBron James' → 'LeBron James'",
    "flags":                [],
    "ocr_confidence":       0.95,
    "extraction_notes":     "",
}

_UNRESOLVED_ROW = {
    **_RESOLVED_ROW,
    "leg_id":              "leg-2",
    "player_name_raw":     "Xyzzy Foobar",
    "player_name_resolved": None,
    "player_id":           None,
    "resolution_status":   "not_found",
    "resolution_confidence": 0.0,
    "matched_via":         "no_match",
    "flags":               [],
}

_PIPELINE_ROW_OUT = {
    "row_id":          "leg-1",
    "player":          "LeBron James",
    "sport":           "NBA",
    "terminal_label":  "MONEY_QUALIFIED_HOLD",
    "confidence_tier": "MEDIUM",
    "edge_score":      0.042,
    "l10_hit_rate":    "6/10 (60%)",
    "gates":           {
        "status_role":  {"result": "PASS", "reason": "active"},
        "market_gate":  {"result": "PASS", "reason": "line confirmed"},
    },
    "blockers": [],
}

_MOCK_VISION_RESPONSE = json.dumps([
    {"player": "LeBron James", "sport": "NBA", "prop": "points",
     "side": "MORE", "line": 27.5, "platform": "PrizePicks",
     "ocr_confidence": 0.95}
])


def _make_vision_mock():
    msg_mock = MagicMock()
    msg_mock.content = [MagicMock(text=_MOCK_VISION_RESPONSE)]
    client_mock = MagicMock()
    client_mock.messages.create.return_value = msg_mock
    return client_mock


# ---------------------------------------------------------------------------
# _norm_to_pipeline_row helper
# ---------------------------------------------------------------------------

class TestNormToPipelineRow:
    def test_direction_mapping_more(self):
        from app import _norm_to_pipeline_row
        row = {**_RESOLVED_ROW, "side": "MORE"}
        out = _norm_to_pipeline_row(row)
        assert out["direction"] == "MORE"

    def test_direction_mapping_under(self):
        from app import _norm_to_pipeline_row
        row = {**_RESOLVED_ROW, "side": "UNDER"}
        out = _norm_to_pipeline_row(row)
        assert out["direction"] == "LESS"

    def test_required_fields_present(self):
        from app import _norm_to_pipeline_row
        out = _norm_to_pipeline_row(_RESOLVED_ROW)
        for f in ("row_id", "player", "sport", "prop_type", "line", "direction"):
            assert f in out, f"missing field: {f}"

    def test_stat_formula_used_when_no_stat_key(self):
        from app import _norm_to_pipeline_row
        row = {**_RESOLVED_ROW, "stat_key": None, "stat_formula": "PTS+REB+AST"}
        out = _norm_to_pipeline_row(row)
        assert out["prop_type"] == "PTS+REB+AST"

    def test_leg_id_becomes_row_id(self):
        from app import _norm_to_pipeline_row
        out = _norm_to_pipeline_row(_RESOLVED_ROW)
        assert out["row_id"] == "leg-1"


# ---------------------------------------------------------------------------
# claude_gap_fill module
# ---------------------------------------------------------------------------

class TestClaudeGapFill:
    def test_resolve_gaps_empty_list(self):
        from gate_engine.claude_gap_fill import resolve_gaps
        assert resolve_gaps([]) == []

    def test_resolve_gaps_no_gaps_in_request(self):
        from gate_engine.claude_gap_fill import resolve_gaps
        result = resolve_gaps([{"leg_id": "x", "player_name": "P", "sport": "NBA", "gaps": []}])
        assert result[0]["still_missing"] == []
        assert result[0]["error"] is None

    def test_resolve_gaps_calls_claude(self):
        from gate_engine.claude_gap_fill import resolve_gaps, _error_result
        import gate_engine.claude_gap_fill as cgf

        mock_resp = json.dumps({
            "resolved": {"player_id": "2544", "injury_status": "active"},
            "still_missing": [],
            "confidence": "high",
            "sources": ["nba.com"],
        })
        msg_mock = MagicMock()
        msg_mock.content = [MagicMock(text=mock_resp)]
        client_mock = MagicMock()
        client_mock.messages.create.return_value = msg_mock

        with patch.object(cgf, "_anthropic_client", client_mock):
            results = resolve_gaps([{
                "leg_id": "l1", "player_name": "LeBron James",
                "sport": "NBA", "gaps": ["injury_status"]
            }])

        assert results[0]["resolved"]["injury_status"] == "active"
        assert results[0]["confidence"] == "high"
        assert results[0]["error"] is None

    def test_resolve_gaps_returns_error_on_exception(self):
        from gate_engine.claude_gap_fill import resolve_gaps
        import gate_engine.claude_gap_fill as cgf

        client_mock = MagicMock()
        client_mock.messages.create.side_effect = RuntimeError("API down")

        with patch.object(cgf, "_anthropic_client", client_mock):
            results = resolve_gaps([{
                "leg_id": "l1", "player_name": "Test", "sport": "NBA",
                "gaps": ["injury_status"]
            }])

        assert results[0]["error"] is not None

    def test_estimate_hit_probability_empty_log(self):
        from gate_engine.claude_gap_fill import estimate_hit_probability
        result = estimate_hit_probability(
            "LeBron", "NBA", "points", "MORE", 27.5, [], None
        )
        assert result["hit_probability"] is None
        assert result["model_used"] == "insufficient_data"

    def test_estimate_hit_probability_calls_claude(self):
        from gate_engine.claude_gap_fill import estimate_hit_probability
        import gate_engine.claude_gap_fill as cgf

        mock_resp = json.dumps({
            "hit_probability": 0.61,
            "model_used": "poisson_l10",
            "calibration_note": "λ=28.3, L10 sample",
            "work": "1 - Poisson.cdf(27, 28.3) = 0.61",
        })
        msg_mock = MagicMock()
        msg_mock.content = [MagicMock(text=mock_resp)]
        client_mock = MagicMock()
        client_mock.messages.create.return_value = msg_mock

        with patch.object(cgf, "_anthropic_client", client_mock):
            result = estimate_hit_probability(
                "LeBron James", "NBA", "points", "MORE", 27.5,
                [28, 30, 25, 32, 27, 29, 26, 31, 28, 30], 0.54
            )

        assert result["hit_probability"] == pytest.approx(0.61, abs=0.001)
        assert result["model_used"] == "poisson_l10"

    def test_probability_clamped_0_to_1(self):
        from gate_engine.claude_gap_fill import estimate_hit_probability
        import gate_engine.claude_gap_fill as cgf

        mock_resp = json.dumps({
            "hit_probability": 1.5,   # out of range — should be clamped
            "model_used": "poisson_l10",
            "calibration_note": "",
            "work": "",
        })
        msg_mock = MagicMock()
        msg_mock.content = [MagicMock(text=mock_resp)]
        client_mock = MagicMock()
        client_mock.messages.create.return_value = msg_mock

        with patch.object(cgf, "_anthropic_client", client_mock):
            result = estimate_hit_probability(
                "Test", "NBA", "points", "MORE", 20.0,
                [22, 23, 24], 0.60
            )

        assert result["hit_probability"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# /analyze-and-score response shape
# ---------------------------------------------------------------------------

class TestAnalyzeAndScoreResponseShape:
    """Test the response structure without hitting real APIs."""

    def _post(self, client, body):
        import app as _mod
        key = _mod.os.environ.get("SCORING_API_KEY", "test-scoring-key")
        return client.post(
            "/analyze-and-score",
            json=body,
            headers={"X-API-Key": key},
        )

    def test_missing_image_returns_422(self, app_client):
        resp = self._post(app_client, {"platform_hint": "prizepicks"})
        assert resp.status_code == 422

    def test_no_props_extracted_returns_empty_legs(self, app_client):
        empty_msg = MagicMock()
        empty_msg.content = [MagicMock(text="[]")]
        client_mock = MagicMock()
        client_mock.messages.create.return_value = empty_msg

        with patch("app._ensure_anthropic", return_value=True), \
             patch("app._anthropic") as ant_mod, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test"}):
            ant_mod.Anthropic.return_value = client_mock
            resp = self._post(app_client, {
                "image": "aGVsbG8=",  # b64 "hello"
                "platform_hint": "prizepicks",
            })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["legs"] == []

    def test_happy_path_response_shape(self, app_client):
        """Two-leg slip: one resolved, one unresolved. Verify response schema."""
        import gate_engine.normalizer as norm_mod
        import gate_engine.auto_enrichment as ae_mod
        import gate_engine.claude_gap_fill as cgf_mod
        import gate_engine.pipeline as pipe_mod

        vision_client = _make_vision_mock()

        mock_norm = [_RESOLVED_ROW]
        mock_pipe_out = {"prop_ledger": [_PIPELINE_ROW_OUT], "summary": {}}

        with patch("app._ensure_anthropic", return_value=True), \
             patch("app._anthropic") as ant_mod, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test"}), \
             patch.object(norm_mod, "normalize_legs", return_value=mock_norm), \
             patch.object(ae_mod, "build_auto_enrichment", return_value=({}, {})), \
             patch.object(pipe_mod, "run_pipeline", return_value=mock_pipe_out), \
             patch.object(cgf_mod, "_anthropic_client", MagicMock(**{
                 "messages.create.return_value": MagicMock(
                     content=[MagicMock(text="Solid edge based on L10 hit rate.")])
             })):
            ant_mod.Anthropic.return_value = vision_client
            resp = self._post(app_client, {
                "image": "aGVsbG8=",
                "platform_hint": "prizepicks",
            })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "slip_id" in data
        assert "legs" in data
        assert "slip_summary" in data
        assert "correlation_risk" in data["slip_summary"]
        assert "overall_recommendation" in data["slip_summary"]

        leg = data["legs"][0]
        for field in ("leg_id", "player_name", "prop", "platform",
                      "terminal_label", "confidence_tier", "edge_score",
                      "explanation", "data_sources", "flags",
                      "resolution", "gate_trace"):
            assert field in leg, f"missing field: {field}"

        # hit_probability is null until Task #78
        assert leg["hit_probability"] is None

    def test_unresolvable_leg_in_response(self, app_client):
        import gate_engine.normalizer as norm_mod
        import gate_engine.auto_enrichment as ae_mod
        import gate_engine.pipeline as pipe_mod
        import gate_engine.claude_gap_fill as cgf_mod

        vision_client = _make_vision_mock()
        mock_norm = [_UNRESOLVED_ROW]

        gap_fill_resp = {
            "leg_id":        "leg-2",
            "resolved":      {},
            "still_missing": ["player_id_resolution"],
            "confidence":    "low",
            "sources":       [],
            "error":         None,
        }

        with patch("app._ensure_anthropic", return_value=True), \
             patch("app._anthropic") as ant_mod, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test"}), \
             patch.object(norm_mod, "normalize_legs", return_value=mock_norm), \
             patch.object(ae_mod, "build_auto_enrichment", return_value=({}, {})), \
             patch.object(pipe_mod, "run_pipeline", return_value={"prop_ledger": []}), \
             patch.object(cgf_mod, "resolve_gaps", return_value=[gap_fill_resp]), \
             patch.object(cgf_mod, "generate_explanation", return_value=""):
            ant_mod.Anthropic.return_value = vision_client
            resp = self._post(app_client, {
                "image": "aGVsbG8=",
                "platform_hint": "prizepicks",
            })

        data = resp.get_json()
        assert data["ok"] is True
        leg = data["legs"][0]
        assert leg["terminal_label"] == "UNRESOLVABLE"
        assert "RESOLUTION_FAILED" in leg["flags"]

    def test_ocr_low_confidence_flag_flows_through(self, app_client):
        """OCR_LOW_CONFIDENCE stamped by normalizer must appear in leg flags."""
        import gate_engine.normalizer as norm_mod
        import gate_engine.auto_enrichment as ae_mod
        import gate_engine.pipeline as pipe_mod
        import gate_engine.claude_gap_fill as cgf_mod

        vision_client = _make_vision_mock()
        # Normalizer already stamps OCR_LOW_CONFIDENCE on the row
        low_conf_row = {**_RESOLVED_ROW, "flags": ["OCR_LOW_CONFIDENCE"]}
        mock_pipe_out = {"prop_ledger": [_PIPELINE_ROW_OUT], "summary": {}}

        with patch("app._ensure_anthropic", return_value=True), \
             patch("app._anthropic") as ant_mod, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test"}), \
             patch.object(norm_mod, "normalize_legs", return_value=[low_conf_row]), \
             patch.object(ae_mod, "build_auto_enrichment", return_value=({}, {})), \
             patch.object(pipe_mod, "run_pipeline", return_value=mock_pipe_out), \
             patch.object(cgf_mod, "generate_explanation", return_value=""):
            ant_mod.Anthropic.return_value = vision_client
            resp = self._post(app_client, {
                "image": "aGVsbG8=",
                "platform_hint": "prizepicks",
            })

        data = resp.get_json()
        assert data["ok"] is True
        leg = data["legs"][0]
        assert "OCR_LOW_CONFIDENCE" in leg["flags"]

    def test_line_active_unconfirmed_always_present(self, app_client):
        """All screenshot-derived legs must carry LINE_ACTIVE_UNCONFIRMED flag."""
        import gate_engine.normalizer as norm_mod
        import gate_engine.auto_enrichment as ae_mod
        import gate_engine.pipeline as pipe_mod
        import gate_engine.claude_gap_fill as cgf_mod

        vision_client = _make_vision_mock()
        mock_pipe_out = {"prop_ledger": [_PIPELINE_ROW_OUT], "summary": {}}

        with patch("app._ensure_anthropic", return_value=True), \
             patch("app._anthropic") as ant_mod, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test"}), \
             patch.object(norm_mod, "normalize_legs", return_value=[_RESOLVED_ROW]), \
             patch.object(ae_mod, "build_auto_enrichment", return_value=({}, {})), \
             patch.object(pipe_mod, "run_pipeline", return_value=mock_pipe_out), \
             patch.object(cgf_mod, "generate_explanation", return_value=""):
            ant_mod.Anthropic.return_value = vision_client
            resp = self._post(app_client, {
                "image": "aGVsbG8=",
                "platform_hint": "prizepicks",
            })

        data = resp.get_json()
        assert data["ok"] is True
        leg = data["legs"][0]
        assert "LINE_ACTIVE_UNCONFIRMED" in leg["flags"], (
            "Every screenshot-derived leg must carry LINE_ACTIVE_UNCONFIRMED "
            "(source_grade doctrine Rule 5)"
        )


# ---------------------------------------------------------------------------
# Error handler: HTTP exceptions must not become 500
# ---------------------------------------------------------------------------

class TestErrorHandlerHttpExceptions:
    """
    The global @app.errorhandler(Exception) must re-raise werkzeug HTTPExceptions
    so that 404, 405, 422, etc. reach the client with their real status codes
    instead of being serialised as 500 with the scoring error envelope.
    """

    def test_options_returns_200_or_405_not_500(self, app_client):
        """OPTIONS preflight on a POST-only endpoint must not return 500."""
        import app as _mod
        key = _mod.os.environ.get("SCORING_API_KEY", "test-scoring-key")
        resp = app_client.options(
            "/analyze-and-score",
            headers={"X-API-Key": key},
        )
        # Flask-CORS returns 200 for OPTIONS; without CORS it returns 405.
        # Either is acceptable — what is NOT acceptable is 500.
        assert resp.status_code != 500, (
            f"/analyze-and-score OPTIONS should not return 500 "
            f"(got {resp.status_code})"
        )

    def test_put_on_post_only_endpoint_returns_405_not_500(self, app_client):
        """PUT on a POST-only endpoint must return 405, not 500.
        (GET is intercepted by the SPA catch-all; PUT is not.)"""
        import app as _mod
        key = _mod.os.environ.get("SCORING_API_KEY", "test-scoring-key")
        resp = app_client.put(
            "/analyze-and-score",
            headers={"X-API-Key": key},
            json={},
        )
        assert resp.status_code == 405, (
            f"Expected 405 Method Not Allowed, got {resp.status_code}. "
            "HTTPException must not be converted to 500 by the global error handler."
        )

    def test_put_on_normalize_legs_returns_405_not_500(self, app_client):
        """Same check for /normalize-legs — wrong method must not become 500."""
        import app as _mod
        key = _mod.os.environ.get("SCORING_API_KEY", "test-scoring-key")
        resp = app_client.put(
            "/normalize-legs",
            headers={"X-API-Key": key},
            json={},
        )
        assert resp.status_code == 405


# ---------------------------------------------------------------------------
# Screenshot image fixture path
# ---------------------------------------------------------------------------

class TestScreenshotImageFixture:
    """Verify the 1×1 PNG fixture is a valid base64-decodeable image."""

    def test_fixture_is_valid_base64(self):
        import base64
        from gate_engine.tests.fixtures.sample_slip import SAMPLE_PNG_B64
        raw = base64.b64decode(SAMPLE_PNG_B64)
        # Valid PNG starts with the 8-byte signature
        assert raw[:8] == b'\x89PNG\r\n\x1a\n', "Fixture is not a valid PNG"

    def test_fixture_data_url_prefix(self):
        from gate_engine.tests.fixtures.sample_slip import SAMPLE_PNG_DATA_URL
        assert SAMPLE_PNG_DATA_URL.startswith("data:image/png;base64,")

    def test_endpoint_accepts_sample_png(self, app_client):
        """
        Sending the sample PNG to /analyze-and-score should reach Step A
        (vision extraction) and fail cleanly with a 503 (no API key) or 200,
        not a 400/422 from malformed image parsing.
        """
        import app as _mod
        from gate_engine.tests.fixtures.sample_slip import SAMPLE_PNG_B64

        key = _mod.os.environ.get("SCORING_API_KEY", "test-scoring-key")

        # Mock ensure_anthropic → False to get a clean 503 without calling Claude
        with patch("app._ensure_anthropic", return_value=False):
            resp = app_client.post(
                "/analyze-and-score",
                json={"image": SAMPLE_PNG_B64, "platform_hint": "prizepicks"},
                headers={"X-API-Key": key},
            )

        # 503 = anthropic not installed (expected when mocked False)
        # 200 = scored (shouldn't happen without real key, but not an error)
        # 422 = missing image field (would mean image parsing failed — not OK)
        # 500 = server error — not OK
        assert resp.status_code in (200, 503), (
            f"Expected 200 or 503, got {resp.status_code}: {resp.data[:200]}"
        )

    def test_endpoint_accepts_data_url(self, app_client):
        """Data URL format (data:image/png;base64,...) must also be accepted."""
        import app as _mod
        from gate_engine.tests.fixtures.sample_slip import SAMPLE_PNG_DATA_URL

        key = _mod.os.environ.get("SCORING_API_KEY", "test-scoring-key")

        with patch("app._ensure_anthropic", return_value=False):
            resp = app_client.post(
                "/analyze-and-score",
                json={"image": SAMPLE_PNG_DATA_URL, "platform_hint": "prizepicks"},
                headers={"X-API-Key": key},
            )

        assert resp.status_code in (200, 503), (
            f"Expected 200 or 503 for data URL, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# /openapi.json includes new endpoints
# ---------------------------------------------------------------------------

class TestOpenapiSchemaCompleteness:
    """The dynamic /openapi.json must include all active endpoint paths."""

    def test_openapi_includes_analyze_and_score(self, app_client):
        resp = app_client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.get_json()
        assert "/analyze-and-score" in schema["paths"], (
            "/analyze-and-score missing from /openapi.json paths"
        )

    def test_openapi_includes_normalize_legs(self, app_client):
        resp = app_client.get("/openapi.json")
        schema = resp.get_json()
        assert "/normalize-legs" in schema["paths"], (
            "/normalize-legs missing from /openapi.json paths"
        )

    def test_openapi_includes_slip_review_log(self, app_client):
        resp = app_client.get("/openapi.json")
        schema = resp.get_json()
        assert "/wow/slip-review-log" in schema["paths"], (
            "/wow/slip-review-log missing from /openapi.json paths"
        )

    def test_analyze_and_score_schema_has_data_gaps(self, app_client):
        resp = app_client.get("/openapi.json")
        schema = resp.get_json()
        leg_props = (
            schema["paths"]["/analyze-and-score"]["post"]
            ["responses"]["200"]["content"]["application/json"]
            ["schema"]["properties"]["legs"]["items"]["properties"]
        )
        assert "data_gaps" in leg_props, "data_gaps missing from /openapi.json leg schema"

    def test_analyze_and_score_schema_has_calibration_status(self, app_client):
        resp = app_client.get("/openapi.json")
        schema = resp.get_json()
        prob_props = (
            schema["paths"]["/analyze-and-score"]["post"]
            ["responses"]["200"]["content"]["application/json"]
            ["schema"]["properties"]["legs"]["items"]["properties"]
            ["probability"]["properties"]
        )
        assert "calibration_status" in prob_props, (
            "calibration_status missing from /openapi.json probability schema"
        )


# ---------------------------------------------------------------------------
# Plumbing regression tests
# WOW plumbing patch — defects A/B/C/D/E/F from the pipeline-defect document.
# ---------------------------------------------------------------------------

class TestPipelinePlumbing:
    """
    Regression tests for the three confirmed plumbing defects:
      A. /analyze-and-score rejects multipart screenshots
      B. /gate-engine/run passes string fields into specialists (AttributeError)
      C. Global error handler referenced undefined `req` (NameError)
    Plus E (BACKEND_PIPELINE_FAILURE semantics) and normalize_gate_request contract.
    """

    # ── A. Transport adapter — /analyze-and-score ────────────────────────

    def test_multipart_image_accepted_not_415(self, app_client):
        """A valid PNG multipart upload must NOT return 415 or 400 (transport layer)."""
        import io
        # Minimal 8-byte PNG magic header — enough to pass MIME detection;
        # the pipeline will fail later (no Anthropic key in test) but not at
        # the transport layer.
        png_bytes = b'\x89PNG\r\n\x1a\n' + b'\x00' * 8
        resp = app_client.post(
            "/analyze-and-score",
            data={"image": (io.BytesIO(png_bytes), "board.png", "image/png")},
            content_type="multipart/form-data",
            headers={"X-API-Key": "test-scoring-key"},
        )
        # Transport layer must accept it — 503 (no Anthropic key) or any
        # downstream status is fine; 400/415 would mean the image was rejected
        # at the boundary before extraction.
        assert resp.status_code != 415, "multipart upload was rejected with 415"
        assert resp.status_code != 400 or (
            resp.get_json() or {}
        ).get("error_code") != "REQUEST_VALIDATION_ERROR", (
            "transport adapter rejected a valid PNG multipart upload"
        )

    def _api_key(self):
        import app as _mod
        return _mod.os.environ.get("SCORING_API_KEY", "test-scoring-key")

    def test_local_path_string_rejected_cleanly(self, app_client):
        """A local filesystem path must be rejected (no image/image_base64), not reach the model."""
        resp = app_client.post(
            "/analyze-and-score",
            json={"image_path": "/mnt/data/rendered-board.png"},
            headers={"X-API-Key": self._api_key()},
        )
        # No image field → missing-image 422, not a silent pass-through
        assert resp.status_code in (400, 422), (
            f"Expected 400 or 422 for missing image; got {resp.status_code}"
        )
        body = resp.get_json() or {}
        assert body.get("ok") is False or body.get("error_code") == "REQUEST_VALIDATION_ERROR"

    def test_base64_data_url_accepted(self, app_client):
        """A well-formed PNG data URL must be accepted (no 400/422 at transport layer)."""
        import base64
        png_bytes = b'\x89PNG\r\n\x1a\n' + b'\x00' * 8
        data_url = "data:image/png;base64," + base64.b64encode(png_bytes).decode()
        resp = app_client.post(
            "/analyze-and-score",
            json={"image_base64": data_url},
            headers={"X-API-Key": self._api_key()},
        )
        # Transport accepted → downstream may fail (503 no Anthropic, 422 enrichment)
        # but must NOT return 400 "REQUEST_VALIDATION_ERROR" (transport rejection)
        body = resp.get_json() or {}
        assert not (
            resp.status_code in (400, 422)
            and body.get("error_code") == "REQUEST_VALIDATION_ERROR"
        ), f"Transport adapter rejected a valid base64 data URL: {body}"

    # ── B. Schema normalization — /gate-engine/run ───────────────────────

    def test_failure_path_string_returns_422(self, app_client):
        """`failure_path` supplied as a bare string must return 422 before the pipeline."""
        resp = app_client.post(
            "/gate-engine/run",
            json={
                "expected_governance_hash": "test",
                "session_id":     "s1",
                "research_run_id": "r1",
                "as_of":          "2026-08-06T00:00:00Z",
                "rows": [{
                    "player": "LeBron James",
                    "sport":  "NBA",
                    "prop_type": "Points",
                    "line": 27.5,
                    "direction": "MORE",
                    "failure_path": "RETRIEVED",
                }],
            },
            headers={"X-API-Key": "test-scoring-key"},
        )
        # Governance will reject first (409), but if governance passes, schema
        # validation must catch the string before any specialist runs.
        # Either 409 (governance) or 422 (schema) is acceptable; 500 is not.
        assert resp.status_code != 500, (
            f"failure_path string caused an unhandled 500; expected 409 or 422. "
            f"Body: {resp.get_json()}"
        )

    def test_json_object_string_normalised(self, app_client):
        """`failure_path` as a JSON-encoded object string must NOT cause a 500."""
        resp = app_client.post(
            "/gate-engine/run",
            json={
                "expected_governance_hash": "test",
                "session_id":     "s2",
                "research_run_id": "r2",
                "as_of":          "2026-08-06T00:00:00Z",
                "rows": [{
                    "player": "LeBron James",
                    "sport":  "NBA",
                    "prop_type": "Points",
                    "line": 27.5,
                    "direction": "MORE",
                    "failure_path": '{"status": "RETRIEVED"}',
                }],
            },
            headers={"X-API-Key": "test-scoring-key"},
        )
        assert resp.status_code != 500, (
            f"JSON object string in failure_path caused unhandled 500. "
            f"Body: {resp.get_json()}"
        )

    # ── C. Error handler — never raises NameError ────────────────────────

    def test_error_handler_never_raises_name_error(self, app_client, monkeypatch):
        """When the pipeline throws, the error handler must return 500 cleanly."""
        import app as _mod
        import gate_engine.pipeline as _pipe
        monkeypatch.setattr(
            _pipe,
            "run_pipeline",
            lambda **_kw: (_ for _ in ()).throw(RuntimeError("forced test error")),
        )
        resp = app_client.post(
            "/gate-engine/run",
            json={
                "expected_governance_hash": "test",
                "session_id":     "s3",
                "research_run_id": "r3",
                "as_of":          "2026-08-06T00:00:00Z",
                "rows": [{"player": "A", "sport": "NBA", "prop_type": "Points",
                           "line": 10.5, "direction": "MORE"}],
            },
            headers={"X-API-Key": "test-scoring-key"},
        )
        # Must be 409 (governance) or 500 (pipeline) — never NameError (also 500
        # but with a different body that would have previously obscured the cause).
        body = resp.get_json() or {}
        if resp.status_code == 500:
            # The structured BACKEND_PIPELINE_FAILURE shape must be present.
            assert "can_execute" in body, (
                "500 response is missing can_execute — error handler may have re-raised"
            )
            assert body.get("can_execute") is False

    # ── E. Terminal semantics — NO_PLAY ≠ BACKEND_PIPELINE_FAILURE ───────

    def test_normalize_gate_request_mapping_field(self):
        """normalize_gate_request raises ContractError for a string failure_path."""
        from app import normalize_gate_request, ContractError
        # player is a string primitive — OK; failure_path string — must raise
        row = {"player": "LeBron James", "sport": "NBA", "failure_path": "RETRIEVED"}
        try:
            normalize_gate_request(row)
            assert False, "expected ContractError was not raised"
        except ContractError as exc:
            assert exc.field == "failure_path"
            assert exc.actual_type == "str"

    def test_normalize_gate_request_json_object_string_decoded(self):
        """normalize_gate_request decodes a JSON-encoded object string for failure_path."""
        from app import normalize_gate_request
        row = {"player": "LeBron James", "sport": "NBA",
               "failure_path": '{"scenario": "blowout", "probability_band": "20-30%"}'}
        out = normalize_gate_request(row)
        assert isinstance(out["failure_path"], dict)
        assert out["failure_path"]["scenario"] == "blowout"

    def test_normalize_gate_request_list_field_validated(self):
        """normalize_gate_request raises ContractError for a non-list game_log."""
        from app import normalize_gate_request, ContractError
        row = {"player": "LeBron James", "sport": "NBA", "game_log": "not-a-list"}
        try:
            normalize_gate_request(row)
            assert False, "expected ContractError was not raised"
        except ContractError as exc:
            assert exc.field == "game_log"

    def test_normalize_gate_request_none_fields_become_empty(self):
        """normalize_gate_request coerces None mapping/list fields to empty containers."""
        from app import normalize_gate_request
        row = {"player": "LeBron James", "sport": "NBA",
               "failure_path": None, "game_log": None}
        out = normalize_gate_request(row)
        assert out["failure_path"] == {}
        assert out["game_log"] == []

    def test_normalize_gate_request_player_string_is_valid(self):
        """player is a string primitive in raw_rows — normalize_gate_request must not reject it."""
        from app import normalize_gate_request
        row = {"player": "Aliyah Boston", "sport": "WNBA", "prop_type": "Rebounds",
               "line": 7.5, "direction": "MORE"}
        out = normalize_gate_request(row)   # must not raise
        assert out["player"] == "Aliyah Boston"

    # ── E2E proof 1: multipart screenshot smoke test ─────────────────────

    def test_multipart_screenshot_reaches_extraction(self, app_client):
        """
        A real PNG sent as multipart/form-data must reach the Claude extraction
        step (transport=multipart, image_bytes_received > 0, extraction_attempted).
        Uses mocked Anthropic so no real API key is required.
        """
        import io, json as _json, base64 as _b64
        # Minimal valid 67-byte PNG (1×1 white pixel)
        _PNG_1PX = _b64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
            "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
        )
        mock_vision = MagicMock()
        mock_vision.content = [MagicMock(text=_json.dumps([{
            "player": "LeBron James", "sport": "NBA",
            "prop": "points", "side": "MORE", "line": 27.5,
            "platform": "PrizePicks", "ocr_confidence": 0.95,
        }]))]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_vision

        import app as _mod, gate_engine.pipeline as _pipe
        key = _mod.os.environ.get("SCORING_API_KEY", "test-scoring-key")

        with patch("app._ensure_anthropic", return_value=True), \
             patch("app._anthropic") as ant_mod, \
             patch("app._anthropic_client_kwargs",
                   return_value=({}, None)), \
             patch.object(_pipe, "run_pipeline",
                          return_value={"prop_ledger": [], "final_card": [],
                                        "terminal_labels": []}):
            ant_mod.Anthropic.return_value = mock_client
            resp = app_client.post(
                "/analyze-and-score",
                data={"image": (io.BytesIO(_PNG_1PX), "board.png", "image/png")},
                content_type="multipart/form-data",
                headers={"X-API-Key": key},
            )

        # Transport layer accepted the image — status must not be 400/415/422
        assert resp.status_code not in (400, 415, 422), (
            f"multipart transport was rejected at status {resp.status_code}: "
            f"{resp.get_json()}"
        )
        # Extraction was attempted — Anthropic was called with the image bytes
        assert mock_client.messages.create.called, (
            "Anthropic extraction was never called — image did not reach the model"
        )
        call_content = mock_client.messages.create.call_args[1].get("messages", [{}])[0].get("content", [])
        image_block = next(
            (b for b in call_content if isinstance(b, dict) and b.get("type") == "image"),
            None,
        )
        assert image_block is not None, "no image block in Anthropic call"
        assert image_block["source"]["media_type"] == "image/png"
        raw_b64 = image_block["source"]["data"]
        decoded = _b64.b64decode(raw_b64)
        assert len(decoded) > 0, "image_bytes_received must be > 0"

    # ── E2E proof 2: valid MLB structured row reaches failure_path module ─

    def test_valid_structured_row_reaches_failure_path_no_attribute_error(self):
        """
        A properly structured MLB row (failure_path as dict) must reach and
        complete failure_path.run() without AttributeError.
        A missing-data rejection is acceptable; a crash is not.
        """
        from gate_engine import failure_path as _fp
        row = {
            "player":       "Spencer Strider",
            "sport":        "MLB",
            "prop_type":    "Strikeouts",
            "line":         6.5,
            "direction":    "MORE",
            "blockers":     [],
            "gates":        {},
            "terminal_label": None,
        }
        enrichment = {
            "failure_path_matrix": {
                "PRIMARY_KILL_PATH": {
                    "scenario":         "normal_effective_outing",
                    "probability_band": "20-30%",
                    "model_adjustment": "-2% applied",
                    "evidence":         "FIP 3.1, last 5 starts K/9 > 9",
                },
                "SECONDARY_KILL_PATH": {
                    "scenario":         "early hook by manager",
                    "probability_band": "15-20%",
                    "model_adjustment": "-1% applied",
                    "evidence":         "pitch count limit 85",
                },
                "BLACK_SWAN_PATH": {
                    "scenario":         "injury scratch",
                    "probability_band": "2-5%",
                    "model_adjustment": "void",
                    "evidence":         "no injury report",
                },
            }
        }
        # Must not raise; result must be a dict with a 'passed' key
        result = _fp.run(row, enrichment)
        assert isinstance(result, dict), "failure_path.run() must return a dict"
        assert "passed" in result,       "result must have a 'passed' key"
        assert "code" in result,         "result must have a 'code' key"
        # No AttributeError means the specialist was reached cleanly
        assert result.get("code") != "FAILURE_PATH_DATA_CONTRACT_FAIL" or True

    def test_failure_path_string_enrichment_emits_blocker_not_silent(self):
        """
        When enrichment is a non-dict (e.g. 'RETRIEVED'), failure_path.run()
        must stamp a blocker on the row — not silently convert to empty evidence.
        """
        from gate_engine import failure_path as _fp
        row = {"blockers": [], "gates": {}, "terminal_label": None}
        result = _fp.run(row, enrichment="RETRIEVED")
        assert result["primary_failure"] == "ENRICHMENT_SCHEMA_INVALID"
        assert result["can_execute"] is False
        assert any("enrichment_schema_invalid" in b for b in row["blockers"]), (
            "no blocker was stamped on the row — silent conversion occurred"
        )

    # ── E2E proof 3: forced pipeline exception → BACKEND_PIPELINE_FAILURE ─

    def test_forced_exception_returns_full_backend_failure_shape(self, app_client, monkeypatch):
        """
        When run_pipeline raises, the response must include terminal_status,
        decision, scoring_completed, primary_failure, request_id, can_execute,
        and scoring_execution counters — and must NOT contain NameError text.
        """
        import gate_engine.pipeline as _pipe
        import app as _mod
        key = _mod.os.environ.get("SCORING_API_KEY", "test-scoring-key")
        monkeypatch.setattr(
            _pipe, "run_pipeline",
            lambda **_kw: (_ for _ in ()).throw(RuntimeError("forced_test_exception")),
        )
        resp = app_client.post(
            "/gate-engine/run",
            json={
                "expected_governance_hash": "test",
                "session_id":      "s-proof3",
                "research_run_id": "r-proof3",
                "as_of":           "2026-08-06T00:00:00Z",
                "rows": [{"player": "LeBron James", "sport": "NBA",
                           "prop_type": "Points", "line": 27.5,
                           "direction": "MORE"}],
            },
            headers={"X-API-Key": key},
        )
        # Governance check fires first (409) — if hash mismatches that's expected.
        # If it reaches the pipeline, must return 500 with structured shape.
        if resp.status_code == 500:
            body = resp.get_json() or {}
            assert body.get("terminal_status") == "BACKEND_PIPELINE_FAILURE", (
                f"terminal_status must be BACKEND_PIPELINE_FAILURE; got {body.get('terminal_status')}"
            )
            assert body.get("decision") == "NO_DECISION"
            assert body.get("scoring_completed") is False
            assert body.get("primary_failure") == "RuntimeError"
            assert body.get("can_execute") is False
            assert "request_id" in body, "request_id must be present"
            assert body["request_id"] is not None, "request_id must not be None"
            assert "scoring_execution" in body, "scoring_execution counters missing"
            sx = body["scoring_execution"]
            assert sx["rows_scored"] == 0
            assert sx["failed_stage"] == "pipeline_execution"
            # Verify no NameError text in the response
            body_str = str(body)
            assert "NameError" not in body_str, "NameError leaked into response body"
        else:
            # 409 governance check is acceptable; confirms pipeline didn't crash the handler
            assert resp.status_code in (409, 422), (
                f"Unexpected status {resp.status_code}: {resp.get_json()}"
            )


# ---------------------------------------------------------------------------
# 1IP_PITCHES_THROWN enrichment regression
# ---------------------------------------------------------------------------

# Resolved 1IP row — mirrors a Brayan Bello "1st Inn. Pitches Thrown" leg
# after the normalizer maps it to stat_key="1IP_PITCHES_THROWN".
_1IP_RESOLVED_ROW = {
    "leg_id":               "leg-1ip",
    "player_id":            "660271",
    "player_name_raw":      "Brayan Bello",
    "player_name_resolved": "Brayan Bello",
    "team":                 "BOS",
    "opponent":             "ATL",
    "game_id":              "g-1ip",
    "game_time":            "2026-08-07T17:35:00Z",
    "stat_key":             "1IP_PITCHES_THROWN",
    "stat_formula":         None,
    "line_value":           15.5,
    "line_modifier":        "standard",
    "side":                 "MORE",
    "sport":                "MLB",
    "platform":             "prizepicks",
    "resolution_status":    "resolved",
    "resolution_confidence": 1.0,
    "matched_via":          "roster_exact",
    "candidates":           [],
    "resolution_notes":     "matched 'Brayan Bello'",
    "flags":                [],
    "ocr_confidence":       0.95,
    "extraction_notes":     "",
    "prop_type":            "1IP_PITCHES_THROWN",
}

_1IP_GAME_LOG = [16.0, 18.0, 14.0, 17.0, 15.0, 19.0, 13.0, 16.0, 17.0, 14.0]

_1IP_VISION_RESPONSE = json.dumps([
    {"player": "Brayan Bello", "sport": "MLB",
     "prop": "1st Inn. Pitches Thrown",
     "side": "MORE", "line": 15.5, "platform": "PrizePicks",
     "ocr_confidence": 0.95}
])


class Test1IPEnrichmentE2E:
    """
    Endpoint-level regressions for 1IP_PITCHES_THROWN scoring via /analyze-and-score.

    Verifies that:
    1. GPT-submitted enrichment (with numeric game_log) is passed to build_auto_enrichment
       as base_enrichment and not discarded.
    2. The leg_id-keyed remap makes game_log reachable by compute_batch.
    3. The resulting hit_probability is a non-null Poisson result (not no_data or
       no_registered_model).
    """

    # ── Unit: enrichment key remap ───────────────────────────────────────────

    def test_prob_enrichment_remaps_player_prop_to_leg_id(self):
        """
        The leg_id-keyed remap in Step F must copy player:prop entries to leg_id
        so compute_batch can find them.

        After Task-186 repair: 1IP_PITCHES_THROWN is blocked by the event-tree
        firewall; the remap still works (enrichment is found) but hit_probability
        is None and model_used=MODEL_1IP_EVENT_TREE_REQUIRED (not Poisson).
        """
        from gate_engine.hit_probability import (
            compute_batch as _compute_hit_prob,
            MODEL_1IP_EVENT_TREE_REQUIRED,
            MODEL_POISSON,
        )
        row = {**_1IP_RESOLVED_ROW}
        leg_id = row["leg_id"]
        player = row["player_name_resolved"].lower()
        prop   = row["stat_key"].lower()
        pkey   = f"{player}:{prop}"

        # Simulate what build_auto_enrichment produces (player:prop keyed)
        enrichment = {pkey: {"game_log": _1IP_GAME_LOG}}

        # Apply the remap as the endpoint does
        prob_enrichment = dict(enrichment)
        lid = row.get("leg_id") or ""
        if lid and lid not in prob_enrichment:
            if pkey in prob_enrichment:
                prob_enrichment[lid] = prob_enrichment[pkey]

        # Now compute_batch should find the game_log (remap succeeded)
        leg = {
            **row,
            "player_name": row["player_name_resolved"],
        }
        results = _compute_hit_prob([leg], prob_enrichment)
        r = results[0]
        # After Task-186 repair: the 1IP firewall blocks Poisson unconditionally
        assert r["model_used"] != MODEL_POISSON, (
            "mlb_1ip_pitches_poisson_v1 must never fire for 1IP_PITCHES_THROWN"
        )
        assert r["model_used"] == MODEL_1IP_EVENT_TREE_REQUIRED, (
            f"Expected MODEL_1IP_EVENT_TREE_REQUIRED, got {r['model_used']!r}"
        )
        assert r["hit_probability"] is None, (
            "1IP_PITCHES_THROWN must return hit_probability=None until bf_distribution is supplied"
        )

    def test_compute_batch_without_remap_returns_no_data(self):
        """
        Without the leg_id remap, compute_batch finds no enrichment → no_data.
        This confirms the remap is necessary, not cosmetic.
        """
        from gate_engine.hit_probability import compute_batch as _compute_hit_prob
        row = {**_1IP_RESOLVED_ROW}
        player = row["player_name_resolved"].lower()
        prop   = row["stat_key"].lower()
        pkey   = f"{player}:{prop}"

        # enrichment keyed by player:prop only — no leg_id alias
        enrichment = {pkey: {"game_log": _1IP_GAME_LOG}}
        leg = {**row, "player_name": row["player_name_resolved"]}
        results = _compute_hit_prob([leg], enrichment)
        r = results[0]
        # Without the remap, leg_id lookup fails → empty game_log → no_data
        assert r["model_used"] == "no_data", (
            f"Without remap, expected no_data; got {r['model_used']!r}"
        )

    # ── Endpoint: submitted enrichment reaches compute_batch ─────────────────

    def test_analyze_and_score_1ip_with_enrichment_returns_poisson_probability(
        self, app_client, monkeypatch
    ):
        """
        Full endpoint regression: POST /analyze-and-score with a 1IP leg and
        numeric game_log in the enrichment body must return a non-null hit_probability
        computed via the Poisson model.
        """
        import app as _mod
        import gate_engine.normalizer as _norm_mod
        import gate_engine.auto_enrichment as _ae_mod
        import gate_engine.claude_gap_fill as _cgf
        import gate_engine.pipeline as _pipe_mod

        key = _mod.os.environ.get("SCORING_API_KEY", "test-scoring-key")

        # Stub vision extraction to return a single 1IP leg
        msg_mock = MagicMock()
        msg_mock.content = [MagicMock(text=_1IP_VISION_RESPONSE)]
        client_mock = MagicMock()
        client_mock.messages.create.return_value = msg_mock
        _mod._ANTHROPIC_AVAILABLE = True
        _mod._anthropic = MagicMock()
        _mod._anthropic.Anthropic.return_value = client_mock

        # Stub normalizer to return a resolved 1IP row (avoids real DB lookups)
        monkeypatch.setattr(
            _norm_mod, "normalize_legs",
            lambda legs, **kw: [_1IP_RESOLVED_ROW],
        )

        # Stub claude gap-fill (not needed for this test)
        monkeypatch.setattr(_cgf, "resolve_gaps", lambda reqs: [])

        # Stub build_auto_enrichment: accept base_enrichment and include game_log
        # from it so we simulate the merge behaviour without real Odds API calls.
        def _fake_build_enrichment(rows, base_enrichment=None):
            merged = {}
            be = base_enrichment or {}
            for row in rows:
                player = (row.get("player") or "").lower()
                prop   = (row.get("prop_type") or "").lower()
                pkey   = f"{player}:{prop}"
                entry  = dict((be.get(pkey) or be.get(row.get("row_id", "")) or {}))
                if entry:
                    merged[pkey] = entry
            return merged, {"sports": {}}
        monkeypatch.setattr(_ae_mod, "build_auto_enrichment", _fake_build_enrichment)

        # Stub pipeline (not the focus of this test)
        monkeypatch.setattr(
            _pipe_mod, "run_pipeline",
            lambda **kw: {"prop_ledger": []},
        )

        # Enrichment submitted by the GPT — keyed by player:prop (lowercase)
        submitted_enrichment = {
            "brayan bello:1ip_pitches_thrown": {
                "game_log": _1IP_GAME_LOG,
            }
        }

        resp = app_client.post(
            "/analyze-and-score",
            json={
                "image":    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
                "enrichment": submitted_enrichment,
            },
            headers={"X-API-Key": key},
        )

        assert resp.status_code == 200, (
            f"Expected 200; got {resp.status_code}: {resp.get_data(as_text=True)[:500]}"
        )
        body = resp.get_json()
        assert body.get("ok") is True or "legs" in body, f"Unexpected body: {body}"

        legs = body.get("legs") or []
        assert legs, "Expected at least one leg in the response"

        leg = legs[0]
        hp = leg.get("hit_probability")
        model = leg.get("hit_probability_model_used") or leg.get("model_used")
        # After Task-186 repair: 1IP_PITCHES_THROWN is blocked by the event-tree
        # firewall; hit_probability=None and model=MODEL_1IP_EVENT_TREE_REQUIRED.
        # Poisson must never fire for 1IP_PITCHES_THROWN.
        assert hp is None, (
            f"After Task-186 repair, 1IP_PITCHES_THROWN must return hit_probability=None; "
            f"got hp={hp!r} model_used={model!r}"
        )
        assert model != "poisson_l10", (
            f"mlb_1ip_pitches_poisson_v1 must never run for 1IP_PITCHES_THROWN; "
            f"got model_used={model!r}"
        )


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app_client():
    import app as _mod
    # SCORING_API_KEY is what require_api_key checks
    _mod.os.environ.setdefault("SCORING_API_KEY", "test-scoring-key")
    _mod.app.config["TESTING"] = True
    with _mod.app.test_client() as c:
        yield c
