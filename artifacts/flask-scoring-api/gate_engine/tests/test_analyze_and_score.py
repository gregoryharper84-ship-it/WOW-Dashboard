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
