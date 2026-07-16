"""
test_governance_resilience.py
WOW-PATCH-2026-07-15-GOVERNANCE-RESILIENCE-AND-ERROR-CONTRACT

Tests for governance_resilience.py covering:
  - Error code taxonomy (GOVERNANCE_UNAVAILABLE vs GOVERNANCE_MISMATCH)
  - make_error_contract() structure and fields
  - make_missing_hash_error() branching logic
  - make_mismatch_error() always = GOVERNANCE_MISMATCH
  - GovernanceSnapshot: refresh, age, freshness, cached response, metadata
  - RunGovernancePin: pin, get, has, eviction, count
  - CEILING_BY_ERROR_CODE table completeness and correctness
  - Retry guidance functions
  - build_engine_health() (no external calls)
"""
from __future__ import annotations

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest

from gate_engine.governance_resilience import (
    GovernanceErrorCode,
    DegradedRunCeiling,
    CEILING_BY_ERROR_CODE,
    get_ceiling_for_error,
    is_retryable_status,
    retry_after_for_attempt,
    make_error_contract,
    make_missing_hash_error,
    make_mismatch_error,
    GovernanceSnapshot,
    RunGovernancePin,
    get_snapshot_singleton,
    get_run_pin_singleton,
    build_engine_health,
)


# ===========================================================================
# Error code taxonomy
# ===========================================================================

class TestErrorCodeTaxonomy:
    def test_all_codes_distinct(self):
        codes = [
            GovernanceErrorCode.GOVERNANCE_UNAVAILABLE,
            GovernanceErrorCode.GOVERNANCE_CACHED_DEGRADED_RUN,
            GovernanceErrorCode.GOVERNANCE_MISMATCH,
            GovernanceErrorCode.GOVERNANCE_CONTRACT_INVALID,
            GovernanceErrorCode.GOVERNANCE_FULL_ATTESTATION,
            GovernanceErrorCode.SCAN_UNAVAILABLE_DEGRADED_RUN,
        ]
        assert len(set(codes)) == len(codes), "Error codes must all be distinct"

    def test_unavailable_and_mismatch_never_equal(self):
        assert (GovernanceErrorCode.GOVERNANCE_UNAVAILABLE !=
                GovernanceErrorCode.GOVERNANCE_MISMATCH)

    def test_unavailable_and_run_invalid_never_equal(self):
        # The document explicitly states these must never be interchangeable
        assert (GovernanceErrorCode.GOVERNANCE_UNAVAILABLE !=
                "RUN_INVALID_GOVERNANCE_MISMATCH")


# ===========================================================================
# Degraded run ceiling table
# ===========================================================================

class TestCeilingTable:
    def test_all_error_codes_in_ceiling_table(self):
        required = [
            GovernanceErrorCode.GOVERNANCE_UNAVAILABLE,
            GovernanceErrorCode.GOVERNANCE_CACHED_DEGRADED_RUN,
            GovernanceErrorCode.GOVERNANCE_MISMATCH,
            GovernanceErrorCode.GOVERNANCE_CONTRACT_INVALID,
            GovernanceErrorCode.GOVERNANCE_FULL_ATTESTATION,
            GovernanceErrorCode.SCAN_UNAVAILABLE_DEGRADED_RUN,
        ]
        for code in required:
            assert code in CEILING_BY_ERROR_CODE, f"Missing ceiling for {code}"

    def test_mismatch_ceiling_is_run_invalid(self):
        assert CEILING_BY_ERROR_CODE[GovernanceErrorCode.GOVERNANCE_MISMATCH] is None

    def test_contract_invalid_ceiling_is_run_invalid(self):
        assert CEILING_BY_ERROR_CODE[GovernanceErrorCode.GOVERNANCE_CONTRACT_INVALID] is None

    def test_unavailable_ceiling_is_research_only(self):
        assert (CEILING_BY_ERROR_CODE[GovernanceErrorCode.GOVERNANCE_UNAVAILABLE] ==
                DegradedRunCeiling.RESEARCH_ONLY)

    def test_cached_degraded_ceiling_is_model_qualified(self):
        assert (CEILING_BY_ERROR_CODE[GovernanceErrorCode.GOVERNANCE_CACHED_DEGRADED_RUN] ==
                DegradedRunCeiling.CACHED_DEGRADED)
        assert CEILING_BY_ERROR_CODE[GovernanceErrorCode.GOVERNANCE_CACHED_DEGRADED_RUN] == \
               "MODEL_QUALIFIED_HOLD"

    def test_full_attestation_ceiling_is_final_approved(self):
        assert (CEILING_BY_ERROR_CODE[GovernanceErrorCode.GOVERNANCE_FULL_ATTESTATION] ==
                DegradedRunCeiling.FULL_PIPELINE)
        assert CEILING_BY_ERROR_CODE[GovernanceErrorCode.GOVERNANCE_FULL_ATTESTATION] == \
               "FINAL_APPROVED"

    def test_get_ceiling_for_error_unknown_returns_run_invalid(self):
        assert get_ceiling_for_error("UNKNOWN_CODE_XYZ") is None


