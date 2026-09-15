"""Acceptance replay for the V17 Daily response/terminal incident.

Reproduces and pins the repaired behaviour for:
  PRIMARY   DAILY_RESPONSE_SERIALIZATION_TOO_LARGE
  CRITICAL  TERMINAL_REDUCER_UPGRADE (NO_LOW_PROBABILITY -> HELD)
  SECONDARY ZERO_ROW_REASON_MISCLASSIFIED
            1IP exact-line REJECT_OOD gate must NOT be weakened
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from v17.daily_response_contract import read_row_detail_page, serialized_byte_size
from v17.daily_snapshot_runtime import DailySnapshotRequest, run_daily_snapshot

FUTURE_DT = datetime.now(timezone.utc) + timedelta(hours=12)
FUTURE = FUTURE_DT.isoformat()
SLATE_DATE = FUTURE_DT.astimezone(ZoneInfo("America/Chicago")).date().isoformat()

# OpenAI GPT Actions rejects oversized Action results with ResponseTooLargeError.
# The documented working budget for this repository is ~100KB.
CLIENT_RESPONSE_LIMIT_BYTES = 100_000
COMPACT_RESPONSE_BUDGET_BYTES = 50_000


def _prop_snapshot(index: int) -> dict:
    return {
        "source_snapshot_id": f"snap-{index}",
        "event_id": f"MLB:{index}",
        "event_start_time": FUTURE,
        "sport": "MLB",
        "player": f"Pitcher {index}",
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 5.5,
        "hydration_status": "PASS",
        "blockers": [],
    }


def _governed_full_payload(direction: str) -> dict:
    """A payload shaped and sized like a real governed V17 prop package."""
    return {
        "ok": True,
        "prediction": {
            "prediction_id": f"pred-{direction}",
            "calibrated_probability": 0.41,
            "calibrated_probability_lower_bound": 0.36,
            "calibrated_probability_upper_bound": 0.47,
            "calibration_status": "PASS",
            "frozen_identity": {"line": 5.5, "direction": direction, "period": "FULL_GAME"},
        },
        "acquisition_evidence": {"attempts": [{"source": f"src-{i}", "status": "PASS"} for i in range(12)]},
        "acquisition_repair": {"attempts": [{"n": i, "detail": "x" * 60} for i in range(8)]},
        "model_evidence": {
            "raw_distribution": [round(i / 512, 6) for i in range(160)],
            "directional_probability_assessments": {
                "MORE": {"qualification_reasons": ["r" * 40] * 6},
                "LESS": {"qualification_reasons": ["r" * 40] * 6},
            },
        },
        "evidence": {
            "game_log": [float(i) for i in range(30)],
            "box_score_log": [{"g": i, "detail": "d" * 90} for i in range(12)],
            "opportunity_ledger": {f"k{i}": "v" * 70 for i in range(14)},
            "role_status": {f"r{i}": "s" * 50 for i in range(10)},
        },
        "objective_lanes": {
            lane: {"status": "HOLD", "notes": "n" * 110} for lane in ("MODEL", "MARKET", "SETTLEMENT", "MONEY", "ARITHMETIC_AUDIT")
        },
        "backend_traversal": {f"stage_{i}": "PASS" for i in range(11)},
        "route_preflight": {"status": "PASS", "certified_artifact_code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY"},
        "probability_qualification": {
            "terminal_label": "NO_LOW_PROBABILITY",
            "verdict_class": "MODEL_REJECTED",
            "pick_rejected": True,
            "model_evaluated": True,
            "model_qualified": False,
            "probability_rank_eligible": False,
            "blockers": ["PAYOUT_UNRESOLVED"],
        },
        "terminal_label": "NO_LOW_PROBABILITY",
        "pick_rejected": True,
        "model_evaluated": True,
        "model_qualified": False,
        "probability_rank_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, data):
        self.data = data
        self.written = None

    def select(self, *_):
        return self

    def eq(self, *_):
        return self

    def order(self, *_, **__):
        return self

    def limit(self, *_):
        return self

    def upsert(self, payload, *_args, **_kwargs):
        self.written = payload
        return self

    def execute(self):
        return _Result(self.data)


class _DB:
    """Canonical-manifest double that also captures persisted row detail."""

    def __init__(self, prop_rows):
        self.prop_rows = prop_rows
        self.detail_rows = []

    def table(self, name):
        if name == "wow_prop_evidence_snapshots":
            return _Query(self.prop_rows)
        if name == "wow_v17_daily_run_row_detail":
            query = _Query(self.detail_rows)
            original_upsert = query.upsert

            def capture(payload, *args, **kwargs):
                self.detail_rows.extend(payload)
                return original_upsert(payload, *args, **kwargs)

            query.upsert = capture
            return query
        return _Query([])


class _RejectingMarket:
    """Controlling model that evaluates and hard-rejects both directions."""

    class ScorePropRequest:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    @staticmethod
    def score_prop(req, _identity):
        return _governed_full_payload(req.kwargs.get("direction", "MORE"))


def _run(db, market, **overrides):
    request = DailySnapshotRequest(
        requested_slate_date=SLATE_DATE,
        requested_timezone="America/Chicago",
        lanes=["PROPS"],
        **overrides,
    )
    return run_daily_snapshot(request, db=db, market_api=market, event_api=object())


# --- PRIMARY: DAILY_RESPONSE_SERIALIZATION_TOO_LARGE -------------------------

def test_twelve_row_daily_run_stays_within_client_response_budget():
    db = _DB([_prop_snapshot(i) for i in range(12)])
    compact = _run(db, _RejectingMarket(), max_props=12)

    assert len(compact["rows"]) == 12
    assert compact["response_mode"] == "COMPACT"
    size = serialized_byte_size(compact)
    assert size < COMPACT_RESPONSE_BUDGET_BYTES, f"compact 12-row response was {size} bytes"
    assert size < CLIENT_RESPONSE_LIMIT_BYTES


def test_full_mode_reproduces_the_oversized_response_the_compact_contract_avoids():
    db = _DB([_prop_snapshot(i) for i in range(12)])
    full = _run(db, _RejectingMarket(), max_props=12, response_mode="FULL")

    assert full["response_mode"] == "FULL"
    assert serialized_byte_size(full) > CLIENT_RESPONSE_LIMIT_BYTES
    # The repair is transport-only: the same run compacts under budget.
    assert serialized_byte_size(_run(_DB([_prop_snapshot(i) for i in range(12)]), _RejectingMarket(), max_props=12)) < COMPACT_RESPONSE_BUDGET_BYTES


def test_compact_rows_keep_terminal_and_probability_fields():
    db = _DB([_prop_snapshot(0)])
    compact = _run(db, _RejectingMarket(), max_props=1)
    direction = compact["rows"][0]["directions"][0]

    assert direction["terminal_label"] == "NO_LOW_PROBABILITY"
    assert direction["pick_rejected"] is True
    assert direction["calibrated_probability"] == 0.41
    assert direction["calibrated_probability_lower_bound"] == 0.36
    assert direction["can_execute"] is False


def test_full_evidence_is_retrievable_in_pages_after_compaction():
    db = _DB([_prop_snapshot(i) for i in range(12)])
    compact = _run(db, _RejectingMarket(), max_props=12)

    assert compact["row_detail_retrieval"]["detail_available"] is True
    assert compact["rows"][0]["detail_ref"]["detail_available"] is True

    page = read_row_detail_page(db, run_id=compact["run_id"], offset=0, limit=5)
    assert page["returned"] == 5
    assert page["next_offset"] == 5
    # Nothing was discarded: the untrimmed governed package is still present.
    detail = page["rows"][0]["detail"]
    assert detail["result"]["outcomes"][0]["payload"]["evidence"]["box_score_log"]
    assert detail["result"]["outcomes"][0]["payload"]["objective_lanes"]["MARKET"]
    assert page["can_execute"] is False


def test_detail_persistence_failure_fails_closed_with_typed_blocker():
    class _BrokenDetailDB(_DB):
        def table(self, name):
            if name == "wow_v17_daily_run_row_detail":
                raise ConnectionError("detail store unavailable")
            return super().table(name)

    compact = _run(_BrokenDetailDB([_prop_snapshot(0)]), _RejectingMarket(), max_props=1)
    assert compact["row_detail_retrieval"]["detail_available"] is False
    assert any("DAILY_ROW_DETAIL_PERSISTENCE_UNAVAILABLE" in b for b in compact["blockers"])
    assert compact["run_status"] == "COMPLETED_WITH_ACQUISITION_BLOCKERS"


# --- CRITICAL: TERMINAL_REDUCER_UPGRADE --------------------------------------

def test_no_low_probability_stays_rejected_through_the_outer_wrapper():
    """The reported incident: a hard inner rejection reported as HELD."""
    db = _DB([_prop_snapshot(0)])
    compact = _run(db, _RejectingMarket(), max_props=1)
    row = compact["rows"][0]

    assert row["row_status"] == "REJECTED"
    assert row["row_status"] != "HELD"
    assert row["terminal_reduction"]["lowest_stage_terminal"] == "REJECTED"
    assert row["terminal_reduction"]["final_terminal"] == "REJECTED"
    assert row["terminal_reduction"]["terminal_upgraded_from_rejection"] is False
    assert compact["reconciliation"]["rows_rejected"] == 1
    assert compact["reconciliation"]["rows_held"] == 0
    assert compact["reconciliation"]["balanced"] is True


def test_rejection_returned_as_a_typed_error_is_also_not_softened_to_held():
    class _RejectingErrorMarket(_RejectingMarket):
        @staticmethod
        def score_prop(*_args):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "MLB_1IP_LINE_OUTSIDE_CERTIFIED_SUPPORT",
                    "terminal_label": "REJECT_OOD",
                    "pick_rejected": True,
                    "model_evaluated": False,
                    "probability_publishable": False,
                    "can_execute": False,
                },
            )

    compact = _run(_DB([_prop_snapshot(0)]), _RejectingErrorMarket(), max_props=1)
    row = compact["rows"][0]
    assert row["row_status"] == "REJECTED"
    assert row["directions"][0]["terminal_label"] == "REJECT_OOD"
    assert row["directions"][0]["code"] == "MLB_1IP_LINE_OUTSIDE_CERTIFIED_SUPPORT"


def test_approved_direction_is_not_collapsed_by_its_rejected_complement():
    """A favoured MORE implies a rejected LESS; that must stay COMPLETED."""

    class _SplitMarket(_RejectingMarket):
        @staticmethod
        def score_prop(req, _identity):
            if req.kwargs.get("direction") == "MORE":
                return {"probability_publishable": True, "rank_eligible": True, "can_execute": False}
            return {"terminal_label": "NO_LOW_PROBABILITY", "pick_rejected": True, "probability_publishable": False}

    compact = _run(_DB([_prop_snapshot(0)]), _SplitMarket(), max_props=1)
    row = compact["rows"][0]
    assert row["row_status"] == "COMPLETED"
    assert row["terminal_reduction"]["approved_stage_exception_applied"] is True


def test_unevaluated_hold_is_still_held_not_rejected():
    class _HoldMarket(_RejectingMarket):
        @staticmethod
        def score_prop(*_args):
            return {"probability_publishable": False, "rank_eligible": False, "can_execute": False}

    compact = _run(_DB([_prop_snapshot(0)]), _HoldMarket(), max_props=1)
    assert compact["rows"][0]["row_status"] == "HELD"


# --- SECONDARY: ZERO_ROW_REASON_MISCLASSIFIED --------------------------------

def test_requested_limit_zero_is_not_reported_as_no_canonical_candidates():
    db = _DB([_prop_snapshot(i) for i in range(298)])
    compact = _run(db, _RejectingMarket(), max_props=0)
    lane = compact["lane_reconciliation"]["PROPS"]

    assert lane["canonicalized_count"] == 298
    assert lane["scored_count"] == 0
    assert lane["requested_row_limit"] == 0
    assert lane["zero_row_reason"] == "REQUESTED_ROW_LIMIT_ZERO"
    assert lane["zero_row_reason"] != "NO_CANONICAL_CANDIDATES"


def test_requested_limit_zero_does_not_invoke_the_acquisition_producer(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "v17.daily_snapshot_runtime.acquire_daily_prop_snapshots",
        lambda **kwargs: calls.append(kwargs) or {"status": "COMPLETED", "attempted": 0, "persisted": 0, "receipts": [], "blockers": []},
    )
    _run(_DB([_prop_snapshot(i) for i in range(298)]), _RejectingMarket(), max_props=0)
    assert calls == []


def test_genuinely_empty_lane_still_reports_no_canonical_candidates(monkeypatch):
    monkeypatch.setattr(
        "v17.daily_snapshot_runtime.acquire_daily_prop_snapshots",
        lambda **_: {"status": "COMPLETED", "attempted": 0, "persisted": 0, "receipts": [], "blockers": [], "can_execute": False},
    )
    compact = _run(_DB([]), _RejectingMarket(), max_props=6)
    assert compact["lane_reconciliation"]["PROPS"]["zero_row_reason"] == "NO_CANONICAL_CANDIDATES"


# --- route / published-contract parity ---------------------------------------

def test_published_action_contract_declares_the_paged_retrieval_route():
    """A route that exists must not be missing from the published manifest."""
    import pathlib

    import yaml

    spec = yaml.safe_load(
        (pathlib.Path(__file__).parent / "openapi.wow-betting-engine.v17.yaml").read_text()
    )
    paths = spec["paths"]
    assert "/v17/daily-snapshot-run/{run_id}/rows" in paths
    assert paths["/v17/daily-snapshot-run/{run_id}/rows"]["get"]["operationId"] == "readWowV17DailySnapshotRowDetail"

    daily_request = spec["components"]["schemas"]["DailySnapshotRequest"]
    assert daily_request["properties"]["response_mode"]["default"] == "COMPACT"
    assert daily_request["properties"]["response_mode"]["enum"] == ["COMPACT", "FULL"]
    # extra="forbid" on the model means an undocumented field would be rejected.
    assert daily_request["additionalProperties"] is False
