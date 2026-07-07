"""
test_degraded_engine_run.py — Phase 1 Item 1: DEGRADED_ENGINE_RUN label tests

Proves that:
- If a critical module raises during a pipeline run, run_status = DEGRADED_ENGINE_RUN
- FINAL_APPROVED and MONEY_QUALIFIED are 0 when degraded
- failed_modules list names the failing module
- A clean run (no exceptions) returns run_status = COMPLETE
"""
from __future__ import annotations
import sys, os, contextlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from gate_engine.pipeline import run_pipeline, _build_output
from gate_engine.labels import PropLabel
from gate_engine.exposure_gate import ExposureLedger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row(player="Alice", prop="Points", line=22.5, side="MORE", sport="NBA"):
    return {
        "player":    player,
        "prop_type": prop,
        "line":      line,
        "direction": side,
        "sport":     sport,
        "game_date": "2026-07-07",
        "platform":  "prizepicks",
    }


@contextlib.contextmanager
def _patch(module_path: str, fn_name: str, replacement):
    """Minimal monkeypatch context manager."""
    import importlib
    mod = importlib.import_module(module_path)
    original = getattr(mod, fn_name)
    setattr(mod, fn_name, replacement)
    try:
        yield mod
    finally:
        setattr(mod, fn_name, original)


def _noop_slate(*args, **kwargs):
    """Replacement for slate_validation.run that does nothing."""
    pass


# ---------------------------------------------------------------------------
# Clean-run tests
# ---------------------------------------------------------------------------

class TestCleanRunStatus:
    def test_clean_run_returns_complete(self):
        with _patch("gate_engine.slate_validation", "run", _noop_slate):
            result = run_pipeline(
                [_row()],
                skip_data_contract=True,
                skip_health_gate=True,
                skip_settlement_check=True,
            )
        assert result["run_status"] == "COMPLETE"

    def test_clean_run_summary_has_run_status(self):
        with _patch("gate_engine.slate_validation", "run", _noop_slate):
            result = run_pipeline(
                [_row()],
                skip_data_contract=True,
                skip_health_gate=True,
                skip_settlement_check=True,
            )
        assert result["summary"]["run_status"] == "COMPLETE"
        assert result["summary"]["degraded_run"] is False

    def test_clean_run_failed_modules_empty(self):
        with _patch("gate_engine.slate_validation", "run", _noop_slate):
            result = run_pipeline(
                [_row()],
                skip_data_contract=True,
                skip_health_gate=True,
                skip_settlement_check=True,
            )
        assert result["failed_modules"] == []


# ---------------------------------------------------------------------------
# DEGRADED_ENGINE_RUN via l5_l10_ledger failure
# ---------------------------------------------------------------------------

class TestDegradedEngineRunL5:
    def _run_with_l5_failure(self):
        def _fail(row, **kwargs):
            raise RuntimeError("ClientResponseError: 503 Service Unavailable")

        with _patch("gate_engine.slate_validation", "run", _noop_slate), \
             _patch("gate_engine.l5_l10_ledger", "run", _fail):
            return run_pipeline(
                [_row("Alice", "Points"), _row("Bob", "Assists")],
                skip_data_contract=True,
                skip_health_gate=True,
                skip_settlement_check=True,
            )

    def test_run_status_is_degraded(self):
        result = self._run_with_l5_failure()
        assert result["run_status"] == "DEGRADED_ENGINE_RUN"

    def test_failed_modules_list_populated(self):
        result = self._run_with_l5_failure()
        assert len(result["failed_modules"]) > 0
        assert any("l5_l10_ledger" in m for m in result["failed_modules"])

    def test_no_final_approved_labels(self):
        result = self._run_with_l5_failure()
        labels = [r["label"] for r in result["terminal_labels"]]
        assert PropLabel.FINAL_APPROVED.value not in labels

    def test_no_money_qualified_labels(self):
        result = self._run_with_l5_failure()
        labels = [r["label"] for r in result["terminal_labels"]]
        assert PropLabel.MONEY_QUALIFIED.value not in labels

    def test_degraded_flag_in_summary(self):
        result = self._run_with_l5_failure()
        assert result["summary"]["degraded_run"] is True

    def test_final_count_is_zero(self):
        result = self._run_with_l5_failure()
        assert result["summary"]["final_count"] == 0

    def test_rows_carry_module_failure_blocker(self):
        result = self._run_with_l5_failure()
        all_blockers = []
        for row in result["prop_ledger"]:
            all_blockers.extend(row.get("blockers") or [])
        assert any("MODULE_FAILURE:l5_l10_ledger" in b for b in all_blockers)