# ===========================================================================
# Retry guidance
# ===========================================================================

class TestRetryGuidance:
    def test_502_is_retryable(self):
        assert is_retryable_status(502) is True

    def test_503_is_retryable(self):
        assert is_retryable_status(503) is True

    def test_429_is_retryable(self):
        assert is_retryable_status(429) is True

    def test_504_is_retryable(self):
        assert is_retryable_status(504) is True

    def test_400_not_retryable(self):
        assert is_retryable_status(400) is False

    def test_401_not_retryable(self):
        assert is_retryable_status(401) is False

    def test_404_not_retryable(self):
        assert is_retryable_status(404) is False

    def test_409_not_retryable(self):
        assert is_retryable_status(409) is False

    def test_none_is_retryable(self):
        assert is_retryable_status(None) is True

    def test_attempt_1_delay_is_zero(self):
        assert retry_after_for_attempt(1) == 0

    def test_attempt_2_delay_is_2(self):
        assert retry_after_for_attempt(2) == 2

    def test_attempt_3_delay_is_5(self):
        assert retry_after_for_attempt(3) == 5

    def test_attempt_beyond_table_returns_last(self):
        assert retry_after_for_attempt(100) == 5


# ===========================================================================
# make_error_contract structure
# ===========================================================================

class TestMakeErrorContract:
    def _build(self, **kw):
        defaults = dict(
            error_code="GOVERNANCE_UNAVAILABLE",
            stage="governance_status_fetch",
            http_status=503,
        )
        defaults.update(kw)
        return make_error_contract(**defaults)

    def test_ok_always_false(self):
        assert self._build()["ok"] is False

    def test_can_execute_always_false(self):
        assert self._build()["can_execute"] is False

    def test_all_required_fields_present(self):
        result = self._build()
        required = [
            "ok", "error_code", "stage", "http_status",
            "retryable", "retry_after_seconds",
            "governance_verified", "governance_hash", "engine_code_version",
            "cached_snapshot_available", "cached_snapshot_age_seconds",
            "can_execute", "detail", "mismatches", "recovery_path",
            "run_id", "session_id",
        ]
        for field in required:
            assert field in result, f"Missing field: {field}"

    def test_extra_kwargs_included(self):
        result = self._build(label_ceiling="MODEL_QUALIFIED_HOLD")
        assert result["label_ceiling"] == "MODEL_QUALIFIED_HOLD"

    def test_mismatches_defaults_to_empty_list(self):
        result = self._build()
        assert isinstance(result["mismatches"], list)

    def test_recovery_path_auto_populated_for_known_code(self):
        result = self._build(error_code=GovernanceErrorCode.GOVERNANCE_MISMATCH)
        assert result["recovery_path"] is not None
        assert "governance/status" in result["recovery_path"].lower() or \
               "resync" in result["recovery_path"].lower()

    def test_recovery_path_custom_overrides_default(self):
        result = self._build(recovery_path="custom recovery instruction")
        assert result["recovery_path"] == "custom recovery instruction"


