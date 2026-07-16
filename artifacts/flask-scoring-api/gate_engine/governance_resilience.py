"""
governance_resilience.py
WOW-PATCH-2026-07-15-GOVERNANCE-RESILIENCE-AND-ERROR-CONTRACT

Implements the five architectural corrections from the governance-resilience
post-mortem:

  1. GovernanceSnapshot — in-process cache of last-known-good governance state
  2. RunGovernancePin — per-run governance identity pinned at run start
  3. Distinct error code taxonomy:
       GOVERNANCE_UNAVAILABLE          endpoint unreachable, no valid cache
       GOVERNANCE_CACHED_DEGRADED_RUN  endpoint unreachable, valid cache exists
       GOVERNANCE_MISMATCH             endpoint reachable, hash/version mismatch
       GOVERNANCE_CONTRACT_INVALID     endpoint reachable, invalid schema
       SCAN_UNAVAILABLE_DEGRADED_RUN   governance valid, scan itself failed
  4. make_error_contract() — structured JSON error builder with retry guidance
  5. DegradedRunCeiling — max-label table per error code

Design constraints:
  - Pure Python, no Flask imports, no app.py imports.
  - Thread-safe (gunicorn multi-worker; in-process state is per-worker).
  - Snapshot never raises — all failures return None or raise explicitly handled.
  - GOVERNANCE_UNAVAILABLE and RUN_INVALID_GOVERNANCE_MISMATCH are never
    interchangeable: unavailable = no comparison made; mismatch = comparison
    made and failed.
"""
from __future__ import annotations

import threading
import time
from typing import Any


# ===========================================================================
# 1. Error Code Taxonomy
# ===========================================================================

class GovernanceErrorCode:
    """
    Structured error codes for governance and scan failures.

    Rules:
      - GOVERNANCE_UNAVAILABLE: endpoint unreachable, no usable cache.
        Cannot distinguish whether governance changed.
      - GOVERNANCE_CACHED_DEGRADED_RUN: endpoint unreachable, but a
        sufficiently fresh in-process cache exists. Research and confidence
        grading may proceed capped at MODEL_QUALIFIED_HOLD.
      - GOVERNANCE_MISMATCH: endpoint was reachable and returned a different
        hash/version than the caller provided. The caller must re-sync.
      - GOVERNANCE_CONTRACT_INVALID: endpoint reachable but returned an
        invalid schema or empty response.
      - SCAN_UNAVAILABLE_DEGRADED_RUN: governance was fully verified but the
        scan/enrichment subsystem itself failed.
    """
    GOVERNANCE_UNAVAILABLE          = "GOVERNANCE_UNAVAILABLE"
    GOVERNANCE_CACHED_DEGRADED_RUN  = "GOVERNANCE_CACHED_DEGRADED_RUN"
    GOVERNANCE_MISMATCH             = "GOVERNANCE_MISMATCH"
    GOVERNANCE_CONTRACT_INVALID     = "GOVERNANCE_CONTRACT_INVALID"
    GOVERNANCE_FULL_ATTESTATION     = "GOVERNANCE_FULL_ATTESTATION"
    SCAN_UNAVAILABLE_DEGRADED_RUN   = "SCAN_UNAVAILABLE_DEGRADED_RUN"


# ===========================================================================
# 2. Degraded Run Ceiling Table
# ===========================================================================

class DegradedRunCeiling:
    """
    Maximum allowed terminal label per governance failure mode.

    | Failure mode                           | Max label              |
    |----------------------------------------|------------------------|
    | Governance never verified              | RESEARCH_INTEREST      |
    | Cached governance valid                | MODEL_QUALIFIED_HOLD   |
    | Governance valid, live market missing  | MODEL_QUALIFIED_HOLD   |
    | Full governance verified               | FINAL_APPROVED (normal)|
    | Hash mismatch / contract invalid       | None (run invalid)     |
    """
    FULL_PIPELINE   = "FINAL_APPROVED"
    CACHED_DEGRADED = "MODEL_QUALIFIED_HOLD"
    RESEARCH_ONLY   = "RESEARCH_INTEREST"
    RUN_INVALID     = None


