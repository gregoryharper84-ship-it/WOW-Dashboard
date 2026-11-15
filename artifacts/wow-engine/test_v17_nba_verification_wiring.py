"""Contract tests for wiring independent verification into basketball maintenance.

Covers the deferral boundary (two named governance blockers, nothing else), the
append-only archive row, typed fail-closed outcomes, and the migration that
creates the receipt ledger.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import random
from datetime import date, timedelta

from basketball_specialist_pipeline import fit_and_persist
from basketball_team_event_specialist import calibration_fingerprint
from v17 import nba_verification_wiring as wiring
from v17.nba_team_event_verification import (
    MappingVerificationSource,
    VerificationContextError,
)

MIGRATION = (
    Path(__file__).resolve().parent
    / "migrations"
    / "20261110_v17_independent_verification_receipts.sql"
)
MODEL_VERSION = "NBA_TEAM_EVENT_LOGISTIC_V1_20261110T000000Z"

PENDING_V10 = {
    "V10": (
        {"capability_status": "SHADOW"},
        ["CAPABILITY_STATUS_NOT_CERTIFIED:SHADOW"],
    )
}
MISSING_V11 = {
    "V11": (
        {},
        [
            "SOURCE_EVENT_REPLAY_NOT_ATTESTED:None",
            "SOURCE_TIMESTAMP_NOT_ATTESTED:None",
            "SOURCE_DATASET_ID_MISSING",
        ],
    )
}


def _checks(failures=None):
    failures = failures or {}
    out = []
    for index in range(1, 13):
        check_id = f"V{index}"
        if check_id in failures:
            observed, blockers = failures[check_id]
            out.append(
                {
                    "check_id": check_id,
                    "status": "FAIL",
                    "observed": observed,
                    "blockers": list(blockers),
                }
            )
        else:
            out.append(
                {"check_id": check_id, "status": "PASS", "observed": {}, "blockers": []}
            )
    return out


def _receipt(status="PASS", blockers=(), failures=None):
    return {
        "schema_version": "WOW_V17_TEAM_EVENT_INDEPENDENT_VERIFICATION_V1",
        "verification_id": "11111111-2222-4333-8444-555555555555",
        "sport": "NBA",
        "league_scope": "NBA",
        "controlling_specialist": "wow.nba-game-win-probability-expert",
        "model_family": "BASKETBALL_TEAM_EVENT_LOGISTIC_V1",
        "model_version": MODEL_VERSION,
        "model_artifact_id": "artifact-1",
        "model_artifact_sha256": "a" * 64,
        "calibration_artifact_id": "cal-1",
        "calibration_sha256": "b" * 64,
        "feature_schema_version": "BASKETBALL_TEAM_EVENT_FEATURES_V2",
        "input_contract_version": "NBA_GAME_WIN_INPUTS_V1",
        "certification_id_attested": None,
        "independent_verifier": "INDEPENDENT_VERIFICATION",
        "implementation_owner": wiring.IMPLEMENTATION_OWNER,
        "verification_owner": wiring.VERIFICATION_OWNER,
        "independent_verification_status": status,
        "method": {
            "harness": "wow-v17-nba-team-event-verification",
            "code_sha": "c" * 40,
            "environment": "pytest",
        },
        "checks": _checks(failures),
        "source_attestation": {},
        "evidence_refs": {},
        "limitations": [],
        "verdict": {
            "publishable_scope": "NONE",
            "certification_eligible": status == "PASS",
            "blockers": list(blockers),
        },
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "created_at_utc": "2026-11-10T00:00:00+00:00",
        "metrics": {"holdout_brier": 0.2},
    }


class _ArchiveClient:
    def __init__(self, error=None):
        self.rows = []
        self.error = error

    def table(self, name):
        assert name == wiring.RECEIPT_TABLE
        return self

    def insert(self, row):
        if self.error is not None:
            raise self.error
        self.rows.append(row)
        return self

    def execute(self):
        return SimpleNamespace(data=[{}])


# --------------------------------------------------------------------------- #
# Deferral boundary
# --------------------------------------------------------------------------- #
def test_pass_receipt_opens_the_gate():
    result = wiring.classify_verdict(_receipt())

    assert result["gate_open"] is True
    assert result["hard_blockers"] == []
    assert result["deferred_blockers"] == []


def test_fail_receipt_closes_the_gate_without_an_explicit_deferral():
    receipt = _receipt(
        "FAIL",
        ["CHECK_FAILED:V10", "CHECK_FAILED:V11"],
        {**PENDING_V10, **MISSING_V11},
    )

    result = wiring.classify_verdict(receipt)

    assert result["gate_open"] is False
    assert result["deferred_blockers"] == []
    assert result["hard_blockers"] == ["CHECK_FAILED:V10", "CHECK_FAILED:V11"]


def test_pending_capability_is_deferred_only_for_the_named_blocker():
    receipt = _receipt("FAIL", ["CHECK_FAILED:V10"], PENDING_V10)

    deferred = wiring.classify_verdict(
        receipt, defer=wiring.DEFERRED_GOVERNANCE_BLOCKERS
    )

    assert deferred["gate_open"] is True
    assert deferred["deferred_blockers"] == ["CHECK_FAILED:V10"]
    assert deferred["hard_blockers"] == []


def test_deferral_cannot_hide_v10_version_drift():
    receipt = _receipt(
        "FAIL",
        ["CHECK_FAILED:V10"],
        {
            "V10": (
                {"capability_status": "SHADOW"},
                [
                    "CAPABILITY_STATUS_NOT_CERTIFIED:SHADOW",
                    "MODEL_VERSION_MISMATCH:capability:OLD_VERSION",
                ],
            )
        },
    )

    result = wiring.classify_verdict(receipt, defer=wiring.DEFERRED_GOVERNANCE_BLOCKERS)

    assert result["gate_open"] is False
    assert result["hard_blockers"] == ["CHECK_FAILED:V10"]


def test_deferral_cannot_hide_a_certified_capability_mismatch():
    receipt = _receipt(
        "FAIL",
        ["CHECK_FAILED:V10"],
        {"V10": ({"capability_status": "CERTIFIED"}, ["MODEL_VERSION_MISMATCH:capability:X"])},
    )

    result = wiring.classify_verdict(receipt, defer=wiring.DEFERRED_GOVERNANCE_BLOCKERS)

    assert result["gate_open"] is False


def test_deferral_never_hides_an_evidence_failure():
    receipt = _receipt(
        "FAIL",
        ["CHECK_FAILED:V6", "CHECK_FAILED:V10", "CHECK_FAILED:V11"],
        {
            **PENDING_V10,
            **MISSING_V11,
            "V6": ({}, ["HOLDOUT_BRIER_NOT_REPRODUCIBLE"]),
        },
    )

    result = wiring.classify_verdict(receipt, defer=wiring.DEFERRED_GOVERNANCE_BLOCKERS)

    assert result["gate_open"] is False
    assert result["hard_blockers"] == ["CHECK_FAILED:V6"]
    assert sorted(result["deferred_blockers"]) == ["CHECK_FAILED:V10", "CHECK_FAILED:V11"]


def test_substitution_failure_is_never_deferrable():
    receipt = _receipt(
        "FAIL",
        ["CHECK_FAILED:V12"],
        {"V12": ({}, ["MODEL_SUBSTITUTION_NOT_DISPROVEN:market_features_used"])},
    )

    assert (
        wiring.classify_verdict(receipt, defer=wiring.DEFERRED_GOVERNANCE_BLOCKERS)[
            "gate_open"
        ]
        is False
    )


def test_only_two_governance_blockers_are_deferrable():
    assert wiring.DEFERRED_GOVERNANCE_BLOCKERS == frozenset(
        {"CHECK_FAILED:V10", "CHECK_FAILED:V11"}
    )


# --------------------------------------------------------------------------- #
# Receipt digest and archive
# --------------------------------------------------------------------------- #
def test_receipt_digest_is_canonical_and_key_order_independent():
    receipt = _receipt()
    reordered = dict(reversed(list(receipt.items())))

    assert wiring.receipt_digest(receipt) == wiring.receipt_digest(reordered)
    assert len(wiring.receipt_digest(receipt)) == 64


def test_archive_appends_an_immutable_governance_neutral_row():
    client = _ArchiveClient()
    receipt = _receipt()

    result = wiring.archive_receipt(client, receipt)
    row = client.rows[0]

    assert row["can_execute"] is False
    assert row["publishable_scope"] == "NONE"
    assert row["independent_verification_status"] == "PASS"
    assert row["certification_eligible"] is True
    assert row["failed_checks"] == []
    assert row["blockers"] == []
    assert row["receipt_id"] == receipt["verification_id"]
    assert row["receipt"] == receipt
    assert row["receipt_digest"] == wiring.receipt_digest(receipt)
    assert row["implementation_owner"] != row["verification_owner"]
    assert result == {
        "archived": True,
        "duplicate": False,
        "receipt_digest": row["receipt_digest"],
    }


def test_archive_records_failed_checks_for_a_fail_receipt():
    client = _ArchiveClient()
    receipt = _receipt(
        "FAIL", ["CHECK_FAILED:V6"], {"V6": ({}, ["HOLDOUT_BRIER_NOT_REPRODUCIBLE"])}
    )

    wiring.archive_receipt(client, receipt)
    row = client.rows[0]

    assert row["independent_verification_status"] == "FAIL"
    assert row["certification_eligible"] is False
    assert row["failed_checks"] == ["V6"]
    assert row["blockers"] == ["CHECK_FAILED:V6"]
    assert row["can_execute"] is False


def test_archive_is_idempotent_for_a_duplicate_receipt():
    error = Exception("duplicate key value violates unique constraint")
    error.code = "23505"

    result = wiring.archive_receipt(_ArchiveClient(error=error), _receipt())

    assert result["duplicate"] is True
    assert result["archived"] is True


def test_archive_failure_is_typed_and_never_swallowed():
    with pytest.raises(wiring.VerificationArchiveError) as excinfo:
        wiring.archive_receipt(
            _ArchiveClient(error=RuntimeError("permission denied")), _receipt()
        )

    assert excinfo.value.code == "NBA_VERIFICATION_ARCHIVE_UNAVAILABLE"


def test_resolve_artifact_id_reads_by_version():
    class _Resolver:
        def __init__(self, rows):
            self.rows = rows
            self.filtered = None

        def table(self, name):
            assert name == wiring.ARTIFACT_TABLE
            return self

        def select(self, columns):
            return self

        def eq(self, key, value):
            self.filtered = (key, value)
            return self

        def order(self, *args, **kwargs):
            return self

        def limit(self, value):
            return self

        def execute(self):
            return SimpleNamespace(data=self.rows)

    resolver = _Resolver([{"artifact_id": "artifact-1"}])
    assert wiring.resolve_artifact_id(resolver, MODEL_VERSION) == "artifact-1"
    assert resolver.filtered == ("model_artifact_version", MODEL_VERSION)
    assert wiring.resolve_artifact_id(_Resolver([]), MODEL_VERSION) is None


def test_verify_and_archive_refuses_to_archive_without_a_receipt():
    client = _ArchiveClient()
    source = MappingVerificationSource(
        {
            "corpus": [
                {
                    "game_id": "nba-1",
                    "season": 2025,
                    "game_date": "2026-01-01",
                    "home_team_id": "BOS",
                    "away_team_id": "NYK",
                    "home_score": 101,
                    "away_score": 99,
                    "source_provider": "SPORTSDATAVERSE_ESPN_NBA",
                    "source_endpoint": "https://example.invalid/1",
                    "source_retrieved_at": "2026-01-01T23:00:00+00:00",
                    "source_payload_sha256": "d" * 64,
                    "settled": True,
                }
            ],
            "artifact": None,
        }
    )

    with pytest.raises(VerificationContextError) as excinfo:
        wiring.verify_and_archive(
            client,
            model_artifact_version=MODEL_VERSION,
            artifact_id="artifact-1",
            source=source,
        )

    assert excinfo.value.code == "ARTIFACT_ROW_NOT_FOUND"
    assert client.rows == []


# --------------------------------------------------------------------------- #
# Prerequisite preflight
# --------------------------------------------------------------------------- #
class _ProbeClient:
    """Client double that can fail a specific table probe."""

    def __init__(self, errors=None):
        self.errors = dict(errors or {})

    def table(self, name):
        return _ProbeQuery(self, name)


class _ProbeQuery:
    def __init__(self, client, name):
        self.client = client
        self.name = name

    def select(self, columns):
        return self

    def limit(self, count):
        return self

    def execute(self):
        error = self.client.errors.get(self.name)
        if error is not None:
            raise error
        return SimpleNamespace(data=[])


def _provider_error(code, message="provider error"):
    error = RuntimeError(message)
    error.code = code
    return error


def test_prerequisites_are_ready_when_both_probes_succeed():
    result = wiring.verification_prerequisites(_ProbeClient())

    assert result["ready"] is True
    assert result["code"] is None
    assert result["missing"] == []
    assert result["can_execute"] is False
    assert [probe["table"] for probe in result["probes"]] == [
        wiring.RECEIPT_TABLE,
        wiring.CALIBRATOR_TABLE,
    ]


def test_prerequisites_name_a_missing_ledger_table():
    result = wiring.verification_prerequisites(
        _ProbeClient(
            {
                wiring.RECEIPT_TABLE: _provider_error(
                    "PGRST205",
                    "Could not find the table 'public.wow_independent_verification_receipts' "
                    "in the schema cache",
                )
            }
        )
    )

    assert result["ready"] is False
    assert result["code"] == wiring.LEDGER_MISSING_CODE
    assert result["code"] == "NBA_VERIFICATION_LEDGER_MISSING"
    assert result["missing"] == [wiring.RECEIPT_TABLE]
    assert result["can_execute"] is False


def test_prerequisites_name_missing_calibrator_evidence_columns():
    result = wiring.verification_prerequisites(
        _ProbeClient(
            {
                wiring.CALIBRATOR_TABLE: _provider_error(
                    "42703", 'column "calibration_sha256" does not exist'
                )
            }
        )
    )

    assert result["ready"] is False
    assert result["code"] == wiring.CALIBRATOR_EVIDENCE_MISSING_CODE
    assert result["code"] == "NBA_VERIFICATION_CALIBRATOR_EVIDENCE_MISSING"
    assert result["missing"] == [f"{wiring.CALIBRATOR_TABLE}.columns"]


def test_prerequisites_prefer_the_ledger_code_when_both_are_missing():
    result = wiring.verification_prerequisites(
        _ProbeClient(
            {
                wiring.RECEIPT_TABLE: _provider_error(
                    "42P01",
                    'relation "wow_independent_verification_receipts" does not exist',
                ),
                wiring.CALIBRATOR_TABLE: _provider_error(
                    "42703", 'column "calibration_fit_end" does not exist'
                ),
            }
        )
    )

    assert result["ready"] is False
    assert result["code"] == wiring.LEDGER_MISSING_CODE
    assert sorted(result["missing"]) == sorted(
        [wiring.RECEIPT_TABLE, f"{wiring.CALIBRATOR_TABLE}.columns"]
    )


def test_prerequisites_report_an_unrecognized_probe_failure():
    result = wiring.verification_prerequisites(
        _ProbeClient(
            {wiring.RECEIPT_TABLE: _provider_error("42501", "permission denied for table")}
        )
    )

    assert result["ready"] is False
    assert result["code"] == wiring.PROBE_FAILED_CODE
    assert result["probe_failures"][0]["stage"] == "VERIFICATION_PREREQUISITE_PROBE"
    assert result["probe_failures"][0]["provider_code"] == "42501"
    assert result["probe_failures"][0]["can_execute"] is False


def test_prerequisite_codes_are_typed_verification_codes():
    for code in (
        wiring.LEDGER_MISSING_CODE,
        wiring.CALIBRATOR_EVIDENCE_MISSING_CODE,
        wiring.PROBE_FAILED_CODE,
    ):
        assert code.startswith("NBA_VERIFICATION_")
        assert code.isupper()


# --------------------------------------------------------------------------- #
# Calibration identity
# --------------------------------------------------------------------------- #
def test_calibration_fingerprint_is_stable_and_key_order_independent():
    payload = {"sport": "NBA", "brier": 0.2, "calibration_row_count": 240}

    assert calibration_fingerprint(payload) == calibration_fingerprint(
        dict(reversed(list(payload.items())))
    )
    assert len(calibration_fingerprint(payload)) == 64
    assert calibration_fingerprint({**payload, "brier": 0.21}) != calibration_fingerprint(
        payload
    )


# --------------------------------------------------------------------------- #
# Maintenance integration
# --------------------------------------------------------------------------- #
def _maintenance_module(monkeypatch, *, replay=None):
    module = pytest.importorskip("v17.basketball_model_maintenance")
    monkeypatch.setattr(
        module,
        "hydrate",
        lambda sport, seasons, client=None, now=None: {
            "sport": sport,
            "settled_rows": 10,
            "can_execute": False,
        },
    )
    default_replay = {
        "sport": "NBA",
        "persisted_status": "SHADOW",
        "model_artifact_version": MODEL_VERSION,
        "can_execute": False,
    }
    monkeypatch.setattr(
        module,
        "run_training_replay",
        lambda sport, client=None: dict(replay if replay is not None else default_replay),
    )
    # The storage probe has its own tests; default this helper to a ready probe
    # so each maintenance test exercises the behaviour it names.
    monkeypatch.setattr(
        module,
        "verification_prerequisites",
        lambda db: {
            "ready": True,
            "code": None,
            "missing": [],
            "probes": [],
            "probe_failures": [],
            "can_execute": False,
        },
    )
    return module


def _gate_open_result(**kwargs):
    return {
        "receipt": {"verification_id": "11111111-2222-4333-8444-555555555555"},
        "receipt_digest": "e" * 64,
        "gate_open": True,
        "hard_blockers": [],
        "deferred_blockers": ["CHECK_FAILED:V10", "CHECK_FAILED:V11"],
        "verification_status": "FAIL",
    }


def test_maintenance_passes_and_declares_the_deferral(monkeypatch):
    module = _maintenance_module(monkeypatch)
    captured = {}

    def fake_verify_and_archive(db, **kwargs):
        captured.update(kwargs)
        return _gate_open_result()

    monkeypatch.setattr(module, "verify_and_archive", fake_verify_and_archive)

    result = module.run_basketball_model_maintenance(
        object(), sports=("NBA",), seasons=(2026,)
    )
    row = result["rows"][0]

    assert result["status"] == "COMPLETE"
    assert row["status"] == "SHADOW_EVIDENCE_UPDATED"
    assert row["independent_verification"]["status"] == "PASS"
    assert row["independent_verification"]["deferred_blockers"] == [
        "CHECK_FAILED:V10",
        "CHECK_FAILED:V11",
    ]
    assert row["independent_verification"]["can_execute"] is False
    assert captured["defer"] == wiring.DEFERRED_GOVERNANCE_BLOCKERS
    assert captured["model_artifact_version"] == MODEL_VERSION
    assert result["can_execute"] is False


def test_maintenance_blocks_when_verification_fails(monkeypatch):
    module = _maintenance_module(monkeypatch)
    monkeypatch.setattr(
        module,
        "verify_and_archive",
        lambda db, **kwargs: {
            "receipt": {"verification_id": "11111111-2222-4333-8444-555555555555"},
            "receipt_digest": "e" * 64,
            "gate_open": False,
            "hard_blockers": ["CHECK_FAILED:V6"],
            "deferred_blockers": [],
            "verification_status": "FAIL",
        },
    )

    result = module.run_basketball_model_maintenance(
        object(), sports=("NBA",), seasons=(2026,)
    )
    row = result["rows"][0]

    assert result["status"] == "BLOCKED"
    assert row["code"] == "NBA_INDEPENDENT_VERIFICATION_FAILED"
    assert row["independent_verification"]["status"] == "FAIL"
    assert row["independent_verification"]["hard_blockers"] == ["CHECK_FAILED:V6"]
    assert row["can_execute"] is False
    assert row["probability_publishable"] is False


def test_maintenance_blocks_when_evidence_is_incomplete(monkeypatch):
    module = _maintenance_module(monkeypatch)

    def raise_context_error(db, **kwargs):
        raise VerificationContextError("CALIBRATION_DIGEST_MISSING")

    monkeypatch.setattr(module, "verify_and_archive", raise_context_error)

    result = module.run_basketball_model_maintenance(
        object(), sports=("NBA",), seasons=(2026,)
    )
    row = result["rows"][0]

    assert result["status"] == "BLOCKED"
    assert row["code"] == "NBA_VERIFICATION_EVIDENCE_INCOMPLETE"
    assert row["independent_verification"]["code"] == "CALIBRATION_DIGEST_MISSING"
    assert row["can_execute"] is False


def test_maintenance_blocks_when_the_receipt_cannot_be_archived(monkeypatch):
    module = _maintenance_module(monkeypatch)

    def raise_archive_error(db, **kwargs):
        raise wiring.VerificationArchiveError("NBA_VERIFICATION_ARCHIVE_UNAVAILABLE")

    monkeypatch.setattr(module, "verify_and_archive", raise_archive_error)

    result = module.run_basketball_model_maintenance(
        object(), sports=("NBA",), seasons=(2026,)
    )
    row = result["rows"][0]

    assert result["status"] == "BLOCKED"
    assert row["code"] == "NBA_VERIFICATION_ARCHIVE_UNAVAILABLE"
    assert row["independent_verification"]["can_execute"] is False


def test_maintenance_does_not_fabricate_a_wnba_receipt(monkeypatch):
    module = _maintenance_module(monkeypatch)
    calls = []
    monkeypatch.setattr(
        module,
        "verify_and_archive",
        lambda db, **kwargs: calls.append(kwargs) or _gate_open_result(),
    )

    result = module.run_basketball_model_maintenance(
        object(), sports=("WNBA",), seasons=(2026,)
    )
    row = result["rows"][0]

    assert calls == []
    assert row["independent_verification"]["status"] == "NOT_APPLICABLE"
    assert row["independent_verification"]["reason"] == "HARNESS_SUPPORTS_NBA_ONLY"
    assert row["independent_verification"]["can_execute"] is False


def test_maintenance_reports_no_verification_without_an_artifact_identity(monkeypatch):
    module = _maintenance_module(
        monkeypatch,
        replay={"sport": "NBA", "persisted_status": "SHADOW", "can_execute": False},
    )
    monkeypatch.setattr(
        module,
        "verify_and_archive",
        lambda db, **kwargs: pytest.fail("verification must not run"),
    )

    result = module.run_basketball_model_maintenance(
        object(), sports=("NBA",), seasons=(2026,)
    )
    row = result["rows"][0]

    assert row["status"] == "SHADOW_EVIDENCE_UPDATED"
    assert row["independent_verification"]["status"] == "NOT_APPLICABLE"
    assert (
        row["independent_verification"]["reason"]
        == "REPLAY_DID_NOT_RETURN_ARTIFACT_VERSION"
    )


def test_maintenance_blocks_before_fitting_when_verification_storage_is_missing(
    monkeypatch,
):
    module = _maintenance_module(monkeypatch)
    replay_calls = []
    monkeypatch.setattr(
        module,
        "run_training_replay",
        lambda sport, client=None: replay_calls.append(sport) or {},
    )
    prerequisites = {
        "ready": False,
        "code": wiring.LEDGER_MISSING_CODE,
        "missing": [wiring.RECEIPT_TABLE],
        "probes": [],
        "probe_failures": [],
        "can_execute": False,
    }
    monkeypatch.setattr(module, "verification_prerequisites", lambda db: prerequisites)
    monkeypatch.setattr(
        module, "verify_and_archive", lambda db, **kwargs: pytest.fail("must not verify")
    )

    result = module.run_basketball_model_maintenance(
        object(), sports=("NBA",), seasons=(2026,)
    )
    row = result["rows"][0]

    assert result["status"] == "BLOCKED"
    assert row["code"] == "NBA_VERIFICATION_LEDGER_MISSING"
    assert row["replay_attempted"] is False
    assert row["verification_prerequisites"]["missing"] == [wiring.RECEIPT_TABLE]
    assert row["can_execute"] is False
    assert row["probability_publishable"] is False
    assert replay_calls == []
    assert result["verification_prerequisites"]["ready"] is False
    assert result["can_execute"] is False


def test_maintenance_skips_the_probe_when_no_nba_lane_is_requested(monkeypatch):
    module = _maintenance_module(monkeypatch)
    monkeypatch.setattr(
        module, "verification_prerequisites", lambda db: pytest.fail("must not probe")
    )

    result = module.run_basketball_model_maintenance(
        object(), sports=("WNBA",), seasons=(2026,)
    )

    assert result["verification_prerequisites"] is None
    assert result["rows"][0]["independent_verification"]["status"] == "NOT_APPLICABLE"


# --------------------------------------------------------------------------- #
# Migration contract
# --------------------------------------------------------------------------- #
def test_migration_creates_the_append_only_receipt_ledger():
    text = MIGRATION.read_text(encoding="utf-8")
    lowered = text.lower()

    assert (
        "create table if not exists public.wow_independent_verification_receipts"
        in lowered
    )
    assert "check (can_execute = false)" in lowered
    assert "check (publishable_scope = 'none')" in lowered
    assert "wow_ivr_owner_independence" in lowered
    assert "wow_ivr_pass_is_clean" in lowered
    assert "wow_ivr_fail_not_eligible" in lowered
    assert "wow_reject_immutable_mutation" in lowered
    assert "before update or delete" in lowered
    assert "before truncate" in lowered
    assert (
        "grant select, insert on table public.wow_independent_verification_receipts "
        "to service_role" in lowered
    )
    assert "revoke all privileges" in lowered
    # Additive calibrator evidence only; existing rows stay readable.
    assert "add column if not exists calibration_sha256" in lowered
    assert "add column if not exists calibration_fit_end" in lowered


def test_migration_never_writes_certification_state():
    lowered = MIGRATION.read_text(encoding="utf-8").lower()

    assert "alter table public.wow_team_event_specialist_certifications" not in lowered
    assert "insert into public.wow_team_event_specialist_certifications" not in lowered
    assert "update public.wow_team_event_specialist_certifications" not in lowered
    assert "wow_basketball_team_event_certification " not in lowered


def _corpus(dates=230, seed=7):
    rng = random.Random(seed)
    teams = ("BOS", "NYK", "LAL", "GSW")
    strength = {team: index * 0.2 for index, team in enumerate(teams)}
    start = date(2025, 10, 1)
    rows = []
    for offset in range(dates):
        game_date = start + timedelta(days=offset)
        for home_index, away_index in ((0, 1), (2, 3)):
            home, away = teams[home_index], teams[away_index]
            if offset % 2:
                home, away = away, home
            probability = 0.5 + (strength[home] - strength[away])
            home_wins = rng.random() < probability
            if rng.random() < 0.02:
                home_wins = not home_wins
            base = rng.randint(96, 116)
            home_score, away_score = (
                (base + rng.randint(1, 14), base)
                if home_wins
                else (base, base + rng.randint(1, 14))
            )
            rows.append(
                {
                    "game_id": f"nba-{len(rows) + 1:05d}",
                    "season": 2025,
                    "game_date": game_date.isoformat(),
                    "home_team_id": home,
                    "away_team_id": away,
                    "home_score": home_score,
                    "away_score": away_score,
                }
            )
    return rows


class _FakeSupabase:
    """Minimal PostgREST double: paged reads plus recorded inserts/upserts."""

    def __init__(self, corpus):
        self.corpus = corpus
        self.inserted = {}
        self.upserted = {}

    def table(self, name):
        return _FakeQuery(self, name)


class _FakeQuery:
    def __init__(self, client, table):
        self.client = client
        self.table_name = table
        self.operation = "select"
        self.payload = None
        self.window = (0, 9999)

    def select(self, columns):
        self.operation = "select"
        return self

    def eq(self, key, value):
        return self

    def order(self, column, desc=False):
        return self

    def limit(self, count):
        return self

    def range(self, start, end):
        self.window = (start, end)
        return self

    def insert(self, payload):
        self.operation = "insert"
        self.payload = payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.operation = "upsert"
        self.payload = payload
        self.on_conflict = on_conflict
        return self

    def execute(self):
        if self.operation == "select":
            start, end = self.window
            return SimpleNamespace(data=self.client.corpus[start : end + 1])
        if self.operation == "insert":
            self.client.inserted.setdefault(self.table_name, []).append(self.payload)
            return SimpleNamespace(data=[{}])
        self.client.upserted[self.table_name] = (self.payload, self.on_conflict)
        return SimpleNamespace(data=[{}])


def test_fit_persists_a_recomputable_calibration_identity_and_cutoff():
    client = _FakeSupabase(_corpus())

    result = fit_and_persist("NBA", client=client, provenance_complete=True)
    row = client.inserted["wow_basketball_team_event_calibrators"][0]

    assert row["calibration_sha256"] == result["calibration_sha256"]
    assert len(row["calibration_sha256"]) == 64
    assert row["calibration_fit_end"] == result["calibration_fit_end"]
    assert row["calibration_fit_end"].endswith("+00:00")
    assert row["calibration_fit_end"] < client.inserted[
        "wow_basketball_team_event_model_artifacts"
    ][0].get("created_at", "9999")
    # A reviewer must be able to rebuild the digest from the persisted row alone.
    identity = {
        "sport": row["sport"],
        "calibrator_version": row["calibrator_version"],
        "model_artifact_version": row["model_artifact_version"],
        "calibration_method": row["calibration_method"],
        "coefficients": row["coefficients"],
        "calibration_row_count": row["calibration_row_count"],
        "brier": row["brier"],
        "log_loss": row["log_loss"],
        "ece": row["ece"],
        "calibration_bias": row["calibration_bias"],
        "calibration_fit_end": row["calibration_fit_end"],
    }
    assert calibration_fingerprint(identity) == row["calibration_sha256"]
    assert row["promoted"] is False and row["active"] is False
    assert result["can_execute"] is False


def test_wiring_module_never_enables_execution():
    identity = wiring.VerificationIdentity()

    assert wiring.CAN_EXECUTE is False
    assert wiring.PUBLISHABLE_SCOPE == "NONE"
    assert identity.implementation_owner != identity.verification_owner
    assert identity.independent_verifier == "INDEPENDENT_VERIFICATION"