# ===========================================================================
# make_missing_hash_error — branching
# ===========================================================================

class TestMakeMissingHashError:
    def _fresh_snapshot(self):
        snap = GovernanceSnapshot()
        snap.refresh()
        return snap

    def _empty_snapshot(self):
        return GovernanceSnapshot()   # never refreshed

    def test_no_cache_yields_governance_unavailable(self):
        snap = self._empty_snapshot()
        err, status = make_missing_hash_error(
            server_hash="abc123", snapshot=snap,
        )
        assert err["error_code"] == GovernanceErrorCode.GOVERNANCE_UNAVAILABLE
        assert status == 409

    def test_no_cache_ceiling_is_research_only(self):
        # GOVERNANCE_UNAVAILABLE = no comparison made, ceiling = RESEARCH_INTEREST
        # (distinct from GOVERNANCE_MISMATCH which is truly run_invalid / None)
        snap = self._empty_snapshot()
        err, _ = make_missing_hash_error(server_hash="abc", snapshot=snap)
        assert err["label_ceiling"] == DegradedRunCeiling.RESEARCH_ONLY
        assert err["label_ceiling"] == "RESEARCH_INTEREST"

    def test_fresh_cache_yields_cached_degraded(self):
        snap = self._fresh_snapshot()
        err, status = make_missing_hash_error(
            server_hash="55191beb", snapshot=snap,
        )
        assert err["error_code"] == GovernanceErrorCode.GOVERNANCE_CACHED_DEGRADED_RUN
        assert status == 200   # degraded but allowed

    def test_fresh_cache_ceiling_is_model_qualified(self):
        snap = self._fresh_snapshot()
        err, _ = make_missing_hash_error(server_hash="55191beb", snapshot=snap)
        assert err["label_ceiling"] == DegradedRunCeiling.CACHED_DEGRADED

    def test_cached_snapshot_available_flag_correct(self):
        fresh_snap = self._fresh_snapshot()
        err_fresh, _ = make_missing_hash_error("h", fresh_snap)
        assert err_fresh["cached_snapshot_available"] is True

        empty_snap = self._empty_snapshot()
        err_empty, _ = make_missing_hash_error("h", empty_snap)
        assert err_empty["cached_snapshot_available"] is False

    def test_can_execute_always_false(self):
        snap = self._fresh_snapshot()
        err, _ = make_missing_hash_error("h", snap)
        assert err["can_execute"] is False


# ===========================================================================
# make_mismatch_error — always GOVERNANCE_MISMATCH
# ===========================================================================

class TestMakeMismatchError:
    def _handshake(self, detail="hash mismatch"):
        return {
            "valid":   False,
            "code":    "RUN_INVALID_GOVERNANCE_MISMATCH",
            "detail":  detail,
            "server_hash": "55191beb",
            "mismatches": ["hash mismatch"],
        }

    def test_always_governance_mismatch_code(self):
        snap = GovernanceSnapshot()
        err, status = make_mismatch_error(self._handshake(), snap)
        assert err["error_code"] == GovernanceErrorCode.GOVERNANCE_MISMATCH

    def test_not_retryable(self):
        snap = GovernanceSnapshot()
        err, _ = make_mismatch_error(self._handshake(), snap)
        assert err["retryable"] is False   # must resync, not retry

    def test_http_409(self):
        snap = GovernanceSnapshot()
        _, status = make_mismatch_error(self._handshake(), snap)
        assert status == 409

    def test_label_ceiling_is_run_invalid(self):
        snap = GovernanceSnapshot()
        err, _ = make_mismatch_error(self._handshake(), snap)
        assert err["label_ceiling"] is None

    def test_never_governance_unavailable(self):
        snap = GovernanceSnapshot()
        err, _ = make_mismatch_error(self._handshake(), snap)
        assert err["error_code"] != GovernanceErrorCode.GOVERNANCE_UNAVAILABLE


