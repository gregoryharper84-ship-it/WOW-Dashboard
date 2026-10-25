"""October 18 NFL official-guard PASS versus full-board audit disagreement.

The stored nested NFL payload proved an official publication guard without
emitting package, dynamic-calibration, or final-refresh receipts. These tests
keep that shape fail-closed: absence is EVIDENCE_NOT_EMITTED, explicit failure
stays EVIDENCE_FAILED, and neither guard PASS nor calibration health nor a
scheduled event becomes a borrowed MLB/Flask receipt.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from v17 import daily_snapshot_runtime as runtime
from v17 import nhl_event_identity
from v17 import team_event_bridge_runtime as bridge_runtime
from v17.nhl_event_identity import NHLEventIdentityError
from v17.team_event_bridge_runtime import TeamEventBridgeRegistration
from v17.team_event_sport_parity import install_cross_sport_discovery_evidence_handoff
from v17.cross_sport_winner_discovery import MODEL_COMPLETED, OTHER_GOVERNED_HOLD
from v17.daily_response_contract import (
    DAILY_ACQUISITION_DETAIL_TABLE,
    DAILY_ROW_DETAIL_TABLE,
    read_row_detail_page,
)
from v17.daily_snapshot_runtime import DailySnapshotRequest, run_daily_snapshot
from v17.full_board_overlay import enrich_full_board_result, install_cross_sport_full_board_overlay
from v17.full_board_stabilization import publication_chain_audit
from v17.nfl_team_event_publication import _govern
from v17.stage_proof_contract import (
    CONTRACT_VERSION,
    EVIDENCE_FAILED,
    EVIDENCE_NOT_EMITTED,
    STAGE_DYNAMIC_CALIBRATION,
    STAGE_EVENT_GOVERNOR,
    STAGE_FINAL_REFRESH,
    STAGE_PACKAGE,
    STAGE_PROBABILITY_AUDIT,
    apply_scan_row_stage_proof_ceiling,
)
from v17 import cross_sport_winner_discovery as discovery

PATRIOTS_PROBABILITY = 0.8021473341678176
PATRIOTS_LOWER_BOUND = 0.7462292510592403
RUN_ID = "run-1"
EVENT_KEY = "NFL:evt"
EVENT_ID = "evt"
SCORE_ID = "score-1"


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.filters = {}
        self.bounds = None
        self.wrote = False

    def select(self, *_args):
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, limit):
        self.bounds = (0, int(limit) - 1)
        return self

    def range(self, start, end):
        self.bounds = (int(start), int(end))
        return self

    def upsert(self, payload, *_args, **_kwargs):
        bucket = self.db.tables.setdefault(self.table, [])
        if self.table == DAILY_ROW_DETAIL_TABLE:
            self.db.row_detail_upserts += 1
        for row in payload:
            if self.table == DAILY_ROW_DETAIL_TABLE:
                bucket[:] = [
                    existing
                    for existing in bucket
                    if not (
                        existing.get("run_id") == row.get("run_id")
                        and existing.get("row_index") == row.get("row_index")
                    )
                ]
            bucket.append(dict(row))
        self.wrote = True
        return self

    def execute(self):
        if self.wrote:
            return _Result([])
        rows = [
            row
            for row in self.db.tables.get(self.table, [])
            if all(row.get(column) == value for column, value in self.filters.items())
        ]
        if self.bounds is not None:
            start, end = self.bounds
            rows = rows[start:end + 1]
        return _Result(rows)


class _DB:
    def __init__(self):
        self.tables = {DAILY_ACQUISITION_DETAIL_TABLE: [], DAILY_ROW_DETAIL_TABLE: []}
        self.row_detail_upserts = 0

    def table(self, name):
        return _Query(self, name)


def _pass_receipt(stage, **extra):
    receipt = {
        "stage": stage,
        "evidence_type": "EVIDENCE_EMITTED",
        "disposition": "PASS",
        "authority": f"nfl.authority.{stage}",
        "reason": None,
        "bound_research_run_id": RUN_ID,
        "bound_event_key": EVENT_KEY,
        "bound_official_event_id": EVENT_ID,
        "bound_score_snapshot_id": SCORE_ID,
        "can_execute": False,
    }
    receipt.update(extra)
    receipt["can_execute"] = False
    return receipt


def _complete_contract(**stage_overrides):
    stages = {
        STAGE_PACKAGE: _pass_receipt(STAGE_PACKAGE),
        STAGE_DYNAMIC_CALIBRATION: _pass_receipt(STAGE_DYNAMIC_CALIBRATION),
        STAGE_PROBABILITY_AUDIT: _pass_receipt(STAGE_PROBABILITY_AUDIT),
        STAGE_EVENT_GOVERNOR: _pass_receipt(STAGE_EVENT_GOVERNOR),
        STAGE_FINAL_REFRESH: _pass_receipt(
            STAGE_FINAL_REFRESH,
            freshness_status="FRESH",
            refreshed_at="2026-10-18T18:00:00+00:00",
            material_update_timestamp="2026-10-18T17:00:00+00:00",
        ),
    }
    stages.update(stage_overrides)
    return {
        "contract_version": CONTRACT_VERSION,
        "producer": "test.nfl.stage_proof",
        "sport": "NFL",
        "research_run_id": RUN_ID,
        "event_key": EVENT_KEY,
        "official_event_id": EVENT_ID,
        "score_snapshot_id": SCORE_ID,
        "stages": stages,
        "can_execute": False,
    }


def _ranked_row(**overrides):
    row = {
        "research_run_id": RUN_ID,
        "event_key": EVENT_KEY,
        "official_event_id": EVENT_ID,
        "score_snapshot_id": SCORE_ID,
        "calibrated_home_probability": PATRIOTS_PROBABILITY,
        "calibrated_home_lower_bound": PATRIOTS_LOWER_BOUND,
        "rank_eligible": True,
        "probability_publishable": True,
        "can_execute": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "stage_proof_contract": _complete_contract(),
    }
    row.update(overrides)
    return row


def _stored_nfl_detail(event_id, *, probability, lower_bound):
    """Shape persisted on 2026-10-18: guard PASS, stage receipts absent."""
    return {
        "code": "GOVERNED_PROBABILITY_PUBLISHED",
        "terminal_label": "FINAL_APPROVED",
        "probability_publishable": True,
        "rank_eligible": True,
        "can_execute": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "calibration_health_status": "PASS",
        "calibrated_home_probability": probability,
        "calibrated_home_lower_bound": lower_bound,
        "llp_probability_audit_result": "PASS_PROBABILITY_AUDIT",
        "event_mutex_status": "PASS",
        "model_invoked": True,
        "llp_governance": {
            "status": "PASS",
            "probability_publishable": True,
            "rank_eligible": True,
            "global_terminal_reducer": "V17_TERMINAL_REDUCER",
            "can_execute": False,
            "probability_audit_result": "PASS_PROBABILITY_AUDIT",
            "event_mutex_status": "PASS",
            "postmodel_gates_status": "PASS",
            "final_gates_status": "PASS",
            "terminal_label": "FINAL_APPROVED",
            "terminal": {"terminal_label": "FINAL_APPROVED", "status": "PASS"},
        },
        "official_publication_guard": {
            "status": "PASS",
            "official_publication_allowed": True,
            "rank_eligible": True,
            "probability_publishable": True,
            "can_execute": False,
            "blockers": [],
        },
    }


def _scan_row(sport, event_id, detail, *, ranked):
    return {
        "sport": sport,
        "league": sport,
        "event_key": f"{sport}:{event_id}",
        "official_event_id": event_id,
        "event_status": "PREGAME",
        "bucket": MODEL_COMPLETED if ranked else "MODEL_UNAVAILABLE",
        "model_status": "SPORTING_PROBABILITY_COMPLETED" if ranked else "MODEL_UNAVAILABLE",
        "rank_eligible": ranked,
        "probability_publishable": ranked,
        "detail": detail,
        "can_execute": False,
    }


def test_complete_bound_receipts_agree_on_publication():
    audit = publication_chain_audit(_ranked_row()).as_dict()
    assert audit["status"] == "PASS"
    assert audit["rank_eligible"] is True
    assert audit["first_blocker"] is None
    assert audit["stage_evidence"]["dynamic_calibration_complete"] == "EVIDENCE_EMITTED"
    assert audit["stage_evidence"]["final_refresh_passed"] == "EVIDENCE_EMITTED"
    assert audit["can_execute"] is False


def test_missing_final_refresh_is_not_emitted_rather_than_failed():
    contract = _complete_contract(**{
        STAGE_FINAL_REFRESH: {
            "stage": STAGE_FINAL_REFRESH,
            "evidence_type": EVIDENCE_NOT_EMITTED,
            "disposition": "UNPROVEN",
            "authority": None,
            "reason": "NFL_FINAL_REFRESH_OPERATION_NOT_EXECUTED",
            "bound_research_run_id": RUN_ID,
            "bound_event_key": EVENT_KEY,
            "bound_official_event_id": EVENT_ID,
            "bound_score_snapshot_id": SCORE_ID,
            "can_execute": False,
            "scheduled_event_status_accepted_as_proof": False,
        }
    })
    audit = publication_chain_audit(_ranked_row(stage_proof_contract=contract))
    assert audit.status == "UNPROVEN"
    assert audit.first_blocker == "NFL_FINAL_REFRESH_OPERATION_NOT_EXECUTED"
    assert audit.stage_evidence["final_refresh_passed"] == EVIDENCE_NOT_EMITTED
    assert audit.rank_eligible is False


def test_stale_refresh_receipt_fails_closed_without_using_event_status():
    contract = _complete_contract(**{
        STAGE_FINAL_REFRESH: _pass_receipt(
            STAGE_FINAL_REFRESH,
            freshness_status="STALE",
            refreshed_at="2026-10-18T16:00:00+00:00",
            material_update_timestamp="2026-10-18T18:30:00+00:00",
        )
    })
    audit = publication_chain_audit(_ranked_row(
        stage_proof_contract=contract,
        official_event_status="SCHEDULED",
    ))
    assert audit.status == "BLOCKED"
    assert audit.first_blocker == "FINAL_REFRESH_EVIDENCE_STALE"
    assert audit.stage_evidence["final_refresh_passed"] == "EVIDENCE_STALE"


def test_failed_dynamic_calibration_is_evidence_failed():
    contract = _complete_contract(**{
        STAGE_DYNAMIC_CALIBRATION: {
            "stage": STAGE_DYNAMIC_CALIBRATION,
            "evidence_type": EVIDENCE_FAILED,
            "disposition": "FAIL",
            "authority": "nfl.dynamic_calibration",
            "reason": "DYNAMIC_CALIBRATION_NOT_COMPLETE",
            "bound_research_run_id": RUN_ID,
            "bound_event_key": EVENT_KEY,
            "bound_official_event_id": EVENT_ID,
            "bound_score_snapshot_id": SCORE_ID,
            "can_execute": False,
        }
    })
    audit = publication_chain_audit(_ranked_row(stage_proof_contract=contract))
    assert audit.status == "BLOCKED"
    assert audit.first_blocker == "DYNAMIC_CALIBRATION_NOT_COMPLETE"
    assert audit.stage_evidence["dynamic_calibration_complete"] == EVIDENCE_FAILED


def test_malformed_package_receipt_is_not_a_missing_receipt():
    contract = _complete_contract(**{
        STAGE_PACKAGE: {
            "stage": STAGE_PACKAGE,
            "evidence_type": "EVIDENCE_MALFORMED",
            "disposition": "FAIL",
            "authority": "v17.team_event_request_runtime._build_governed_probability_package",
            "reason": "PROBABILITY_PACKAGE_MALFORMED",
            "bound_research_run_id": RUN_ID,
            "bound_event_key": EVENT_KEY,
            "bound_official_event_id": EVENT_ID,
            "bound_score_snapshot_id": SCORE_ID,
            "can_execute": False,
        }
    })
    audit = publication_chain_audit(_ranked_row(stage_proof_contract=contract))
    assert audit.status == "BLOCKED"
    assert audit.first_blocker == "PROBABILITY_PACKAGE_MALFORMED"
    assert audit.stage_evidence["probability_package_valid"] == "EVIDENCE_MALFORMED"


def test_absent_package_key_is_not_labeled_as_failed_validation():
    absent = publication_chain_audit({
        "calibrated_probability": 0.61,
        "calibrated_lower_bound": 0.55,
        "dynamic_calibration_complete": True,
        "probability_audit_passed": True,
        "event_governor_complete": True,
        "final_refresh_passed": True,
        "rank_eligible": True,
        "probability_publishable": True,
        "can_execute": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
    })
    failed = publication_chain_audit({
        "calibrated_probability": 0.61,
        "calibrated_lower_bound": 0.55,
        "probability_package_valid": False,
        "dynamic_calibration_complete": True,
        "probability_audit_passed": True,
        "event_governor_complete": True,
        "final_refresh_passed": True,
        "rank_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
    })
    assert absent.status == "UNPROVEN"
    assert absent.first_blocker == "PROBABILITY_PACKAGE_EVIDENCE_NOT_EMITTED"
    assert absent.stage_evidence["probability_package_valid"] == EVIDENCE_NOT_EMITTED
    assert failed.status == "BLOCKED"
    assert failed.first_blocker == "PROBABILITY_PACKAGE_NOT_VALID"
    assert failed.stage_evidence["probability_package_valid"] == EVIDENCE_FAILED


def test_calibration_health_and_pregame_do_not_prove_later_stages():
    audit = publication_chain_audit({
        "probability_package_valid": True,
        "calibration_health_status": "PASS",
        "official_event_status": "PREGAME",
        "calibrated_home_probability": PATRIOTS_PROBABILITY,
        "calibrated_home_lower_bound": PATRIOTS_LOWER_BOUND,
        "rank_eligible": True,
        "probability_publishable": True,
        "can_execute": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
    })
    assert audit.status == "UNPROVEN"
    assert audit.first_blocker == "DYNAMIC_CALIBRATION_EVIDENCE_NOT_EMITTED"
    assert audit.stage_evidence["dynamic_calibration_complete"] == EVIDENCE_NOT_EMITTED


def test_scorer_and_guard_mismatch_stays_conflict_and_unranked():
    detail = _stored_nfl_detail("evt", probability=PATRIOTS_PROBABILITY, lower_bound=PATRIOTS_LOWER_BOUND)
    detail["official_publication_guard"] = {
        "status": "HELD",
        "official_publication_allowed": False,
        "rank_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
        "blockers": ["TEAM_EVENT_RANK_ELIGIBILITY_NOT_PROVEN"],
    }
    detail["stage_proof_contract"] = _complete_contract()
    row = _scan_row("NFL", EVENT_ID, detail, ranked=True)
    audited = apply_scan_row_stage_proof_ceiling(
        row,
        lambda candidate: publication_chain_audit({**candidate.get("detail", {}), **candidate}).as_dict(),
    )
    assert audited["publication_agreement"]["status"] == "CONFLICT"
    assert audited["publication_agreement"]["reason"] == "SCORER_GUARD_PUBLICATION_MISMATCH"
    assert audited["rank_eligible"] is False
    assert audited["probability_publishable"] is False
    assert audited["detail"]["calibrated_home_probability"] == PATRIOTS_PROBABILITY
    assert audited["detail"]["calibrated_home_lower_bound"] == PATRIOTS_LOWER_BOUND
    assert audited["detail"]["official_publication_guard"]["official_publication_allowed"] is False
    assert audited["can_execute"] is False


def test_stored_nested_nfl_guard_pass_is_unproven_without_changing_probability():
    games = [
        ("nfl-17", "New England Patriots", "Buffalo Bills", PATRIOTS_PROBABILITY, PATRIOTS_LOWER_BOUND),
        ("nfl-18", "Kansas City Chiefs", "Denver Broncos", 0.64, 0.58),
        ("nfl-19", "Dallas Cowboys", "Philadelphia Eagles", 0.57, 0.51),
        ("nfl-20", "San Francisco 49ers", "Seattle Seahawks", 0.71, 0.66),
        ("nfl-21", "Baltimore Ravens", "Cincinnati Bengals", 0.69, 0.63),
        ("nfl-22", "Green Bay Packers", "Chicago Bears", 0.60, 0.55),
        ("nfl-23", "Miami Dolphins", "New York Jets", 0.66, 0.61),
        ("nfl-24", "Detroit Lions", "Minnesota Vikings", 0.73, 0.68),
    ]
    rows = [
        _scan_row(
            "NFL",
            event_id,
            _stored_nfl_detail(event_id, probability=probability, lower_bound=lower),
            ranked=True,
        )
        for event_id, _home, _away, probability, lower in games
    ]
    rows.append(_scan_row("MLB", "mlb-1", {
        "calibrated_probability": 0.70,
        "calibrated_lower_bound": 0.64,
        "probability_package_valid": True,
        "dynamic_calibration_complete": True,
        "probability_audit_passed": True,
        "event_governor_complete": True,
        "final_refresh_passed": True,
        "terminal_label": "FINAL_APPROVED",
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
        "model_invoked": True,
    }, ranked=True))
    rows.append(_scan_row("NHL", "nhl-1", {
        "reason": "NHL_TEAM_EVENT_FITTED_MODEL_OR_ADAPTER_UNAVAILABLE",
        "model_invoked": False,
        "can_execute": False,
    }, ranked=False))

    result = enrich_full_board_result({
        "discovery": {"sports_queried": ["NFL", "MLB", "NHL"]},
        "rows": rows,
        "reconciliation": {"acquisition_audit": []},
        "can_execute": False,
    })
    by_id = {row["official_event_id"]: row for row in result["rows"]}
    patriots = by_id["nfl-17"]
    assert patriots["bucket"] == OTHER_GOVERNED_HOLD
    assert patriots["rank_eligible"] is False
    assert patriots["probability_publishable"] is False
    assert patriots["detail"]["calibrated_home_probability"] == PATRIOTS_PROBABILITY
    assert patriots["detail"]["calibrated_home_lower_bound"] == PATRIOTS_LOWER_BOUND
    assert patriots["detail"]["llp_governance"]["terminal"]["terminal_label"] == "FINAL_APPROVED"
    assert patriots["detail"]["official_publication_guard"]["status"] == "HELD"
    assert patriots["detail"]["official_publication_guard"]["upstream_official_publication_guard_status"] == "PASS"
    assert patriots["publication_chain_audit"]["status"] == "UNPROVEN"
    assert patriots["publication_chain_audit"]["first_blocker"] == "PROBABILITY_PACKAGE_EVIDENCE_NOT_EMITTED"
    assert patriots["publication_chain_audit"]["stage_evidence"]["probability_package_valid"] == EVIDENCE_NOT_EMITTED
    assert patriots["publication_chain_audit"]["stage_evidence"]["dynamic_calibration_complete"] == EVIDENCE_NOT_EMITTED
    assert patriots["publication_chain_audit"]["stage_evidence"]["final_refresh_passed"] == EVIDENCE_NOT_EMITTED
    assert patriots["publication_agreement"]["upstream_official_publication_guard_status"] == "PASS"
    assert patriots["can_execute"] is False
    assert all(by_id[event_id]["rank_eligible"] is False for event_id, *_rest in games)
    assert len({by_id[event_id]["detail"]["calibrated_home_probability"] for event_id, *_rest in games}) == 8
    assert by_id["mlb-1"]["rank_eligible"] is True
    assert by_id["mlb-1"]["publication_chain_audit"]["status"] == "PASS"
    assert by_id["nhl-1"]["bucket"] == "MODEL_UNAVAILABLE"
    assert by_id["nhl-1"]["publication_chain_audit"] is None
    assert result["can_execute"] is False


def _governance_pass():
    return {
        "status": "PASS",
        "probability_audit_result": "PASS_PROBABILITY_AUDIT",
        "event_mutex_status": "PASS",
        "postmodel_gates_status": "PASS",
        "final_gates_status": "PASS",
        "global_terminal_reducer": "V17_TERMINAL_REDUCER",
        "probability_publishable": True,
        "rank_eligible": True,
        "can_execute": False,
        "terminal_label": "FINAL_APPROVED",
        "terminal": {"terminal_label": "FINAL_APPROVED", "status": "PASS"},
        "blockers": [],
    }


def _govern_inputs(db):
    base = SimpleNamespace(
        LLP_TEAM_BETTING_ENGINE="LLP_TEAM_BETTING_ENGINE",
        _build_governed_probability_package=lambda envelope, model_result: object(),
    )
    req = SimpleNamespace(
        research_run_id=RUN_ID,
        event_key=EVENT_KEY,
        source_snapshot_id="nflverse-snap",
        requested_timezone="America/Chicago",
        candidate_family="OUTRIGHT_WINNER",
        decision_intent="BEST_SIDE",
    )
    route = SimpleNamespace(
        requester_host_identity="WOW_BETTING_ENGINE",
        candidate_family="OUTRIGHT_WINNER",
    )
    model_result = {
        "score_snapshot_id": SCORE_ID,
        "model_artifact_id": "artifact-1",
        "model_version": "nfl-v1",
        "calibrated_home_probability": PATRIOTS_PROBABILITY,
        "calibrated_home_lower_bound": PATRIOTS_LOWER_BOUND,
        "calibration_health_status": "PASS",
        "can_execute": False,
    }
    envelope = SimpleNamespace(official_event_id=EVENT_ID)
    return base, req, route, model_result, envelope


def test_nfl_producer_holds_when_dynamic_calibration_and_refresh_were_not_run():
    class _DBBridge:
        def rpc(self, _name, _params):
            payload = _governance_pass()

            class _Call:
                data = payload

                def execute(self):
                    return self

            return _Call()

    base, req, route, model_result, envelope = _govern_inputs(_DBBridge())
    result = _govern(base, req, route, model_result, envelope, db=_DBBridge())
    dynamic = result["stage_proof_contract"]["stages"][STAGE_DYNAMIC_CALIBRATION]
    refresh = result["stage_proof_contract"]["stages"][STAGE_FINAL_REFRESH]
    package = result["stage_proof_contract"]["stages"][STAGE_PACKAGE]
    assert result["stage_proof_contract"]["contract_version"] == CONTRACT_VERSION
    assert package["evidence_type"] == "EVIDENCE_EMITTED"
    assert package["authority"] == "v17.team_event_request_runtime._build_governed_probability_package"
    assert dynamic["evidence_type"] == EVIDENCE_NOT_EMITTED
    assert dynamic["reason"] == "NFL_DYNAMIC_CALIBRATION_STAGE_NOT_EXECUTED"
    assert dynamic["calibration_health_accepted_as_proof"] is False
    assert dynamic["observed_calibration_health_status"] == "PASS"
    assert refresh["evidence_type"] == EVIDENCE_NOT_EMITTED
    assert refresh["borrowed_mlb_or_flask_receipt"] is False
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["code"] == "STAGE_PROOF_UNPROVEN"
    assert result["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert result["upstream_terminal_label"] == "FINAL_APPROVED"
    assert result["llp_governance"]["terminal"]["terminal_label"] == "FINAL_APPROVED"
    assert result["calibrated_home_probability"] == PATRIOTS_PROBABILITY
    assert result["calibrated_home_lower_bound"] == PATRIOTS_LOWER_BOUND
    assert result["official_publication_guard"]["status"] == "HELD"
    assert result["official_publication_guard"]["blockers"][0] == "NFL_DYNAMIC_CALIBRATION_STAGE_NOT_EXECUTED"
    assert result["publication_agreement"]["status"] == "UNPROVEN"
    assert result["can_execute"] is False


def test_nfl_producer_does_not_relabel_bridge_outage_as_model_unavailable():
    class _DBBridge:
        def rpc(self, _name, _params):
            raise RuntimeError("bridge down")

    base, req, route, model_result, envelope = _govern_inputs(_DBBridge())
    result = _govern(base, req, route, model_result, envelope, db=_DBBridge())
    assert result["code"] != "MODEL_UNAVAILABLE"
    assert result["stage_proof_contract"]["stages"][STAGE_PROBABILITY_AUDIT]["evidence_type"] == EVIDENCE_NOT_EMITTED
    assert result["stage_proof_contract"]["stages"][STAGE_PROBABILITY_AUDIT]["reason"] == "NFL_GOVERNANCE_BRIDGE_DID_NOT_EXECUTE"
    assert result["rank_eligible"] is False
    assert result["calibrated_home_probability"] == PATRIOTS_PROBABILITY
    assert result["can_execute"] is False


def _future_slate():
    commence = (datetime.now(timezone.utc) + timedelta(days=3)).replace(minute=0, second=0, microsecond=0)
    slate = commence.astimezone(ZoneInfo("America/Chicago")).date().isoformat()
    return slate, commence.isoformat().replace("+00:00", "Z")


def test_full_and_compact_action_readback_persists_eight_nfl_rows_once():
    slate, commence = _future_slate()
    games = [
        ("nfl-17", "New England Patriots", "Buffalo Bills", PATRIOTS_PROBABILITY, PATRIOTS_LOWER_BOUND),
        ("nfl-18", "Kansas City Chiefs", "Denver Broncos", 0.64, 0.58),
        ("nfl-19", "Dallas Cowboys", "Philadelphia Eagles", 0.57, 0.51),
        ("nfl-20", "San Francisco 49ers", "Seattle Seahawks", 0.71, 0.66),
        ("nfl-21", "Baltimore Ravens", "Cincinnati Bengals", 0.69, 0.63),
        ("nfl-22", "Green Bay Packers", "Chicago Bears", 0.60, 0.55),
        ("nfl-23", "Miami Dolphins", "New York Jets", 0.66, 0.61),
        ("nfl-24", "Detroit Lions", "Minnesota Vikings", 0.73, 0.68),
    ]
    payloads = {
        event_id: _stored_nfl_detail(event_id, probability=probability, lower_bound=lower)
        for event_id, _home, _away, probability, lower in games
    }
    payloads["mlb-1"] = {
        "code": "GOVERNED_PROBABILITY_PUBLISHED",
        "terminal_label": "FINAL_APPROVED",
        "probability_publishable": True,
        "rank_eligible": True,
        "can_execute": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "calibrated_probability": 0.67,
        "calibrated_lower_bound": 0.61,
        "probability_package_valid": True,
        "dynamic_calibration_complete": True,
        "probability_audit_passed": True,
        "event_governor_complete": True,
        "final_refresh_passed": True,
        "model_invoked": True,
    }

    def fetch(family, target=None):
        sport_id = getattr(target, "sport_id", None)
        if family == "NFL" and sport_id == 2:
            return [
                {
                    "id": event_id,
                    "home_team": home,
                    "away_team": away,
                    "commence_time": commence,
                    "status": "SCHEDULED",
                }
                for event_id, home, away, _probability, _lower in games
            ]
        if family == "MLB" and sport_id == 3:
            return [{
                "id": "mlb-1",
                "home_team": "Chicago Cubs",
                "away_team": "St. Louis Cardinals",
                "commence_time": commence,
                "status": "SCHEDULED",
            }]
        if family == "NHL" and sport_id == 6:
            return [{
                "id": "nhl-1",
                "home_team": "Chicago Blackhawks",
                "away_team": "Detroit Red Wings",
                "commence_time": commence,
                "status": "SCHEDULED",
            }]
        return []

    def score_team_event_request(request, **_kwargs):
        return dict(payloads[request.official_event_id])

    original_scan = discovery.run_cross_sport_winner_scan
    already_installed = getattr(discovery, "_v17_full_board_overlay_installed", False)
    original_bridges = dict(bridge_runtime.TEAM_EVENT_BRIDGES)
    original_feed = runtime.discovery_feed.union_feed
    original_score = runtime.score_team_event_request
    original_resolve = nhl_event_identity.resolve_nhl_current_event_identity
    original_cross = runtime._cross_sport_moneyline_rows
    handoff_before = getattr(
        runtime, "_v17_cross_sport_discovery_evidence_handoff_installed", False
    )
    install_cross_sport_full_board_overlay()
    # Shard 3 imports the production entrypoint, which installs this handoff
    # before the test. An unresolved NHL alias then loses official_event_id and
    # cannot be read back as nhl-1. Force that same path in isolation, and stub
    # only the schedule match. The stub carries no probability and no NHL bridge.
    if not handoff_before:
        install_cross_sport_discovery_evidence_handoff()
    bridge_runtime.TEAM_EVENT_BRIDGES.clear()
    for sport in ("NFL", "MLB"):
        bridge_runtime.TEAM_EVENT_BRIDGES[sport] = TeamEventBridgeRegistration(
            sport=sport,
            adapter_name=f"{sport.lower()}_test_adapter",
            controlling_specialist=f"{sport}_TEAM_EVENT_SPECIALIST",
            required_inputs=(),
            scorer=lambda *_args, **_kwargs: {},
            supported_regimes=("REGULAR_SEASON",),
        )

    def resolve_nhl_schedule_stub(**_kwargs):
        return {
            "event_id": "nhl-1",
            "identity_provider": "NHL_TEST_SCHEDULE_STUB",
            "identity_verified_at": commence,
            "market_features_used": False,
            "can_execute": False,
        }

    nhl_event_identity.resolve_nhl_current_event_identity = resolve_nhl_schedule_stub
    runtime.discovery_feed.union_feed = lambda *_args, **_kwargs: fetch
    runtime.score_team_event_request = score_team_event_request
    db = _DB()
    try:
        request = DailySnapshotRequest(
            requested_slate_date=slate,
            requested_timezone="America/Chicago",
            lanes=["MONEYLINE"],
            response_mode="FULL",
        )
        full = run_daily_snapshot(request, db=db, market_api=object(), event_api=object())
        compact = run_daily_snapshot(
            request.model_copy(update={"response_mode": "COMPACT"}),
            db=db,
            market_api=object(),
            event_api=object(),
        )
    finally:
        runtime.discovery_feed.union_feed = original_feed
        runtime.score_team_event_request = original_score
        nhl_event_identity.resolve_nhl_current_event_identity = original_resolve
        if not handoff_before:
            runtime._cross_sport_moneyline_rows = original_cross
            runtime._v17_cross_sport_discovery_evidence_handoff_installed = False
        bridge_runtime.TEAM_EVENT_BRIDGES.clear()
        bridge_runtime.TEAM_EVENT_BRIDGES.update(original_bridges)
        if not already_installed:
            discovery.run_cross_sport_winner_scan = getattr(
                discovery, "_v17_full_board_original_scan", original_scan
            )
            discovery._v17_full_board_overlay_installed = False

    assert db.row_detail_upserts == 2
    for response in (full, compact):
        assert response["can_execute"] is False
        assert response["reconciliation"]["balanced"] is True
        assert response["reconciliation"]["rows_in"] == (
            response["reconciliation"]["rows_completed"]
            + response["reconciliation"]["rows_held"]
            + response["reconciliation"]["rows_rejected"]
            + response["reconciliation"]["rows_purged"]
        )
        moneyline = [row for row in response["rows"] if row["lane"] == "MONEYLINE"]
        assert response["reconciliation"]["rows_in"] == len(moneyline)
        readback = read_row_detail_page(db, run_id=response["run_id"], limit=50)
        assert readback["status"] != "DETAIL_UNAVAILABLE"
        assert len(readback["rows"]) == len(moneyline)
        assert len({row["row_index"] for row in readback["rows"]}) == len(moneyline)
        assert all(row["can_execute"] is False for row in readback["rows"])

    full_by_event = {row["identity"]["official_event_id"]: row for row in full["rows"]}
    patriots = full_by_event["nfl-17"]
    assert patriots["row_status"] == "HELD"
    assert patriots["probability_publishable"] is False
    assert patriots["can_execute"] is False
    assert patriots["result"]["detail"]["calibrated_home_probability"] == PATRIOTS_PROBABILITY
    assert patriots["result"]["detail"]["calibrated_home_lower_bound"] == PATRIOTS_LOWER_BOUND
    assert patriots["result"]["detail"]["official_publication_guard"]["status"] == "HELD"
    assert patriots["result"]["publication_chain_audit"]["status"] == "UNPROVEN"
    assert patriots["result"]["publication_chain_audit"]["first_blocker"] == "PROBABILITY_PACKAGE_EVIDENCE_NOT_EMITTED"
    stage_evidence = patriots["result"]["publication_chain_audit"]["stage_evidence"]
    assert stage_evidence["dynamic_calibration_complete"] == "EVIDENCE_NOT_EMITTED"
    assert stage_evidence["final_refresh_passed"] == "EVIDENCE_NOT_EMITTED"
    # The producer claims model publication while the official guard holds it:
    # this is an authentic scorer/guard CONTRADICTION, not mere missing proof.
    assert patriots["result"]["publication_agreement"]["status"] == "CONFLICT"
    # This intentionally sparse historical fixture also lacks the canonical
    # immutable scoring-package fields. The strict package validator reports
    # MODEL_OUTPUT_INVALID first; do not disguise that scorer contract failure
    # as a stage-proof-only hold or fabricate the eight missing fields.
    assert patriots["result"]["detail"].get("code") == "MODEL_OUTPUT_INVALID"
    assert patriots["result"]["bucket"] != "MODEL_UNAVAILABLE"
    # This intentionally sparse MLB fixture has legacy stage booleans, but
    # lacks the strict immutable prediction/source/bounds package required by
    # the shared full-board scorer. Do not publish it merely because the
    # individual stage booleans look complete.
    assert full_by_event["mlb-1"]["row_status"] == "HELD"
    assert full_by_event["mlb-1"]["probability_publishable"] is False
    assert full_by_event["mlb-1"]["result"]["publication_chain_audit"]["status"] == "PASS"
    nhl = full_by_event["nhl-1"]
    nhl_detail = nhl["result"]["detail"]
    assert nhl["row_status"] == "HELD"
    assert nhl["result"]["bucket"] == "MODEL_UNAVAILABLE"
    assert nhl["probability_publishable"] is False
    assert nhl["can_execute"] is False
    assert nhl_detail.get("model_invoked") is False
    assert "calibrated_probability" not in nhl_detail
    assert "calibrated_home_probability" not in nhl_detail
    assert sum(1 for event_id in full_by_event if str(event_id).startswith("nfl-")) == 8
    discovery_audit = full["cross_sport_discovery_audit"]["reconciliation"]
    assert discovery_audit["events_discovered"] == discovery_audit["events_accounted"]
    assert discovery_audit["events_discovered"] == len(full_by_event)

    compact_by_event = {row["identity"]["official_event_id"]: row for row in compact["rows"]}
    compact_patriots = compact_by_event["nfl-17"]
    assert compact["response_mode"] == "COMPACT"
    assert compact_patriots["row_status"] == "HELD"
    assert compact_patriots["probability_publishable"] is False
    assert compact_patriots["can_execute"] is False
    assert compact_patriots["result_summary"]["calibrated_home_probability"] == PATRIOTS_PROBABILITY
    assert compact_patriots["result_summary"]["calibrated_home_lower_bound"] == PATRIOTS_LOWER_BOUND
    assert compact_patriots["result_summary"]["official_publication_guard"]["status"] == "HELD"
    assert compact_patriots["result_summary"]["publication_chain_audit"]["status"] == "UNPROVEN"
    # COMPACT cannot erase the same upstream official-guard contradiction.
    assert compact_patriots["result_summary"]["publication_agreement"]["status"] == "CONFLICT"
    assert compact_by_event["mlb-1"]["row_status"] == "COMPLETED"
    compact_nhl = compact_by_event["nhl-1"]
    assert compact_nhl["row_status"] == "HELD"
    assert compact_nhl["probability_publishable"] is False
    assert compact_nhl["can_execute"] is False
    assert "calibrated_home_probability" not in (compact_nhl.get("result_summary") or {})

    stored = read_row_detail_page(db, run_id=full["run_id"], limit=50)
    stored_patriots = next(
        row for row in stored["rows"] if row["identity"]["official_event_id"] == "nfl-17"
    )
    assert stored_patriots["detail"]["result"]["detail"]["official_publication_guard"]["status"] == "HELD"
    assert stored_patriots["detail"]["result"]["publication_chain_audit"]["status"] == "UNPROVEN"
    assert stored_patriots["detail"]["result"]["detail"]["calibrated_home_probability"] == PATRIOTS_PROBABILITY
    assert stored_patriots["detail"]["can_execute"] is False
    stored_nhl = [
        row for row in stored["rows"] if row["identity"]["official_event_id"] == "nhl-1"
    ]
    assert len(stored_nhl) == 1
    assert stored_nhl[0]["detail"]["result"]["bucket"] == "MODEL_UNAVAILABLE"
    assert stored_nhl[0]["detail"]["can_execute"] is False


def test_unresolved_nhl_alias_is_retained_without_a_probability():
    """An official-schedule miss must not delete the discovered NHL row.

    The production handoff clears official_event_id when the NHL schedule does
    not match. The row stays on the board as an identity hold, with no model
    probability substituted for the missing canonical id.
    """
    slate, commence = _future_slate()

    def fetch(family, target=None):
        sport_id = getattr(target, "sport_id", None)
        if family == "NHL" and sport_id == 6:
            return [{
                "id": "nhl-unresolved",
                "home_team": "Chicago Blackhawks",
                "away_team": "Detroit Red Wings",
                "commence_time": commence,
                "status": "SCHEDULED",
            }]
        return []

    def fail_nhl_schedule(**_kwargs):
        raise NHLEventIdentityError("NHL_CANONICAL_EVENT_NOT_FOUND")

    original_scan = discovery.run_cross_sport_winner_scan
    already_installed = getattr(discovery, "_v17_full_board_overlay_installed", False)
    original_bridges = dict(bridge_runtime.TEAM_EVENT_BRIDGES)
    original_feed = runtime.discovery_feed.union_feed
    original_resolve = nhl_event_identity.resolve_nhl_current_event_identity
    original_cross = runtime._cross_sport_moneyline_rows
    handoff_before = getattr(
        runtime, "_v17_cross_sport_discovery_evidence_handoff_installed", False
    )
    install_cross_sport_full_board_overlay()
    if not handoff_before:
        install_cross_sport_discovery_evidence_handoff()
    bridge_runtime.TEAM_EVENT_BRIDGES.clear()
    nhl_event_identity.resolve_nhl_current_event_identity = fail_nhl_schedule
    runtime.discovery_feed.union_feed = lambda *_args, **_kwargs: fetch
    db = _DB()
    try:
        request = DailySnapshotRequest(
            requested_slate_date=slate,
            requested_timezone="America/Chicago",
            lanes=["MONEYLINE"],
            response_mode="FULL",
        )
        full = run_daily_snapshot(request, db=db, market_api=object(), event_api=object())
        compact = run_daily_snapshot(
            request.model_copy(update={"response_mode": "COMPACT"}),
            db=db,
            market_api=object(),
            event_api=object(),
        )
    finally:
        runtime.discovery_feed.union_feed = original_feed
        nhl_event_identity.resolve_nhl_current_event_identity = original_resolve
        if not handoff_before:
            runtime._cross_sport_moneyline_rows = original_cross
            runtime._v17_cross_sport_discovery_evidence_handoff_installed = False
        bridge_runtime.TEAM_EVENT_BRIDGES.clear()
        bridge_runtime.TEAM_EVENT_BRIDGES.update(original_bridges)
        if not already_installed:
            discovery.run_cross_sport_winner_scan = getattr(
                discovery, "_v17_full_board_original_scan", original_scan
            )
            discovery._v17_full_board_overlay_installed = False

    for response in (full, compact):
        assert response["can_execute"] is False
        assert response["reconciliation"]["balanced"] is True
        moneyline = [row for row in response["rows"] if row["lane"] == "MONEYLINE"]
        assert len(moneyline) == 1
        assert response["reconciliation"]["rows_in"] == 1
        audit = response["cross_sport_discovery_audit"]["reconciliation"]
        assert audit["events_discovered"] == audit["events_accounted"] == 1
        nhl_rows = [
            row for row in moneyline
            if (row.get("identity") or {}).get("home_team") == "Chicago Blackhawks"
        ]
        assert len(nhl_rows) == 1
        assert nhl_rows[0]["identity"].get("official_event_id") in (None, "")
        assert nhl_rows[0]["row_status"] == "HELD"
        assert nhl_rows[0]["probability_publishable"] is False
        assert nhl_rows[0]["can_execute"] is False

    assert full["rows"][0]["result"]["bucket"] == "EVENT_IDENTITY_UNRESOLVED"
    detail = full["rows"][0]["result"]["detail"]
    assert detail.get("model_invoked") is False
    assert "calibrated_probability" not in detail
    assert "calibrated_home_probability" not in detail
    stored = read_row_detail_page(db, run_id=full["run_id"], limit=50)
    assert len(stored["rows"]) == 1
    assert stored["rows"][0]["detail"]["can_execute"] is False