CEILING_BY_ERROR_CODE: dict[str, str | None] = {
    GovernanceErrorCode.GOVERNANCE_UNAVAILABLE:         DegradedRunCeiling.RESEARCH_ONLY,
    GovernanceErrorCode.GOVERNANCE_CACHED_DEGRADED_RUN: DegradedRunCeiling.CACHED_DEGRADED,
    GovernanceErrorCode.GOVERNANCE_MISMATCH:            DegradedRunCeiling.RUN_INVALID,
    GovernanceErrorCode.GOVERNANCE_CONTRACT_INVALID:    DegradedRunCeiling.RUN_INVALID,
    GovernanceErrorCode.GOVERNANCE_FULL_ATTESTATION:    DegradedRunCeiling.FULL_PIPELINE,
    GovernanceErrorCode.SCAN_UNAVAILABLE_DEGRADED_RUN:  DegradedRunCeiling.CACHED_DEGRADED,
}


def get_ceiling_for_error(error_code: str) -> str | None:
    """Return the maximum allowed label for the given error code."""
    return CEILING_BY_ERROR_CODE.get(error_code, DegradedRunCeiling.RUN_INVALID)


# ===========================================================================
# 3. Retry Guidance
# ===========================================================================

_RETRYABLE_HTTP_STATUSES    = {429, 502, 503, 504}
_NON_RETRYABLE_HTTP_STATUSES = {400, 401, 403, 404, 405, 409, 422}
_RETRY_DELAY_SECONDS         = [0, 2, 5]   # attempt 1 / 2 / 3


def is_retryable_status(http_status: int | None) -> bool:
    """Return True when the HTTP status warrants a retry."""
    if http_status is None:
        return True   # unknown → assume transient
    return http_status in _RETRYABLE_HTTP_STATUSES


def retry_after_for_attempt(attempt_number: int) -> int:
    """
    Return the recommended delay (seconds) before the given attempt.
    attempt_number is 1-indexed (1 = first retry attempt).
    """
    idx = max(0, attempt_number - 1)
    if idx < len(_RETRY_DELAY_SECONDS):
        return _RETRY_DELAY_SECONDS[idx]
    return _RETRY_DELAY_SECONDS[-1]


# ===========================================================================
# 4. Structured Error Contract Builder
# ===========================================================================

def make_error_contract(
    error_code:                 str,
    stage:                      str,
    http_status:                int,
    *,
    retryable:                  bool            = True,
    retry_after:                int             = 2,
    governance_verified:        bool            = False,
    governance_hash:            str  | None     = None,
    engine_code_version:        str  | None     = None,
    cached_snapshot_available:  bool            = False,
    cached_snapshot_age_seconds: int | None     = None,
    detail:                     str  | None     = None,
    mismatches:                 list[str] | None = None,
    recovery_path:              str  | None     = None,
    run_id:                     str  | None     = None,
    session_id:                 str  | None     = None,
    **extra: Any,
) -> dict[str, Any]:
    """
    Build a structured error response dict for any governance or scan failure.

    All failed calls return this shape so callers can distinguish:
      - whether to retry (retryable + retry_after_seconds)
      - what failed (error_code + stage)
      - whether governance was at least cached (cached_snapshot_available)
      - whether the run is fully invalid or just degraded (error_code)

    This dict is intended to be passed directly to Flask's jsonify().
    """
    return {
        "ok":                         False,
        "error_code":                 error_code,
        "stage":                      stage,
        "http_status":                http_status,
        "retryable":                  retryable,
        "retry_after_seconds":        retry_after,
        "governance_verified":        governance_verified,
        "governance_hash":            governance_hash,
        "engine_code_version":        engine_code_version,
        "cached_snapshot_available":  cached_snapshot_available,
        "cached_snapshot_age_seconds": cached_snapshot_age_seconds,
        "can_execute":                False,
        "detail":                     detail,
        "mismatches":                 mismatches or [],
        "recovery_path":              recovery_path or _default_recovery(error_code),
        "run_id":                     run_id,
        "session_id":                 session_id,
        **extra,
    }


