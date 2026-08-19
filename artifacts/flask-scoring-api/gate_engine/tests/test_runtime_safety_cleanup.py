"""
Regression coverage for moneyline orientation and daily-summary source safety.

All tests are offline.  No network calls or database writes are performed.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
from unittest.mock import patch

import pytest


def _moneyline_row(**overrides):
    row = {
        "sport": "MLB",
        "team": "Away Club",
        "opponent": "Home Club",
        "market_type": "h2h",
        "event_id": "mlb-away-home-regression",
        "slate_date": "2026-08-19",
    }
    row.update(overrides)
    return row


def _model_enrichment(**overrides):
    enrichment = {
        "home_win_pct": 0.70,
        "away_win_pct": 0.30,
        "event_status": "SCHEDULED",
        "status_freshness_hours": 0.1,
        "lineup_confirmed": True,
        "starter_confirmed": True,
        "player_status": "ACTIVE",
        "game_log": [{"result": "W"}] * 7 + [{"result": "L"}] * 3,
    }
    enrichment.update(overrides)
    return enrichment


class TestTypedOrientationResolver:
    @pytest.mark.parametrize("marker", ["HOME", "home", "vs", "VS.", True, 1, "YES", "H"])
    def test_explicit_home_markers(self, marker):
        from gate_engine.moneyline.orientation import (
            ParticipantOrientation,
            resolve_participant_orientation,
        )

        result = resolve_participant_orientation({"home_away": marker})
        assert result.orientation == ParticipantOrientation.HOME
        assert result.resolved is True
        assert result.is_home is True

    @pytest.mark.parametrize("marker", ["AWAY", "away", "@", False, 0, "NO"])
    def test_explicit_away_markers(self, marker):
        from gate_engine.moneyline.orientation import (
            ParticipantOrientation,
            resolve_participant_orientation,
        )

        result = resolve_participant_orientation({"home_away": marker})
        assert result.orientation == ParticipantOrientation.AWAY
        assert result.resolved is True
        assert result.is_home is False

    def test_absent_marker_is_typed_missing(self):
        from gate_engine.moneyline.orientation import (
            OrientationFailureReason,
            ParticipantOrientation,
            resolve_participant_orientation,
        )

        result = resolve_participant_orientation({})
        assert result.orientation == ParticipantOrientation.UNRESOLVED
        assert result.reason == OrientationFailureReason.MISSING
        assert result.is_home is None

    @pytest.mark.parametrize("marker", ["NEUTRAL", "", "home-ish", object()])
    def test_malformed_marker_is_typed_malformed(self, marker):
        from gate_engine.moneyline.orientation import (
            OrientationFailureReason,
            resolve_participant_orientation,
        )

        result = resolve_participant_orientation({"home_away": marker})
        assert result.resolved is False
        assert result.reason == OrientationFailureReason.MALFORMED
        assert result.invalid_values

    def test_conflicting_markers_are_typed_ambiguous(self):
        from gate_engine.moneyline.orientation import (
            OrientationFailureReason,
            resolve_participant_orientation,
        )

        result = resolve_participant_orientation(
            {"home_away": "HOME"},
            {"home_away": "AWAY"},
        )
        assert result.resolved is False
        assert result.reason == OrientationFailureReason.AMBIGUOUS

    def test_canonical_wrapper_preserves_side_unknown(self):
        from gate_engine.daily_orchestrator import resolve_participant_side

        assert resolve_participant_side({}) == "SIDE_UNKNOWN"
        assert resolve_participant_side({"home_away": "NEUTRAL"}) == "SIDE_UNKNOWN"
        assert resolve_participant_side({
            "home_away": "HOME",
            "enrichment": {"home_away": "AWAY"},
        }) == "SIDE_UNKNOWN"


class TestMoneylineCallerBoundaries:
    def test_legacy_helper_now_returns_typed_result_without_default(self):
        from gate_engine.moneyline.orientation import ParticipantOrientation
        from gate_engine.moneyline.sport_model import _is_home_side

        missing = _is_home_side(_moneyline_row(), {})
        home = _is_home_side(_moneyline_row(home_away="HOME"), {})
        away = _is_home_side(_moneyline_row(home_away="AWAY"), {})
        assert missing.orientation == ParticipantOrientation.UNRESOLVED
        assert missing.is_home is None
        assert home.orientation == ParticipantOrientation.HOME
        assert home.is_home is True
        assert away.orientation == ParticipantOrientation.AWAY
        assert away.is_home is False

    def test_direct_sport_model_returns_non_crashing_typed_failure(self):
        from gate_engine.moneyline.sport_model import compute_independent_probability

        result = compute_independent_probability(
            _moneyline_row(),
            _model_enrichment(),
        )
        assert result["independent_probability"] is None
        assert result["data_contract_status"] == "DATA_CONTRACT_FAIL"
        assert result["orientation_resolution"]["reason"] == "MISSING_ORIENTATION"
        assert result["can_execute"] is False

    @pytest.mark.parametrize(
        "orientation_fields,reason",
        [
            ({}, "MISSING_ORIENTATION"),
            ({"home_away": "NEUTRAL"}, "MALFORMED_ORIENTATION"),
            (
                {
                    "home_away": "HOME",
                    "enrichment_home_away": "AWAY",
                },
                "AMBIGUOUS_ORIENTATION",
            ),
        ],
    )
    def test_pipeline_returns_typed_failure_before_probability_work(
        self, orientation_fields, reason
    ):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline

        row_fields = {
            key: value
            for key, value in orientation_fields.items()
            if key != "enrichment_home_away"
        }
        enrichment = _model_enrichment()
        if "enrichment_home_away" in orientation_fields:
            enrichment["home_away"] = orientation_fields["enrichment_home_away"]

        result = run_moneyline_pipeline(
            _moneyline_row(**row_fields),
            enrichment,
            n_sims=50,
            seed=1,
        )

        assert result.terminal_label == "DATA_CONTRACT_FAIL"
        assert any(reason in blocker for blocker in result.blockers)
        assert result.outputs.independent_probability is None
        assert result.calibration == {}
        assert result.classification == {}
        assert result.can_execute is False

    def test_direct_scorer_returns_non_crashing_typed_failure(self):
        from gate_engine.moneyline_probability import score_outright_winner_row

        result = score_outright_winner_row(
            _moneyline_row(),
            enrichment=_model_enrichment(),
        )
        assert result["terminal_label"] == "DATA_CONTRACT_FAIL"
        assert result["probability_snapshot"] is None
        assert result["orientation_resolution"]["reason"] == "MISSING_ORIENTATION"
        assert result["can_execute"] is False
        assert result["can_approve_bets"] is False

    def test_mlb_away_home_field_reproduction_fails_closed_without_marker(self):
        """
        The evaluated team is the away club while inputs describe a strong home
        club.  The legacy default treated this as HOME and published the wrong
        perspective; the row must now stop before inversion/calibration.
        """
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline

        result = run_moneyline_pipeline(
            _moneyline_row(team="Away Club", opponent="Home Club"),
            _model_enrichment(home_win_pct=0.80, away_win_pct=0.20),
            n_sims=50,
            seed=7,
        )
        assert result.terminal_label == "DATA_CONTRACT_FAIL"
        assert result.outputs.independent_probability is None
        assert result.calibration == {}
        assert result.classification == {}

    def test_explicit_away_preserves_candidate_side_inversion(self):
        from gate_engine.moneyline.pipeline import run_moneyline_pipeline

        result = run_moneyline_pipeline(
            _moneyline_row(home_away="AWAY"),
            _model_enrichment(home_win_pct=0.70, away_win_pct=0.30),
            n_sims=500,
            seed=3,
        )
        assert result.outputs.independent_probability is not None
        assert result.outputs.independent_probability < 0.50


class TestCommittedManifestContract:
    def _run(self, **overrides):
        run = {
            "finished_at": "2026-08-19T12:00:00Z",
            "run_status": "COMPLETE",
            "reconciliation": {"reconciled": True},
            "total_discovered": 2,
            "persisted_row_count": 2,
        }
        run.update(overrides)
        return run

    def test_complete_reconciled_and_fully_persisted_is_committed(self):
        from storage.daily_manifest import _manifest_is_committed

        assert _manifest_is_committed(self._run()) is True

    def test_degraded_but_reconciled_and_fully_persisted_is_committed(self):
        from storage.daily_manifest import _manifest_is_committed

        assert _manifest_is_committed(self._run(run_status="DEGRADED")) is True

    @pytest.mark.parametrize(
        "override",
        [
            {"finished_at": None},
            {"run_status": "IN_PROGRESS"},
            {"run_status": "FAILED"},
            {"run_status": "RECONCILIATION_WARNING"},
            {"reconciliation": {"reconciled": False}},
            {"persisted_row_count": 1},
        ],
    )
    def test_incomplete_manifest_is_not_committed(self, override):
        from storage.daily_manifest import _manifest_is_committed

        assert _manifest_is_committed(self._run(**override)) is False


class TestDailySummarySelection:
    def test_canonical_manifest_is_preferred_and_legacy_is_not_read(self):
        import storage.daily_summary as summary

        canonical = {
            "run_id": "run-canonical",
            "run_status": "COMPLETE",
            "reconciliation": {"reconciled": True},
        }
        with (
            patch.object(summary, "get_latest_committed_run", return_value=canonical),
            patch.object(
                summary, "get_run_summary_counts",
                return_value={"Watch": 1},
            ) as canonical_counts,
            patch.object(
                summary, "get_run_summary_rows",
                return_value=[{"classification": "Watch"}],
            ) as canonical_rows,
            patch.object(
                summary, "get_run_source_flags",
                return_value={"total": 1},
            ) as canonical_flags,
            patch.object(
                summary, "get_scan_summary",
                side_effect=AssertionError("legacy summary must not be read"),
            ),
            patch.object(
                summary, "get_compact_scan_rows",
                side_effect=AssertionError("legacy rows must not be read"),
            ),
            patch.object(
                summary, "get_scan_source_flags",
                side_effect=AssertionError("legacy flags must not be read"),
            ),
        ):
            selected = summary.get_effective_daily_summary(
                "2026-08-19",
                category="Watch",
                limit=10,
            )

        assert selected["selected_source"] == summary.CANONICAL_MANIFEST
        assert selected["run_id"] == "run-canonical"
        assert selected["status"] == "completed"
        canonical_counts.assert_called_once_with("run-canonical")
        canonical_rows.assert_called_once_with(
            "run-canonical",
            category="Watch",
            sport=None,
            limit=10,
            offset=0,
        )
        canonical_flags.assert_called_once_with("run-canonical")

    def test_legacy_is_used_only_when_canonical_is_absent(self):
        import storage.daily_summary as summary

        with (
            patch.object(summary, "get_latest_committed_run", return_value=None),
            patch.object(summary, "get_scan_summary", return_value={"Reject": 2}),
            patch.object(
                summary, "get_compact_scan_rows",
                return_value=[{"classification": "Reject"}],
            ),
            patch.object(
                summary, "get_scan_source_flags",
                return_value={"total": 2},
            ),
        ):
            selected = summary.get_effective_daily_summary(
                "2026-08-19",
                limit=10,
            )

        assert selected["selected_source"] == summary.LEGACY_SCAN_RESULTS
        assert selected["run_id"] is None
        assert selected["status"] == "completed"
        assert selected["summary_counts"] == {"Reject": 2}

    def test_verbose_legacy_fallback_preserves_filters_and_pagination(self):
        import storage.daily_summary as summary

        expected_rows = [{"sport": "MLB", "classification": "Watch"}]
        with (
            patch.object(summary, "get_latest_committed_run", return_value=None),
            patch.object(summary, "get_scan_summary", return_value={"Watch": 1}),
            patch.object(
                summary, "get_scan_results", return_value=expected_rows,
            ) as legacy_results,
            patch.object(
                summary, "get_scan_source_flags", return_value={"total": 1},
            ),
        ):
            selected = summary.get_effective_daily_summary(
                "2026-08-19",
                category="Watch",
                sport="MLB",
                limit=7,
                offset=2,
                compact=False,
            )

        assert selected["selected_source"] == summary.LEGACY_SCAN_RESULTS
        assert selected["rows"] == expected_rows
        legacy_results.assert_called_once_with(
            "2026-08-19",
            sport="MLB",
            classification="Watch",
            limit=7,
            offset=2,
        )

    def test_verbose_filters_and_pagination_stay_on_selected_canonical_store(self):
        import storage.daily_summary as summary

        canonical = {
            "run_id": "run-filtered",
            "run_status": "COMPLETE",
            "reconciliation": {"reconciled": True},
        }
        expected_rows = [{"sport": "MLB", "classification": "Watch"}]
        with (
            patch.object(summary, "get_latest_committed_run", return_value=canonical),
            patch.object(summary, "get_run_summary_counts", return_value={"Watch": 9}),
            patch.object(
                summary, "get_run_summary_rows", return_value=expected_rows,
            ) as canonical_rows,
            patch.object(summary, "get_run_source_flags", return_value={"total": 9}),
            patch.object(
                summary, "get_scan_results",
                side_effect=AssertionError("legacy results must not be read"),
            ),
        ):
            selected = summary.get_effective_daily_summary(
                "2026-08-19",
                category="Watch",
                sport="MLB",
                limit=7,
                offset=2,
                compact=False,
            )

        assert selected["rows"] == expected_rows
        canonical_rows.assert_called_once_with(
            "run-filtered",
            category="Watch",
            sport="MLB",
            limit=7,
            offset=2,
        )

    def test_canonical_lookup_error_does_not_silently_fallback(self):
        import storage.daily_summary as summary

        with (
            patch.object(
                summary, "get_latest_committed_run",
                side_effect=RuntimeError("canonical query failed"),
            ),
            patch.object(summary, "get_scan_summary") as legacy_summary,
        ):
            with pytest.raises(RuntimeError, match="canonical query failed"):
                summary.get_effective_daily_summary("2026-08-19")
        legacy_summary.assert_not_called()

    def test_committed_empty_canonical_run_is_completed_not_pending(self):
        import storage.daily_summary as summary

        canonical = {
            "run_id": "run-empty",
            "run_status": "COMPLETE",
            "reconciliation": {"reconciled": True},
        }
        with (
            patch.object(summary, "get_latest_committed_run", return_value=canonical),
            patch.object(summary, "get_run_summary_counts", return_value={}),
            patch.object(summary, "get_run_summary_rows", return_value=[]),
            patch.object(summary, "get_run_source_flags", return_value={"total": 0}),
        ):
            selected = summary.get_effective_daily_summary("2026-08-19")

        assert selected["selected_source"] == summary.CANONICAL_MANIFEST
        assert selected["status"] == "completed"


class TestSummaryConsumerWiring:
    def test_scan_results_http_response_never_mixes_canonical_counts_and_legacy_rows(self):
        # app.py starts a production warmup daemon at import time.  Prevent
        # test-only imports from leaking background model/data loads into the
        # rest of the pytest process.
        with patch("threading.Thread.start", return_value=None):
            from app import app

        selected = {
            "selected_source": "canonical_manifest",
            "run_id": "run-http",
            "run": {
                "run_id": "run-http",
                "reconciliation": {"reconciled": True},
            },
            "status": "completed",
            "summary_counts": {"Watch": 1},
            "rows": [{
                "sport": "MLB",
                "classification": "Watch",
                "player": "Away Club",
            }],
            "source_flags": {"total": 1},
        }
        with (
            patch.dict(
                os.environ,
                {"SCORING_API_KEY": "runtime-safety-test-key"},
                clear=False,
            ),
            patch(
                "storage.daily_summary.get_effective_daily_summary",
                return_value=selected,
            ) as selector,
        ):
            app.config["TESTING"] = True
            response = app.test_client().get(
                "/scan-results"
                "?run_date=2026-08-19"
                "&sport=MLB"
                "&classification=Watch"
                "&limit=7",
                headers={"X-API-Key": "runtime-safety-test-key"},
            )

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["summary_source"] == "canonical_manifest"
        assert payload["results_source"] == "canonical_manifest"
        assert payload["summary_run_id"] == "run-http"
        assert payload["summary"] == {"Watch": 1}
        assert payload["results"] == selected["rows"]
        selector.assert_called_once_with(
            "2026-08-19",
            category="Watch",
            sport="MLB",
            limit=7,
            compact=False,
        )

    def test_scan_results_http_legacy_fallback_remains_compatible(self):
        import storage.daily_summary as summary
        with patch("threading.Thread.start", return_value=None):
            from app import app

        expected_rows = [{
            "sport": "MLB",
            "classification": "Watch",
            "player": "Legacy Away Club",
        }]
        with (
            patch.dict(
                os.environ,
                {"SCORING_API_KEY": "runtime-safety-test-key"},
                clear=False,
            ),
            patch.object(summary, "get_latest_committed_run", return_value=None),
            patch.object(summary, "get_scan_summary", return_value={"Watch": 1}),
            patch.object(
                summary, "get_scan_results", return_value=expected_rows,
            ) as legacy_results,
            patch.object(
                summary, "get_scan_source_flags", return_value={"total": 1},
            ),
        ):
            app.config["TESTING"] = True
            response = app.test_client().get(
                "/scan-results"
                "?run_date=2026-08-19"
                "&sport=MLB"
                "&classification=Watch"
                "&limit=7",
                headers={"X-API-Key": "runtime-safety-test-key"},
            )

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["summary_source"] == "legacy_scan_results"
        assert payload["results_source"] == "legacy_scan_results"
        assert payload["summary_run_id"] is None
        assert payload["summary"] == {"Watch": 1}
        assert payload["results"] == expected_rows
        legacy_results.assert_called_once_with(
            "2026-08-19",
            sport="MLB",
            classification="Watch",
            limit=7,
            offset=0,
        )

    def test_all_three_daily_summary_consumers_use_single_selector(self):
        app_path = Path(__file__).resolve().parents[2] / "app.py"
        source = app_path.read_text()
        tree = ast.parse(source)
        lines = source.splitlines(keepends=True)
        targets = {"wow_daily_scan", "scan_results", "scan_results_summary"}
        found = {}

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in targets:
                found[node.name] = "".join(lines[node.lineno - 1:node.end_lineno])

        assert set(found) == targets
        for name, function_source in found.items():
            assert "get_effective_daily_summary(" in function_source, name
            assert "get_scan_summary(" not in function_source, name
            assert '"summary_source"' in function_source, name
        assert "get_scan_results(" not in found["scan_results"]

    def test_production_callers_no_longer_call_legacy_bool_resolver(self):
        root = Path(__file__).resolve().parents[2]
        pipeline_source = (root / "gate_engine/moneyline/pipeline.py").read_text()
        app_source = (root / "app.py").read_text()
        scorer_source = (
            root / "gate_engine/moneyline_probability.py"
        ).read_text()

        assert "_is_home_side(" not in pipeline_source
        assert "_is_home_side(" not in app_source
        assert "_is_home_side(" not in scorer_source
        assert "_score_moneyline(_ow_row, enrichment=_ow_enr)" in app_source