# ---------------------------------------------------------------------------
# DEGRADED_ENGINE_RUN via market_gate failure
# ---------------------------------------------------------------------------

class TestDegradedEngineRunMarket:
    def _run_with_market_failure(self):
        def _fail(row, **kwargs):
            raise ConnectionError("market_gate: fetch timeout")

        with _patch("gate_engine.slate_validation", "run", _noop_slate), \
             _patch("gate_engine.market_gate", "run", _fail):
            return run_pipeline(
                [_row()],
                skip_data_contract=True,
                skip_health_gate=True,
                skip_settlement_check=True,
            )

    def test_market_failure_marks_degraded(self):
        result = self._run_with_market_failure()
        assert result["run_status"] == "DEGRADED_ENGINE_RUN"

    def test_market_failure_module_named(self):
        result = self._run_with_market_failure()
        assert any("market_gate" in m for m in result["failed_modules"])

    def test_market_failure_no_final_approved(self):
        result = self._run_with_market_failure()
        assert result["summary"]["final_count"] == 0


# ---------------------------------------------------------------------------
# Ceiling enforcement: build_output with injected failed_modules
# Tests that rows carrying FINAL_APPROVED are downgraded in the output
# ---------------------------------------------------------------------------

class TestDegradedCeilingEnforcement:
    def _build_degraded(self, rows_with_labels):
        """
        Build output dict directly with pre-labelled rows and non-empty
        failed_modules, bypassing the full pipeline flow.
        """
        from gate_engine import board_intake
        rows = board_intake.normalize_board(rows_with_labels)
        for i, row in enumerate(rows):
            row["terminal_label"] = rows_with_labels[i].get("_inject_label")
        ledger = ExposureLedger()
        return _build_output(
            rows, ledger,
            failed_modules=["test_module:RuntimeError:simulated"],
            run_status="DEGRADED_ENGINE_RUN",
        )

    def test_final_approved_downgraded(self):
        rows = [{**_row("X"), "_inject_label": PropLabel.FINAL_APPROVED.value}]
        result = self._build_degraded(rows)
        labels = [r["label"] for r in result["terminal_labels"]]
        assert PropLabel.FINAL_APPROVED.value not in labels
        assert PropLabel.MODEL_QUALIFIED_HOLD.value in labels

    def test_money_qualified_downgraded(self):
        rows = [{**_row("Y"), "_inject_label": PropLabel.MONEY_QUALIFIED.value}]
        result = self._build_degraded(rows)
        labels = [r["label"] for r in result["terminal_labels"]]
        assert PropLabel.MONEY_QUALIFIED.value not in labels
        assert PropLabel.MODEL_QUALIFIED_HOLD.value in labels

    def test_model_qualified_not_touched(self):
        rows = [{**_row("Z"), "_inject_label": PropLabel.MODEL_QUALIFIED_HOLD.value}]
        result = self._build_degraded(rows)
        labels = [r["label"] for r in result["terminal_labels"]]
        assert PropLabel.MODEL_QUALIFIED_HOLD.value in labels

    def test_run_status_present_in_output(self):
        rows = [{**_row("W"), "_inject_label": PropLabel.RESEARCH_INTEREST.value}]
        result = self._build_degraded(rows)
        assert result["run_status"] == "DEGRADED_ENGINE_RUN"
        assert result["summary"]["degraded_run"] is True