def _default_recovery(error_code: str) -> str:
    """Return a terse human-readable recovery instruction for each error code."""
    _recovery = {
        GovernanceErrorCode.GOVERNANCE_UNAVAILABLE: (
            "Check /wow/engine/health. Retry in ~5s. "
            "If worker was sleeping, first retry may succeed."
        ),
        GovernanceErrorCode.GOVERNANCE_CACHED_DEGRADED_RUN: (
            "Live governance unavailable; cached snapshot in use. "
            "Run proceeds at MODEL_QUALIFIED_HOLD ceiling. "
            "Retry /wow/governance/status to get live hash for full approval."
        ),
        GovernanceErrorCode.GOVERNANCE_MISMATCH: (
            "Call GET /wow/governance/status to obtain the current "
            "governance_hash and resync before retrying."
        ),
        GovernanceErrorCode.GOVERNANCE_CONTRACT_INVALID: (
            "The governance endpoint returned an invalid response schema. "
            "Check /wow/engine/health and report to the engine team."
        ),
        GovernanceErrorCode.SCAN_UNAVAILABLE_DEGRADED_RUN: (
            "Governance is valid. Retry the scan after checking "
            "/wow/engine/health for scan-queue status."
        ),
    }
    return _recovery.get(error_code, "Retry after checking /wow/engine/health.")


def make_missing_hash_error(
    server_hash:    str,
    snapshot:       "GovernanceSnapshot",
    run_id:         str | None = None,
    session_id:     str | None = None,
) -> tuple[dict[str, Any], int]:
    """
    Build the appropriate error contract when expected_governance_hash is absent.

    Distinguishes between:
      - Cached snapshot available → GOVERNANCE_CACHED_DEGRADED_RUN (allow degraded run)
      - No cache at all           → GOVERNANCE_UNAVAILABLE (run invalid)

    Returns (error_dict, http_status).
    """
    age     = snapshot.age_seconds()
    fresh   = snapshot.is_fresh()
    age_int = int(age) if age is not None else None

    if fresh:
        error_code = GovernanceErrorCode.GOVERNANCE_CACHED_DEGRADED_RUN
        http_code  = 200   # degraded but allowed to proceed with research
        detail     = (
            "expected_governance_hash not supplied. "
            "Live verification skipped; using cached governance snapshot. "
            "Run proceeds at MODEL_QUALIFIED_HOLD ceiling. "
            "Supply expected_governance_hash for full pipeline."
        )
    else:
        error_code = GovernanceErrorCode.GOVERNANCE_UNAVAILABLE
        http_code  = 409
        detail     = (
            "expected_governance_hash is required. "
            "Call GET /wow/governance/status to obtain the current hash."
        )

    return make_error_contract(
        error_code=error_code,
        stage="governance_handshake",
        http_status=http_code,
        retryable=True,
        retry_after=0,
        governance_verified=False,
        governance_hash=server_hash,
        cached_snapshot_available=fresh,
        cached_snapshot_age_seconds=age_int,
        detail=detail,
        mismatches=["expected_governance_hash missing from request"],
        run_id=run_id,
        session_id=session_id,
        label_ceiling=get_ceiling_for_error(error_code),
    ), http_code


def make_mismatch_error(
    handshake_result: dict[str, Any],
    snapshot:         "GovernanceSnapshot",
    run_id:           str | None = None,
    session_id:       str | None = None,
) -> tuple[dict[str, Any], int]:
    """
    Build the error contract for a hash/version mismatch.

    This is always GOVERNANCE_MISMATCH — never GOVERNANCE_UNAVAILABLE.
    The endpoint was reachable; the caller is using a stale hash.
    """
    age     = snapshot.age_seconds()
    age_int = int(age) if age is not None else None

    return make_error_contract(
        error_code=GovernanceErrorCode.GOVERNANCE_MISMATCH,
        stage="governance_handshake",
        http_status=409,
        retryable=False,      # must resync, not retry with same hash
        retry_after=0,
        governance_verified=False,
        governance_hash=handshake_result.get("server_hash"),
        engine_code_version=None,
        cached_snapshot_available=snapshot.is_fresh(),
        cached_snapshot_age_seconds=age_int,
        detail=handshake_result.get("detail"),
        mismatches=handshake_result.get("mismatches", []),
        run_id=run_id,
        session_id=session_id,
        label_ceiling=DegradedRunCeiling.RUN_INVALID,
    ), 409


# ===========================================================================
# 5. GovernanceSnapshot — in-process cache
# ===========================================================================