# ===========================================================================
# GovernanceSnapshot
# ===========================================================================

class TestGovernanceSnapshot:
    def test_empty_snapshot_age_is_none(self):
        snap = GovernanceSnapshot()
        assert snap.age_seconds() is None

    def test_empty_snapshot_not_fresh(self):
        snap = GovernanceSnapshot()
        assert snap.is_fresh() is False

    def test_empty_snapshot_get_returns_none(self):
        snap = GovernanceSnapshot()
        assert snap.get() is None

    def test_refresh_returns_dict(self):
        snap = GovernanceSnapshot()
        result = snap.refresh()
        assert isinstance(result, dict)
        assert "governance_hash" in result

    def test_after_refresh_age_is_small(self):
        snap = GovernanceSnapshot()
        snap.refresh()
        age = snap.age_seconds()
        assert age is not None and age < 2.0

    def test_after_refresh_is_fresh(self):
        snap = GovernanceSnapshot()
        snap.refresh()
        assert snap.is_fresh()

    def test_get_returns_snapshot_after_refresh(self):
        snap = GovernanceSnapshot()
        snap.refresh()
        result = snap.get()
        assert result is not None
        assert "governance_hash" in result

    def test_get_or_refresh_returns_dict(self):
        snap = GovernanceSnapshot()
        result = snap.get_or_refresh()
        assert isinstance(result, dict)

    def test_as_cached_response_none_when_empty(self):
        snap = GovernanceSnapshot()
        assert snap.as_cached_response() is None

    def test_as_cached_response_has_source_metadata(self):
        snap = GovernanceSnapshot()
        snap.refresh()
        resp = snap.as_cached_response()
        assert resp is not None
        assert resp["source"] == "cached_snapshot"
        assert resp["live_verified"] is False
        assert "cached_at_age_seconds" in resp

    def test_snapshot_metadata_fields(self):
        snap = GovernanceSnapshot()
        snap.refresh()
        meta = snap.snapshot_metadata()
        assert meta["snapshot_available"] is True
        assert meta["snapshot_is_fresh"] is True
        assert meta["snapshot_age_seconds"] is not None

    def test_expired_snapshot_not_fresh(self):
        snap = GovernanceSnapshot()
        snap.refresh()
        # Force expire by checking with max_age=0
        assert snap.is_fresh(max_age_seconds=0) is False

    def test_singleton_not_none(self):
        assert get_snapshot_singleton() is not None


# ===========================================================================
# RunGovernancePin
# ===========================================================================

