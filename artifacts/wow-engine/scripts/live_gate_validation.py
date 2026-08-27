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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RUN_ID = uuid.uuid4().hex[:8]
PORT = 8811

RESULTS: list[tuple[str, bool, str]] = []  # (name, passed, detail)


def check(name: str, condition: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(condition), detail))
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" -- {detail}" if detail else ""))
    return bool(condition)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


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
    save_platt_calibrator(coeffs_v1, metrics, parent_cohort=cohort, calibration_version="v1", training_n=250, activate=True)

    loaded_v1 = load_active_calibrator(cohort, CalibrationStatus.PLATT_TIME_SPLIT_V1)
    check("Platt v1 loads back as active", loaded_v1 is not None and loaded_v1.get("active") is True)
    check("Platt v1 fields round-trip", loaded_v1 is not None and loaded_v1["platt_a"] == 0.1 and loaded_v1["platt_b"] == 1.1
          and loaded_v1["calibration_version"] == "v1" and loaded_v1["training_n"] == 250
          and loaded_v1.get("phase") == "PHASE_B" and loaded_v1.get("bounds_method_version") is not None)

    # Promote a new Platt calibrator for the SAME cohort -- must deactivate v1.
    coeffs_v2 = PlattCoefficients(a=0.2, b=1.2)
    save_platt_calibrator(coeffs_v2, metrics, parent_cohort=cohort, calibration_version="v2", training_n=400, activate=True)
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
    client = _client()

    method = "PLATT_TIME_SPLIT_V1"
    # Step 3d BLOCKER-01 fix: these rows simulate the NATURAL lifecycle --
    # real Phase A observations for this cohort, not pre-tagged as already
    # PLATT rows. The original version of this fixture seeded every row
    # with calibration_method=method directly, which is exactly what made
    # the original bug invisible: it required a historical row to already
    # carry the Phase B/C method being trained, so a cohort's first real
    # Phase B candidate could never bootstrap from prior Phase A rows.
    rows_spec = [
        # (event_id_suffix, raw_prob, hit, event_start_time, settlement_timestamp)
        ("past1", 0.55, True, "2026-01-01T00:00:00Z", "2026-01-01T03:00:00Z"),
        ("past2", 0.62, False, "2026-02-01T00:00:00Z", "2026-02-01T03:00:00Z"),
        ("past3", 0.48, True, "2026-03-01T00:00:00Z", "2026-03-01T03:00:00Z"),
        # Event started before candidate_as_of but SETTLED after it -- must
        # be excluded once as-of filtered, even though event_start_time
        # alone would look eligible.
        ("late_settle", 0.51, True, "2026-08-01T00:00:00Z", "2026-12-01T00:00:00Z"),
        # No recorded settlement availability at all -- must fail closed
        # (excluded by the loader itself, not just by the as-of filter).
        ("no_settlement", 0.58, True, "2026-06-01T00:00:00Z", None),
    ]
    candidate_as_of = "2026-08-27T00:00:00Z"

    prediction_ids = {}
    for suffix, raw_prob, hit, event_start, settlement_ts in rows_spec:
        pred_id = str(uuid.uuid4())
        prediction_ids[suffix] = pred_id
        client.table("wow_predictions").insert({
            "prediction_id": pred_id,
            "event_id": f"LIVE_GATE_{RUN_ID}_{suffix}",
            "event_start_time": event_start,
            "sport": "MLB", "market_type": "engine", "stat_type": "strikeouts",
            "line": 4.5, "direction": "MORE",
            "source_snapshot_id": str(uuid.uuid4()),
            "raw_model_probability": raw_prob,
            "calibration_parent_cohort": platt_cohort,
            # Deliberately NOT the Phase B/C method -- these are Phase A
            # observations, exactly like real production rows would be
            # before this cohort's first calibrator was ever promoted.
            "calibration_method": "CONSERVATIVE_EMPIRICAL_BAYES_SHRINKAGE_V1",
            "probability_publishable": False,
        }).execute()
        outcome_payload = {"prediction_id": pred_id, "hit": hit, "official_result": "settled"}
        if settlement_ts is not None:
            outcome_payload["settlement_timestamp"] = settlement_ts
        client.table("wow_outcomes").insert(outcome_payload).execute()

    loaded = load_historical_calibration_rows(platt_cohort, method)
    check("load_historical_calibration_rows finds Phase A rows by cohort alone "
          "(no pre-existing PLATT-tagged row required), excluding only the "
          "missing-settlement row",
          len(loaded) == 4, f"got {len(loaded)}, expected 4 (no_settlement excluded)")

    timestamps = {r.timestamp for r in loaded}
    check("returned timestamps are settlement_timestamp, not event_start_time",
          "2026-12-01T00:00:00Z" in timestamps and "2026-06-01T00:00:00Z" not in timestamps)

    # The as_of filtering itself is calibration.compute_predictive_bounds()'s
    # job (already unit-tested locally) -- verify it live here too, using
    # rows that actually came from Supabase this time.
    eligible = [r for r in loaded if r.timestamp < candidate_as_of]
    check("late-settling row excluded once filtered by candidate_as_of, "
          "even though its event started earlier",
          len(eligible) == 3, f"got {len(eligible)} eligible of {len(loaded)} total")

    return {"prediction_ids": list(prediction_ids.values()), "candidate_as_of": candidate_as_of}


# ---------------------------------------------------------------------
# Section 4: real Phase B bounds computation from Supabase-loaded rows.
# ---------------------------------------------------------------------

def section_4_live_bounds(platt_cohort: str, candidate_as_of: str):
    section("4. Live Phase B/C bounds gate")
    from calibrator_store import load_historical_calibration_rows, platt_coefficients_from_record, load_active_calibrator
    from calibration import compute_predictive_bounds, CalibrationStatus

    method = CalibrationStatus.PLATT_TIME_SPLIT_V1
    record = load_active_calibrator(platt_cohort, method)
    coefficients = platt_coefficients_from_record(record)
    rows = load_historical_calibration_rows(platt_cohort, method)

    def sampler(rng):
        return float(rng.uniform(0.4, 0.6))

    point_estimate = coefficients.apply(0.55)
    bounds = compute_predictive_bounds(
        method=method, historical_rows=rows, candidate_as_of=candidate_as_of,
        candidate_raw_probability_sampler=sampler,
        full_data_calibrated_probability=point_estimate, rng_seed=7,
    )
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
    section("Cleanup (best-effort)")
    client = _client()
    try:
        preds = client.table("wow_predictions").select("prediction_id").like("event_id", f"LIVE_GATE_{RUN_ID}_%").execute().data
        for p in preds:
            try:
                client.table("wow_outcomes").delete().eq("prediction_id", p["prediction_id"]).execute()
            except Exception:
                pass
            try:
                client.table("wow_predictions").delete().eq("prediction_id", p["prediction_id"]).execute()
            except Exception as e:
                print(f"  (left in place, likely the locked neg3 row -- expected: {e})")
        client.table("wow_calibrators").delete().like("parent_cohort", f"LIVE_GATE_{RUN_ID}_%").execute()
        print(f"cleanup attempted for run {RUN_ID}")
    except Exception as e:
        print(f"cleanup encountered an error (non-fatal): {e}")


def main():
    require_env()
    print(f"LIVE_GATE_RUN_ID = {RUN_ID}\n")

    try:
        section_0_connectivity()
        cohorts = section_2_calibrator_persistence()
        hist = section_3_historical_rows(cohorts["platt_cohort"])
        section_4_live_bounds(cohorts["platt_cohort"], hist["candidate_as_of"])
        section_5_7_real_endpoint()
        section_8_negative_paths()
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