class GovernanceSnapshot:
    """
    In-process, thread-safe cache of the last successfully fetched governance
    state (from gate_engine.governance.get_governance_status).

    Rationale: get_governance_status() reads a pure-Python registry with no
    external I/O — it cannot fail while the process is alive.  The snapshot
    therefore reflects the governance state at the most recent explicit
    refresh call.

    The cache survives within a gunicorn worker process. Each worker maintains
    its own independent snapshot (by design — no shared memory across workers).
    In-process governance state is always current, so the snapshot is mainly
    useful to:
      a) respond to external agents when the /wow/governance/status HTTP route
         is temporarily unreachable from the client's perspective.
      b) record what governance state was active when a run started.

    DEFAULT_MAX_AGE_SECONDS = 300 (5 minutes). Configurable via env var
    WOW_GOVERNANCE_SNAPSHOT_MAX_AGE.
    """

    DEFAULT_MAX_AGE_SECONDS: int = int(
        __import__("os").environ.get("WOW_GOVERNANCE_SNAPSHOT_MAX_AGE", "300")
    )

    def __init__(self) -> None:
        self._lock:       threading.Lock     = threading.Lock()
        self._snapshot:   dict[str, Any] | None = None
        self._fetched_at: float | None       = None

    def refresh(self) -> dict[str, Any]:
        """
        Re-read governance state from the in-process registry.
        Always succeeds (no network I/O). Raises only on import failure.
        """
        from gate_engine.governance import get_governance_status
        snapshot = get_governance_status()
        with self._lock:
            self._snapshot   = snapshot
            self._fetched_at = time.monotonic()
        return snapshot

    def get(self) -> dict[str, Any] | None:
        """Return the cached snapshot without refreshing (may be None)."""
        with self._lock:
            return self._snapshot

    def age_seconds(self) -> float | None:
        """Return the age of the snapshot in seconds, or None if never fetched."""
        with self._lock:
            if self._fetched_at is None:
                return None
            return time.monotonic() - self._fetched_at

    def is_fresh(self, max_age_seconds: int | None = None) -> bool:
        """Return True if the snapshot is within its freshness window."""
        max_age = max_age_seconds if max_age_seconds is not None else self.DEFAULT_MAX_AGE_SECONDS
        age     = self.age_seconds()
        return age is not None and age <= max_age

    def get_or_refresh(
        self, max_age_seconds: int | None = None
    ) -> dict[str, Any]:
        """Return cached snapshot if fresh, otherwise refresh first."""
        if not self.is_fresh(max_age_seconds):
            return self.refresh()
        snap = self.get()
        if snap is None:
            return self.refresh()
        return snap

    def as_cached_response(self) -> dict[str, Any] | None:
        """
        Return a copy of the snapshot annotated with source/age metadata,
        or None if the cache is empty.
        """
        with self._lock:
            snap = self._snapshot
            age  = (
                time.monotonic() - self._fetched_at
                if self._fetched_at is not None else None
            )
        if snap is None:
            return None
        return {
            **snap,
            "source":                "cached_snapshot",
            "cached_at_age_seconds": round(age, 1) if age is not None else None,
            "live_verified":         False,
        }

    def snapshot_metadata(self) -> dict[str, Any]:
        """Return cache metadata without the full snapshot content."""
        age = self.age_seconds()
        return {
            "snapshot_available":    self._snapshot is not None,
            "snapshot_age_seconds":  round(age, 1) if age is not None else None,
            "snapshot_is_fresh":     self.is_fresh(),
            "snapshot_max_age":      self.DEFAULT_MAX_AGE_SECONDS,
        }


# Singleton — one per gunicorn worker process
_governance_snapshot = GovernanceSnapshot()


def get_snapshot_singleton() -> GovernanceSnapshot:
    """Return the module-level singleton GovernanceSnapshot."""
    return _governance_snapshot


# ===========================================================================
# 6. RunGovernancePin — per-run governance identity
# ===========================================================================

