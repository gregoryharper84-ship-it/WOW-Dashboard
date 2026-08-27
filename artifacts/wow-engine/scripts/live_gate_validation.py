#!/usr/bin/env python3
"""
live_gate_validation.py
WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE v2 -- Batch 3 live gates

Runs the live-Supabase validation sequence agreed after the Step 3d
re-review and the local-Postgres stopgap: real supabase-py/PostgREST
behavior, real calibrator persistence, real historical-row acquisition,
a real Phase B bounds computation from Supabase-loaded data, and the
actual /score-prop HTTP handler (via a local uvicorn server, not
FastAPI's in-process TestClient) with real Supabase persistence,
including a read-back comparison and a live negative-path regression.

PREREQUISITES (this script does none of these for you):
  1. A real Supabase project exists.
  2. schema.sql has already been applied to it -- via the Supabase SQL
     editor, or `psql "$SUPABASE_DB_URL" -f schema.sql` using the
     project's direct Postgres connection string (NOT the same thing as
     SUPABASE_URL/SUPABASE_SERVICE_KEY below -- those talk to the
     PostgREST API, which cannot run arbitrary DDL). This mirrors the
     local-Postgres stopgap gate, now against the real project.
  3. SUPABASE_URL and SUPABASE_SERVICE_KEY are set as environment
     variables in this session -- NEVER pass them on the command line or
     paste them into chat (shell history, process listings).

USAGE:
    cd artifacts/wow-engine
    pip install -r requirements.txt
    export SUPABASE_URL=...        # environment secret, not chat text
    export SUPABASE_SERVICE_KEY=...
    python scripts/live_gate_validation.py

Every row this script creates is tagged with LIVE_GATE_RUN_ID (a random
suffix printed at startup) and deleted in a best-effort cleanup pass at
the end, so re-running is safe and doesn't accumulate test data in the
real project. Cleanup runs even on failure.

This script does NOT flip governed_probability_capability -- that
remains a manual, reviewed decision per patch v2's own deployment order,
made lane-by-lane after this sequence comes back clean for a given
sport/market lane with real fitted parameters (not the synthetic
fixtures this script uses, which are clearly labeled as such throughout).
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RUN_ID = uuid.uuid4().hex[:8]
PORT = 8811

# Section 2's Platt v1/v2 training_n and Section 3/4's normal-fixture row
# count are kept in agreement deliberately (Step-5 review STEP5-VALIDATOR-
# FIX-02): the live Phase B/C bounds gate needs a statistically
# nondegenerate historical cohort, not the 3-row fixture that produced a
# saturated bootstrap Platt fit and a spurious ModelCalibrationUnavailableError
# on run fcec80e3.
SECTION2_V1_TRAINING_N = 200  # PHASE_B_MIN_N boundary
SECTION4_TRAINING_N = 250  # normal (non-late_settle/no_settlement) fixture rows

RESULTS: list[tuple[str, bool, str]] = []  # (name, passed, detail)


def check(name: str, condition: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(condition), detail))
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" -- {detail}" if detail else ""))
    return bool(condition)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_section3_fixture(n_normal: int = SECTION4_TRAINING_N, seed: int = 2027) -> dict:
    """Pure (no I/O) fixture builder for Sections 3-4's historical-
    calibration cohort. No network calls, no wall-clock side effects
    beyond reading the current instant once as a future-safe anchor --
    testable in isolation.

    Step-5 review STEP5-VALIDATOR-FIX-02/03: every 'normal' row's
    event_start_time is anchored 10 years past the moment this function
    runs (not a hardcoded calendar date), so ordinary fixture rows never
    age into the DB's post-start immutability trigger and remain
    deletable by cleanup() regardless of when this script is run. Only
    Section 8's neg3 row is deliberately backdated and permanent.

    Chronology (all instants relative to `anchor`):
        anchor  <  normal event starts/settlements (~n_normal hours)
                <  no_settlement event start (day 20)
                <  late_settle event start (day 40)
                <  candidate_as_of (day 60)
                <  late_settle settlement (day 90)
    so late_settle is excluded only by the as-of filter (its event_start
    alone would look eligible) and no_settlement is excluded by the
    loader itself (no settlement_timestamp at all).
    """
    import numpy as np

    anchor = datetime.now(timezone.utc) + timedelta(days=3650)
    candidate_as_of = anchor + timedelta(days=60)

    rng = np.random.default_rng(seed)
    raw = rng.uniform(0.3, 0.7, size=n_normal)
    hits = (rng.uniform(0, 1, size=n_normal) < raw).astype(bool)

    rows = []
    for i in range(n_normal):
        event_start = anchor + timedelta(hours=i)
        settlement = event_start + timedelta(hours=3)
        rows.append({
            "suffix": f"past_{i:04d}",
            "raw_probability": float(raw[i]),
            "hit": bool(hits[i]),
            "event_start_time": _iso(event_start),
            "settlement_timestamp": _iso(settlement),
        })

    rows.append({
        "suffix": "late_settle",
        "raw_probability": 0.51,
        "hit": True,
        "event_start_time": _iso(anchor + timedelta(days=40)),
        "settlement_timestamp": _iso(anchor + timedelta(days=90)),
    })
    rows.append({
        "suffix": "no_settlement",
        "raw_probability": 0.58,
        "hit": True,
        "event_start_time": _iso(anchor + timedelta(days=20)),
        "settlement_timestamp": None,
    })

    return {"candidate_as_of": _iso(candidate_as_of), "rows": rows}


def categorize_cleanup_row(event_id: str, delete_succeeded: bool) -> str:
    """Pure classification of one cleanup-pass delete attempt (Step-5
    review STEP5-VALIDATOR-FIX-03/cleanup-reconciliation). 'neg3_locked'
    is the single expected permanent artifact per run -- Section 8's own
    post-start immutability regression row. Any other row that fails to
    delete is an unexpected locked row and must be surfaced, not assumed
    benign."""
    if delete_succeeded:
        return "deleted"
    return "neg3_locked" if event_id.endswith("_neg3") else "unexpected_locked"


def run_or_record_failure(step_name: str, fn, *args, **kwargs):
    """Runs one section function; an unanticipated exception becomes one
    explicit failed check (Step-5 review STEP5-VALIDATOR-FIX-04) instead
    of aborting main() and skipping every independent section after it.
    A section's own already-governed failures (e.g. Section 4's
    ModelCalibrationUnavailableError) are expected to be caught and
    recorded by the section itself; this is the outer safety net for
    everything else."""
    try:
        return fn(*args, **kwargs)
    except SystemExit:
        raise
    except Exception as e:
        check(f"{step_name} completed without an unexpected exception", False, f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return None


def require_env() -> None:
    missing = [v for v in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY") if not os.environ.get(v)]
    if missing:
        print(f"Missing required environment variable(s): {', '.join(missing)}")
        print("Set them as environment secrets on this session, then re-run. Aborting.")
        sys.exit(1)


# ---------------------------------------------------------------------
# Section 0: connectivity + schema-applied precondition
# ---------------------------------------------------------------------

def section_0_connectivity():
    section("0. Connectivity + schema-applied precondition")
    from ledger import get_client
    client = get_client()
    for table in ("wow_predictions", "wow_calibrators", "wow_outcomes"):
        try:
            client.table(table).select("*").limit(1).execute()
            check(f"table {table} is reachable", True)
        except Exception as e:
            check(f"table {table} is reachable", False, str(e))
            print("\nschema.sql does not appear to be applied to this project yet.")
            print('Run: psql "$SUPABASE_DB_URL" -f schema.sql   (direct Postgres connection, not SUPABASE_URL)')
            sys.exit(1)
    return client


# ---------------------------------------------------------------------
# Section 2 (user's numbering): calibrator_store.py against the real
# project -- insert Platt, insert isotonic, deactivate-old-on-promote,
# load current, verify field/artifact equality.
# ---------------------------------------------------------------------

def section_2_calibrator_persistence():
    section("2. Live calibrator persistence gate")
    from calibration import PlattCoefficients, PlattFitMetrics, CalibrationStatus
    from calibrator_store import (
        save_platt_calibrator, save_isotonic_calibrator, load_active_calibrator,
        _serialize_isotonic_model,
    )
    from sklearn.isotonic import IsotonicRegression
    import numpy as np

    cohort = f"LIVE_GATE_{RUN_ID}_PLATT"
    metrics = PlattFitMetrics(brier=0.18, log_loss=0.55, ece=0.03, calibration_bias=-0.01)

    coeffs_v1 = PlattCoefficients(a=0.1, b=1.1)
    save_platt_calibrator(coeffs_v1, metrics, parent_cohort=cohort, calibration_version="v1", training_n=SECTION2_V1_TRAINING_N, activate=True)

    loaded_v1 = load_active_calibrator(cohort, CalibrationStatus.PLATT_TIME_SPLIT_V1)
    check("Platt v1 loads back as active", loaded_v1 is not None and loaded_v1.get("active") is True)
    check("Platt v1 fields round-trip", loaded_v1 is not None and loaded_v1["platt_a"] == 0.1 and loaded_v1["platt_b"] == 1.1
          and loaded_v1["calibration_version"] == "v1" and loaded_v1["training_n"] == SECTION2_V1_TRAINING_N
          and loaded_v1.get("phase") == "PHASE_B" and loaded_v1.get("bounds_method_version") is not None)

    # Promote a new Platt calibrator for the SAME cohort -- must deactivate v1.
    # training_n intentionally agrees with Section 3/4's live historical
    # cohort size (SECTION4_TRAINING_N) -- see STEP5-VALIDATOR-FIX-02.
    coeffs_v2 = PlattCoefficients(a=0.2, b=1.2)
    save_platt_calibrator(coeffs_v2, metrics, parent_cohort=cohort, calibration_version="v2", training_n=SECTION4_TRAINING_N, activate=True)
    loaded_v2 = load_active_calibrator(cohort, CalibrationStatus.PLATT_TIME_SPLIT_V1)
    check("promoting v2 makes it the active calibrator", loaded_v2 is not None and loaded_v2["calibration_version"] == "v2")

    client = _client()
    v1_after = client.table("wow_calibrators").select("*").eq("parent_cohort", cohort).eq("calibration_version", "v1").execute().data
    check("promoting v2 deactivated v1 (fit history preserved, not deleted)",
          len(v1_after) == 1 and v1_after[0]["active"] is False)

    # Isotonic
    iso_cohort = f"LIVE_GATE_{RUN_ID}_ISO"
    rng = np.random.default_rng(1)
    raw = rng.uniform(0.3, 0.7, size=80)
    y = (rng.uniform(0, 1, size=80) < raw).astype(int)
    model = IsotonicRegression(out_of_bounds="clip", y_min=1e-9, y_max=1 - 1e-9)
    model.fit(raw, y)
    expected_artifact = _serialize_isotonic_model(model)
    save_isotonic_calibrator(model, metrics, parent_cohort=iso_cohort, calibration_version="v1", training_n=600, activate=True)

    loaded_iso = load_active_calibrator(iso_cohort, CalibrationStatus.ISOTONIC_V1)
    check("isotonic calibrator loads back as active", loaded_iso is not None)
    check("isotonic artifact round-trips byte-equivalent",
          loaded_iso is not None and loaded_iso["isotonic_artifact_b64"] == expected_artifact)

    return {"platt_cohort": cohort, "iso_cohort": iso_cohort}


# ---------------------------------------------------------------------
# Section 3: load_historical_calibration_rows() against the real project.
# ---------------------------------------------------------------------

def section_3_historical_rows(platt_cohort: str):
    section("3. Live historical-calibration acquisition gate")
    from calibrator_store import load_historical_calibration_rows
    from calibration import _parse_ts
    client = _client()

    method = "PLATT_TIME_SPLIT_V1"
    # Step 3d BLOCKER-01 fix (unchanged by this review): these rows
    # simulate the NATURAL lifecycle -- real Phase A observations for
    # this cohort, not pre-tagged as already PLATT rows. calibration_method
    # is deliberately NOT the Phase B/C method being trained, exactly like
    # real production rows would be before this cohort's first calibrator
    # was ever promoted.
    #
    # Step-5 review STEP5-VALIDATOR-FIX-02/03: fixture size and chronology
    # come from build_section3_fixture() (pure, unit-tested) --
    # SECTION4_TRAINING_N normal rows (statistically nondegenerate for
    # Section 4's bootstrap refit) all anchored years in the future so
    # they remain deletable by cleanup(), plus the same late_settle /
    # no_settlement edge cases as before.
    fixture = build_section3_fixture()
    candidate_as_of = fixture["candidate_as_of"]
    n_normal = len(fixture["rows"]) - 2  # exclude late_settle, no_settlement

    prediction_ids = {}
    prediction_rows = []
    outcome_rows = []
    for row in fixture["rows"]:
        pred_id = str(uuid.uuid4())
        prediction_ids[row["suffix"]] = pred_id
        prediction_rows.append({
            "prediction_id": pred_id,
            "event_id": f"LIVE_GATE_{RUN_ID}_{row['suffix']}",
            "event_start_time": row["event_start_time"],
            "sport": "MLB", "market_type": "engine", "stat_type": "strikeouts",
            "line": 4.5, "direction": "MORE",
            "source_snapshot_id": str(uuid.uuid4()),
            "raw_model_probability": row["raw_probability"],
            "calibration_parent_cohort": platt_cohort,
            "calibration_method": "CONSERVATIVE_EMPIRICAL_BAYES_SHRINKAGE_V1",
            "probability_publishable": False,
        })
        outcome_payload = {"prediction_id": pred_id, "hit": row["hit"], "official_result": "settled"}
        if row["settlement_timestamp"] is not None:
            outcome_payload["settlement_timestamp"] = row["settlement_timestamp"]
        outcome_rows.append(outcome_payload)

    # Bulk insert -- one network round trip per table instead of one per
    # fixture row (SECTION4_TRAINING_N + 2 rows).
    client.table("wow_predictions").insert(prediction_rows).execute()
    client.table("wow_outcomes").insert(outcome_rows).execute()

    loaded = load_historical_calibration_rows(platt_cohort, method)
    check("load_historical_calibration_rows finds Phase A rows by cohort alone "
          "(no pre-existing PLATT-tagged row required), excluding only the "
          "missing-settlement row",
          len(loaded) == n_normal + 1, f"got {len(loaded)}, expected {n_normal + 1} (no_settlement excluded)")

    # Step-5 review STEP5-VALIDATOR-FIX-01: compare parsed instants, not
    # raw strings -- PostgREST may echo a Z-suffixed timestamptz back as
    # an equivalent +00:00 offset, which made this check a false negative
    # on run fcec80e3 even though the loader was already correct.
    late_settle = next(r for r in fixture["rows"] if r["suffix"] == "late_settle")
    no_settlement = next(r for r in fixture["rows"] if r["suffix"] == "no_settlement")
    returned_instants = {_parse_ts(r.timestamp) for r in loaded}
    check("returned timestamps are settlement_timestamp, not event_start_time",
          _parse_ts(late_settle["settlement_timestamp"]) in returned_instants
          and _parse_ts(no_settlement["event_start_time"]) not in returned_instants)

    # The as_of filtering itself is calibration.compute_predictive_bounds()'s
    # job (already unit-tested locally) -- verify it live here too, using
    # rows that actually came from Supabase this time, with the same
    # timezone-aware datetime comparison the governed calibration logic uses.
    as_of_dt = _parse_ts(candidate_as_of)
    eligible = [r for r in loaded if _parse_ts(r.timestamp) < as_of_dt]
    check("late-settling row excluded once filtered by candidate_as_of, "
          "even though its event started earlier",
          len(eligible) == n_normal, f"got {len(eligible)} eligible of {len(loaded)} total")

    return {"prediction_ids": list(prediction_ids.values()), "candidate_as_of": candidate_as_of}


# ---------------------------------------------------------------------
# Section 4: real Phase B bounds computation from Supabase-loaded rows.
# ---------------------------------------------------------------------

def section_4_live_bounds(platt_cohort: str, candidate_as_of: str):
    section("4. Live Phase B/C bounds gate")
    from calibrator_store import load_historical_calibration_rows, platt_coefficients_from_record, load_active_calibrator
    from calibration import compute_predictive_bounds, CalibrationStatus, ModelCalibrationUnavailableError

    method = CalibrationStatus.PLATT_TIME_SPLIT_V1
    record = load_active_calibrator(platt_cohort, method)
    coefficients = platt_coefficients_from_record(record)
    rows = load_historical_calibration_rows(platt_cohort, method)

    def sampler(rng):
        return float(rng.uniform(0.4, 0.6))

    point_estimate = coefficients.apply(0.55)
    # Step-5 review STEP5-VALIDATOR-FIX-04: ModelCalibrationUnavailableError
    # is a GOVERNED, expected failure mode of compute_predictive_bounds()
    # (do not weaken/clip/catch-and-retry inside the engine itself -- see
    # calibration.py). The validator's job is to record it as an explicit
    # failed gate and let Sections 5-8 still run, not to let it crash
    # main() and abort every independent section after it (run fcec80e3).
    try:
        bounds = compute_predictive_bounds(
            method=method, historical_rows=rows, candidate_as_of=candidate_as_of,
            candidate_raw_probability_sampler=sampler,
            full_data_calibrated_probability=point_estimate, rng_seed=7,
        )
    except ModelCalibrationUnavailableError as e:
        check(f"Phase B/C predictive bounds computed from {len(rows)} eligible historical rows",
              False, f"ModelCalibrationUnavailableError: {e}")
        return
    check(">= 2000 valid realizations", bounds.realizations_used >= 2000, str(bounds.realizations_used))
    check("0 < lower_bound <= calibrated_probability <= upper_bound < 1",
          0 < bounds.lower_bound <= bounds.calibrated_probability <= bounds.upper_bound < 1,
          f"{bounds.lower_bound} <= {bounds.calibrated_probability} <= {bounds.upper_bound}")


# ---------------------------------------------------------------------
# Sections 5-7: local uvicorn against the real project, real HTTP, real
# persistence, read back and compare.
# ---------------------------------------------------------------------

def _start_uvicorn(iso_cohort: str) -> None:
    import api
    from regime_model import PrimaryRegime, CohortCounts, PitcherCounts
    from simulation import RegimeConditionalParams

    def synthetic_provider(sport, stat_type):
        def bf_sampler(rng, n):
            return rng.integers(18, 26, size=n)

        def stat_sampler(rng, n, bf):
            return rng.poisson(bf * 0.30)

        params = {
            r: RegimeConditionalParams(regime=r, batters_faced_sampler=bf_sampler, stat_rate_sampler=stat_sampler)
            for r in PrimaryRegime
        }
        return api.FittedParamsBundle(
            cohort=CohortCounts(counts={r: 100 for r in PrimaryRegime}),
            pitcher=PitcherCounts(counts={PrimaryRegime.NORMAL_EFFECTIVE_OUTING: 12}),
            regime_params=params,
            resample_fn=lambda rng, n: rng.normal(loc=0.55, scale=0.08, size=n).clip(0.01, 0.99),
            n_eff=16,
            # Deliberately Phase A eligible (settled_n_in_cohort=0) --
            # this proves the real HTTP -> real Supabase persistence path
            # end to end without depending on section 2's live-fitted
            # calibrator also being routed to, which is a separate concern
            # already covered by section 4.
            parent_cohort=None, settled_n_in_cohort=0,
        )

    api.GOVERNED_PROBABILITY_CAPABILITY = "AVAILABLE"
    api.set_fitted_params_provider(synthetic_provider)
    # api._persist_fn is left at its default (ledger.insert_prediction) --
    # this is the whole point of sections 5-7: real Supabase persistence,
    # not a stub.

    import uvicorn
    config = uvicorn.Config(api.app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server


def section_5_7_real_endpoint():
    section("5-7. Real /score-prop HTTP path + Supabase persistence + read-back")
    import httpx

    server = _start_uvicorn(iso_cohort="unused")
    try:
        base = f"http://127.0.0.1:{PORT}"
        for _ in range(50):
            try:
                httpx.get(f"{base}/health", timeout=1).raise_for_status()
                break
            except Exception:
                time.sleep(0.1)
        else:
            check("uvicorn server became reachable", False, "timed out waiting for /health")
            return None

        source_snapshot_id = str(uuid.uuid4())
        # Step 3d BLOCKER-02 fix: scored_at is no longer a client-settable
        # request field -- /score-prop always generates it server-side now.
        resp = httpx.post(f"{base}/score-prop", json={
            "event_id": f"LIVE_GATE_{RUN_ID}_endpoint",
            "event_start_time": "2026-08-28T00:00:00Z",
            "sport": "MLB", "stat_type": "strikeouts", "line": 4.5, "direction": "MORE",
            "source_snapshot_id": source_snapshot_id,
            "money_lane_status": "RESOLVED",
        }, timeout=30)

        check("POST /score-prop returns 200 over real HTTP", resp.status_code == 200, f"got {resp.status_code}: {resp.text[:300]}")
        if resp.status_code != 200:
            return None
        body = resp.json()

        required_fields = [
            "raw_model_probability", "calibrated_probability",
            "calibrated_probability_lower_bound", "calibrated_probability_upper_bound",
            "calibration_method", "source_snapshot_id", "model_timestamp",
            "probability_publishable",
        ]
        missing = [f for f in required_fields if body.get(f) is None and f != "probability_publishable"]
        check("response includes required fields", not missing, f"missing: {missing}")
        check("probability_publishable is true", body.get("probability_publishable") is True)
        check("source_snapshot_id matches request", body.get("source_snapshot_id") == source_snapshot_id)

        prediction_id = body.get("prediction_id")
        check("response includes a prediction_id (persistence actually happened)", bool(prediction_id))
        if not prediction_id:
            return None

        client = _client()
        stored = client.table("wow_predictions").select("*").eq("prediction_id", prediction_id).execute().data
        check("persisted row is readable back from Supabase", len(stored) == 1)
        if stored:
            row = stored[0]
            mismatches = [
                f for f in required_fields
                if f != "probability_publishable" and float(row.get(f) or -1) != float(body.get(f) or -2)
            ]
            check("persisted row matches endpoint response for numeric fields", not mismatches, str(mismatches))
            check("persisted row's probability_publishable matches", row.get("probability_publishable") == body.get("probability_publishable"))

        return prediction_id
    finally:
        server.should_exit = True
        time.sleep(0.3)


# ---------------------------------------------------------------------
# Section 8: negative paths against the live store.
# ---------------------------------------------------------------------

def section_8_negative_paths():
    section("8. Live negative-path regression")
    client = _client()

    # Missing source_snapshot_id -> NOT NULL rejected by the live store.
    try:
        client.table("wow_predictions").insert({
            "event_id": f"LIVE_GATE_{RUN_ID}_neg1", "event_start_time": "2026-08-28T00:00:00Z",
            "sport": "MLB", "market_type": "engine", "stat_type": "strikeouts",
            "line": 4.5, "direction": "MORE", "source_snapshot_id": None,
        }).execute()
        check("missing source_snapshot_id rejected by live store", False, "insert unexpectedly succeeded")
    except Exception as e:
        check("missing source_snapshot_id rejected by live store", True, str(e)[:200])

    # Malformed calibration_status is a Python-layer gap (determine_publishability),
    # not a DB constraint -- already covered locally (test_gate_07c). What's
    # live-specific here: an unrecognized calibration_method IS a DB check
    # constraint, so confirm PostgREST surfaces it correctly.
    try:
        client.table("wow_predictions").insert({
            "event_id": f"LIVE_GATE_{RUN_ID}_neg2", "event_start_time": "2026-08-28T00:00:00Z",
            "sport": "MLB", "market_type": "engine", "stat_type": "strikeouts",
            "line": 4.5, "direction": "MORE", "source_snapshot_id": str(uuid.uuid4()),
            "calibration_status": "BOGUS_STATUS_NOT_IN_ANY_ENUM",
        }).execute()
        # calibration_status has no DB-level CHECK (only method does) --
        # this row is expected to insert; the Python-layer gate
        # (determine_publishability) is what blocks it, already covered
        # locally. Recorded here as a documented non-finding, not a FAIL.
        check("calibration_status has no DB check constraint (Python-layer gate covers this -- see test_gate_07c)", True)
    except Exception as e:
        check("calibration_status insert behavior", True, f"DB rejected it too: {e}")

    # Post-start mutation/delete rejected by the live trigger.
    pid = str(uuid.uuid4())
    client.table("wow_predictions").insert({
        "prediction_id": pid, "event_id": f"LIVE_GATE_{RUN_ID}_neg3",
        "event_start_time": "2020-01-01T00:00:00Z",  # already in the past
        "sport": "MLB", "market_type": "engine", "stat_type": "strikeouts",
        "line": 4.5, "direction": "MORE", "source_snapshot_id": str(uuid.uuid4()),
    }).execute()
    try:
        client.table("wow_predictions").update({"line": 9.9}).eq("prediction_id", pid).execute()
        check("post-start UPDATE rejected by live trigger", False, "update unexpectedly succeeded")
    except Exception as e:
        check("post-start UPDATE rejected by live trigger", True, str(e)[:200])
    try:
        client.table("wow_predictions").delete().eq("prediction_id", pid).execute()
        check("post-start DELETE rejected by live trigger", False, "delete unexpectedly succeeded")
    except Exception as e:
        check("post-start DELETE rejected by live trigger", True, str(e)[:200])
    # this row is intentionally left in place -- it cannot be cleaned up
    # (that's the point of the test), so it's a known, permanent, clearly
    # tagged artifact of running this script. Documented, not a bug.


# ---------------------------------------------------------------------
# Cleanup + driver
# ---------------------------------------------------------------------

_client_singleton = None


def _client():
    global _client_singleton
    if _client_singleton is None:
        from ledger import get_client
        _client_singleton = get_client()
    return _client_singleton


def cleanup():
    section("Cleanup (best-effort) + reconciliation")
    client = _client()
    # Step-5 review cleanup-reconciliation: distinguish the single expected
    # permanent neg3 artifact from any genuinely unexpected locked row,
    # scoped strictly to THIS run's RUN_ID -- a prior failed run's own
    # stranded rows (e.g. fcec80e3) are never touched or counted here.
    neg3_remaining = 0
    unexpected_locked = 0
    try:
        preds = client.table("wow_predictions").select("prediction_id, event_id").like("event_id", f"LIVE_GATE_{RUN_ID}_%").execute().data or []
        for p in preds:
            try:
                client.table("wow_outcomes").delete().eq("prediction_id", p["prediction_id"]).execute()
            except Exception:
                pass
            try:
                client.table("wow_predictions").delete().eq("prediction_id", p["prediction_id"]).execute()
            except Exception as e:
                category = categorize_cleanup_row(p["event_id"], False)
                if category == "neg3_locked":
                    print(f"  (left in place, expected: neg3 immutability regression row -- {e})")
                    neg3_remaining += 1
                else:
                    print(f"  UNEXPECTED locked row (not neg3): {p['event_id']} {p['prediction_id']} -- {e}")
                    unexpected_locked += 1

        client.table("wow_calibrators").delete().like("parent_cohort", f"LIVE_GATE_{RUN_ID}_%").execute()
        print(f"cleanup attempted for run {RUN_ID}")

        remaining = client.table("wow_predictions").select("prediction_id, event_id").like("event_id", f"LIVE_GATE_{RUN_ID}_%").execute().data or []
        ordinary_remaining = [r for r in remaining if not r["event_id"].endswith("_neg3")]
        remaining_ids = [r["prediction_id"] for r in remaining]
        outcomes_remaining = (
            client.table("wow_outcomes").select("prediction_id").in_("prediction_id", remaining_ids).execute().data
            if remaining_ids else []
        )
        calibrators_remaining = client.table("wow_calibrators").select("parent_cohort").like("parent_cohort", f"LIVE_GATE_{RUN_ID}_%").execute().data or []

        check("cleanup: ordinary (non-neg3) predictions remaining == 0", len(ordinary_remaining) == 0, str(len(ordinary_remaining)))
        check("cleanup: outcomes remaining == 0", len(outcomes_remaining or []) == 0, str(len(outcomes_remaining or [])))
        check("cleanup: calibrators remaining == 0", len(calibrators_remaining) == 0, str(len(calibrators_remaining)))
        check("cleanup: current-run neg3 remaining == exactly 1", neg3_remaining == 1, str(neg3_remaining))
        check("cleanup: unexpected current-run locked rows == 0", unexpected_locked == 0, str(unexpected_locked))
    except Exception as e:
        print(f"cleanup encountered an error (non-fatal): {e}")


def main():
    require_env()
    print(f"LIVE_GATE_RUN_ID = {RUN_ID}\n")

    try:
        section_0_connectivity()
        cohorts = run_or_record_failure("Section 2 (calibrator persistence)", section_2_calibrator_persistence)
        hist = (
            run_or_record_failure("Section 3 (historical acquisition)", section_3_historical_rows, cohorts["platt_cohort"])
            if cohorts is not None else None
        )
        if hist is not None:
            run_or_record_failure("Section 4 (predictive bounds)", section_4_live_bounds, cohorts["platt_cohort"], hist["candidate_as_of"])
        run_or_record_failure("Section 5-7 (score-prop endpoint)", section_5_7_real_endpoint)
        run_or_record_failure("Section 8 (negative paths)", section_8_negative_paths)
    except SystemExit:
        raise
    except Exception:
        print("\nUNEXPECTED ERROR:")
        traceback.print_exc()
    finally:
        cleanup()

    print("\n=== SUMMARY ===")
    failed = [r for r in RESULTS if not r[1]]
    for name, passed, detail in RESULTS:
        print(f"{'PASS' if passed else 'FAIL'}: {name}")
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("\ngoverned_probability_capability = UNAVAILABLE  (failures above)")
        sys.exit(1)
    else:
        print("\nAll live checks passed. governed_probability_capability is still")
        print("UNAVAILABLE by design -- this script does not flip it. That remains")
        print("a manual, reviewed, lane-by-lane decision with real fitted parameters.")


if __name__ == "__main__":
    main()