class TestRunGovernancePin:
    def _pin(self):
        return RunGovernancePin()

    def test_empty_pin_has_returns_false(self):
        pin = self._pin()
        assert pin.has("run-001") is False

    def test_pin_and_has_returns_true(self):
        pin = self._pin()
        pin.pin("run-001", {"governance_hash": "abc"})
        assert pin.has("run-001") is True

    def test_pin_and_get_returns_correct_entry(self):
        pin = self._pin()
        pin.pin("run-002", {"governance_hash": "xyz", "engine_code_version": "v16.4"})
        entry = pin.get("run-002")
        assert entry is not None
        assert entry["governance_hash"] == "xyz"

    def test_get_missing_run_returns_none(self):
        pin = self._pin()
        assert pin.get("nonexistent-run") is None

    def test_pin_adds_timestamp_metadata(self):
        pin = self._pin()
        pin.pin("run-003", {})
        entry = pin.get("run-003")
        assert "pinned_at_iso" in entry
        assert "pinned_at_monotonic" in entry

    def test_pin_count_increments(self):
        pin = self._pin()
        assert pin.pin_count() == 0
        pin.pin("r1", {})
        assert pin.pin_count() == 1
        pin.pin("r2", {})
        assert pin.pin_count() == 2

    def test_eviction_at_max_pins(self):
        pin = RunGovernancePin()
        pin.MAX_PINS = 3
        for i in range(3):
            pin.pin(f"run-{i}", {"idx": i})
        assert pin.pin_count() == 3
        pin.pin("run-3", {"idx": 3})   # triggers eviction
        assert pin.pin_count() == 3
        assert not pin.has("run-0")    # oldest evicted
        assert pin.has("run-3")        # newest kept

    def test_re_pin_same_run_id_does_not_increase_count(self):
        pin = self._pin()
        pin.pin("dup-run", {"v": 1})
        pin.pin("dup-run", {"v": 2})
        assert pin.pin_count() == 1
        entry = pin.get("dup-run")
        assert entry["v"] == 2   # latest value kept

    def test_build_pin_payload_contains_required_fields(self):
        pin = self._pin()
        payload = pin.build_pin_payload(
            run_id="run-xyz",
            handshake_result={"server_hash": "55191beb"},
        )
        for field in ["run_id", "master_spec_version", "governance_hash",
                      "engine_code_version", "active_patch_ids"]:
            assert field in payload, f"Missing field: {field}"

    def test_singleton_not_none(self):
        assert get_run_pin_singleton() is not None


# ===========================================================================
# build_engine_health
# ===========================================================================

class TestBuildEngineHealth:
    def test_returns_dict(self):
        result = build_engine_health(uptime_seconds=42.5)
        assert isinstance(result, dict)

    def test_ok_true(self):
        result = build_engine_health(uptime_seconds=10.0)
        assert result["ok"] is True

    def test_can_execute_false(self):
        result = build_engine_health(uptime_seconds=10.0)
        assert result["can_execute"] is False

    def test_governance_loaded(self):
        result = build_engine_health(uptime_seconds=10.0)
        assert result["governance"]["loaded"] is True

    def test_uptime_present(self):
        result = build_engine_health(uptime_seconds=99.3)
        assert abs(result["uptime_seconds"] - 99.3) < 0.1

    def test_governance_hash_prefix_present(self):
        result = build_engine_health(uptime_seconds=1.0)
        assert result["governance"]["hash_prefix"] is not None
        assert "…" in result["governance"]["hash_prefix"]

    def test_no_external_calls(self):
        # build_engine_health must complete instantly (< 1s) — no external I/O
        start = time.monotonic()
        build_engine_health(uptime_seconds=1.0)
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"build_engine_health took {elapsed:.2f}s — external I/O suspected"

    def test_worker_pid_present(self):
        result = build_engine_health(uptime_seconds=1.0)
        assert isinstance(result["worker_pid"], int)
        assert result["worker_pid"] > 0


# ===========================================================================
# Patch registration check
# ===========================================================================

class TestGovernanceResiliencePatchRegistered:
    def test_patch_80_registered(self):
        from gate_engine.governance import get_governance_status
        status = get_governance_status()
        assert "WOW-PATCH-2026-07-15-GOVERNANCE-RESILIENCE-AND-ERROR-CONTRACT" in \
               status["active_patch_ids"]

    def test_precedence_80(self):
        from gate_engine.governance import _PATCH_REGISTRY
        patch = next(
            (p for p in _PATCH_REGISTRY
             if p["patch_id"] == "WOW-PATCH-2026-07-15-GOVERNANCE-RESILIENCE-AND-ERROR-CONTRACT"),
            None,
        )
        assert patch is not None
        assert patch["precedence"] == 80

    def test_engine_version_v16_5(self):
        from gate_engine.governance import ENGINE_CODE_VERSION
        assert ENGINE_CODE_VERSION == "v16.5"