class RunGovernancePin:
    """
    Pin the governance identity confirmed at the start of a run.

    Once a run's governance handshake succeeds, its run_id is mapped to the
    governance state that was active when it started. A temporary outage of
    the governance endpoint later in the same run does not invalidate
    already-verified governance.

    The pin dict records:
      run_id, master_spec_version, governance_hash, engine_code_version,
      active_patch_ids, verified_at (monotonic), verified_at_iso (UTC ISO-8601)
    """

    MAX_PINS: int = 1000   # cap to prevent unbounded memory growth

    def __init__(self) -> None:
        self._lock: threading.Lock                  = threading.Lock()
        self._pins: dict[str, dict[str, Any]]       = {}
        self._order: list[str]                       = []   # insertion order for eviction

    def pin(self, run_id: str, governance_state: dict[str, Any]) -> None:
        """Pin governance_state to run_id. Evicts oldest entry if at cap."""
        from datetime import datetime, timezone
        now_mono = time.monotonic()
        now_iso  = datetime.now(timezone.utc).isoformat()
        entry = {
            **governance_state,
            "pinned_at_monotonic": now_mono,
            "pinned_at_iso":       now_iso,
        }
        with self._lock:
            if run_id not in self._pins and len(self._pins) >= self.MAX_PINS:
                # Evict oldest entry
                oldest = self._order.pop(0)
                del self._pins[oldest]
            self._pins[run_id] = entry
            if run_id not in self._order:
                self._order.append(run_id)

    def get(self, run_id: str) -> dict[str, Any] | None:
        """Return the pinned governance state for run_id, or None."""
        with self._lock:
            return self._pins.get(run_id)

    def has(self, run_id: str) -> bool:
        """Return True if run_id has a pinned governance state."""
        with self._lock:
            return run_id in self._pins

    def pin_count(self) -> int:
        """Return the number of currently pinned runs."""
        with self._lock:
            return len(self._pins)

    def build_pin_payload(
        self,
        run_id: str,
        handshake_result: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Construct the pin payload from a successful handshake result.
        Stores the subset of governance fields relevant to the run audit trail.
        """
        from gate_engine.governance import (
            MASTER_SPEC_VERSION, ENGINE_CODE_VERSION, _ACTIVE_PATCH_IDS, _GOVERNANCE_HASH
        )
        return {
            "run_id":              run_id,
            "master_spec_version": MASTER_SPEC_VERSION,
            "governance_hash":     _GOVERNANCE_HASH,
            "engine_code_version": ENGINE_CODE_VERSION,
            "active_patch_ids":    list(_ACTIVE_PATCH_IDS),
        }


# Singleton
_run_pin = RunGovernancePin()


def get_run_pin_singleton() -> RunGovernancePin:
    """Return the module-level singleton RunGovernancePin."""
    return _run_pin


# ===========================================================================
# 7. Engine Health Summary (used by /wow/engine/health)
# ===========================================================================

def build_engine_health(uptime_seconds: float) -> dict[str, Any]:
    """
    Build the lightweight engine health response.

    This function makes NO external HTTP calls — it only inspects
    in-process state. It is safe to call from the health endpoint
    with sub-millisecond overhead.
    """
    import os

    # Governance
    try:
        from gate_engine.governance import (
            _GOVERNANCE_HASH, ENGINE_CODE_VERSION, _ACTIVE_PATCH_IDS,
            MASTER_SPEC_VERSION,
        )
        gov_loaded   = True
        gov_hash     = _GOVERNANCE_HASH
        gov_version  = ENGINE_CODE_VERSION
        gov_patches  = len(_ACTIVE_PATCH_IDS)
        gov_spec     = MASTER_SPEC_VERSION
    except Exception as exc:
        gov_loaded   = False
        gov_hash     = None
        gov_version  = None
        gov_patches  = 0
        gov_spec     = None

    # DB (light ping — just check env var present; full check is optional)
    db_url_set = bool(os.environ.get("DATABASE_URL"))

    # Snapshot metadata
    snap_meta = _governance_snapshot.snapshot_metadata()

    return {
        "ok":                   True,
        "service":              "WOW Gate Engine",
        "uptime_seconds":       round(uptime_seconds, 1),
        "worker_pid":           os.getpid(),
        "governance": {
            "loaded":           gov_loaded,
            "master_spec":      gov_spec,
            "engine_version":   gov_version,
            "hash_prefix":      gov_hash[:16] + "…" if gov_hash else None,
            "active_patches":   gov_patches,
        },
        "snapshot":             snap_meta,
        "db_env_configured":    db_url_set,
        "can_execute":          False,
    }
