"""Adversarial proof regression; no external time service and no keyless signing."""
from datetime import datetime
from hashlib import sha256
import json
from unittest.mock import patch
import pytest
from v17 import pregame_attestation as p

START = "2026-11-12T23:00:00Z"
FROZEN = "2026-11-12T22:00:00Z"
OBSERVED = "2026-11-12T21:45:00Z"
D = "a" * 64


def receipt(**overrides):
    obj = dict(
        contract=p.CONTRACT, prediction_id="synthetic-pred-01", sport="NFL",
        official_event_id="synthetic-game-01", home_team="HOME", away_team="AWAY",
        event_start_time_utc=START, market_type="POINT_SPREAD",
        settlement_basis="FULL_GAME_INCLUDING_OVERTIME",
        selected_side="HOME", signed_home_line="-3.5",
        quote_source_id="fixture-only", quote_observed_at_utc=OBSERVED,
        frozen_at_utc=FROZEN, model_artifact_sha256=D,
        feature_snapshot_sha256="b" * 64, quote_receipt_sha256="c" * 64,
        p_cover=None, p_push=None, p_not_cover=None,
        calibrated_lower_bound=None, calibrated_upper_bound=None,
        model_certification_status="UNQUALIFIED", certification_reference_id=None,
        terminal_authority=p.TERMINAL_AUTHORITY, probability_publishable=False,
        can_execute=False,
    )
    obj.update(overrides)
    return obj


def pair(obj):
    prediction = p.canonical_bytes(obj)
    statement = p.canonical_bytes(p.build_statement(obj))
    return prediction, statement


def test_statement_binds_exact_canonical_frozen_receipt():
    raw, statement = pair(receipt())
    parsed = p.validate_statement(raw, statement)
    assert parsed["prediction_id"] == "synthetic-pred-01"
    st = json.loads(statement)
    assert st["_type"] == p.STATEMENT_TYPE
    assert st["subject"][0]["digest"]["sha256"] == sha256(raw).hexdigest()
    assert st["predicate"]["frozenPrediction"] == parsed


@pytest.mark.parametrize("override", [
    {"selected_side": "UNKNOWN"},
    {"selected_side": []},
    {"sport": {}},
    {"market_type": []},
    {"model_certification_status": []},
    {"can_execute": True},
    {"probability_publishable": "false"},
    {"terminal_authority": "ATTACKER"},
    {"sport": "GOLF"},
    {"market_type": "MONEYLINE"},
    {"settlement_basis": "REGULATION"},
    {"signed_home_line": 3.5},
    {"signed_home_line": "NaN"},
    {"signed_home_line": "1e100"},
    {"model_artifact_sha256": "not-a-hash"},
    {"frozen_at_utc": "2026-11-12T23:00:00Z"},
    {"frozen_at_utc": "2026-11-12T22:00:00"},
    {"quote_observed_at_utc": "2026-11-12T20:00:00Z"},
    {"quote_observed_at_utc": "2026-11-12T22:01:00Z"},
    {"p_cover": "0.58"},
    {"model_certification_status": "CERTIFIED_FITTED"},
    {"probability_publishable": True},
])
def test_invalid_prediction_fields_fail_closed(override):
    with pytest.raises(p.ReceiptHold):
        p.validate_prediction(receipt(**override))


def test_full_distribution_may_be_claimed_but_does_not_certify():
    r = receipt(p_cover="0.58", p_push="0.02", p_not_cover="0.40",
                calibrated_lower_bound="0.50", calibrated_upper_bound="0.66",
                model_certification_status="CERTIFIED_FITTED",
                certification_reference_id="synthetic-not-a-real-certificate",
                probability_publishable=True)
    assert p.validate_prediction(r) == r
    raw, statement = pair(r)
    assert p.validate_statement(raw, statement) == r


@pytest.mark.parametrize("field,value", [
    ("p_not_cover", "0.41"),
    ("calibrated_lower_bound", "0.59"),
    ("p_cover", "Infinity"),
    ("calibrated_upper_bound", "1.1"),
])
def test_invalid_probability_math_rejected(field, value):
    r = receipt(p_cover="0.58", p_push="0.02", p_not_cover="0.40",
                calibrated_lower_bound="0.50", calibrated_upper_bound="0.66")
    r[field] = value
    with pytest.raises(p.ReceiptHold):
        p.validate_prediction(r)


def test_modified_prediction_cannot_reuse_attestation():
    raw, statement = pair(receipt())
    changed = receipt(selected_side="AWAY")
    with pytest.raises(p.ReceiptHold, match="STATEMENT_OR_DIGEST_MISMATCH"):
        p.validate_statement(p.canonical_bytes(changed), statement)


def test_modified_signed_statement_cannot_mismatch_frozen():
    raw, statement = pair(receipt())
    s = json.loads(statement)
    s["predicate"]["frozenPrediction"]["selected_side"] = "AWAY"
    with pytest.raises(p.ReceiptHold, match="STATEMENT_OR_DIGEST_MISMATCH"):
        p.validate_statement(raw, p.canonical_bytes(s))


def test_noncanonical_or_duplicate_keys_are_rejected():
    raw, statement = pair(receipt())
    with pytest.raises(p.ReceiptHold, match="FROZEN_BYTES_NONCANONICAL"):
        p.validate_statement(raw.replace(b":", b": ", 1), statement)
    with pytest.raises(p.ReceiptHold, match="DUPLICATE_JSON_KEY"):
        p.validate_statement(b'{"contract":"x","contract":"y"}', statement)
    with pytest.raises(p.ReceiptHold, match="STATEMENT_BYTES_NONCANONICAL"):
        p.validate_statement(raw, statement.rstrip(b"\n"))


def _files(tmp_path, r=None):
    a, b = pair(r or receipt())
    frozen, statement = tmp_path / "receipt.json", tmp_path / "statement.json"
    frozen.write_bytes(a)
    statement.write_bytes(b)
    return frozen, statement


def test_valid_structure_without_cosign_stays_held(tmp_path):
    frozen, statement = _files(tmp_path)
    with patch.object(p, "_run", return_value=type("Result", (), {"returncode": 1})()):
        result = p.verify(frozen=str(frozen), statement=str(statement), bundle="missing",
                          signer="issuer@example", issuer="https://oidc.example")
    assert result["status"] == "HELD"
    assert "RECEIPT_COSIGN_IDENTITY_OR_SIGNATURE_INVALID" in result["reason_codes"]
    assert not result["card_admission_eligible"]


def test_verified_cosign_without_independent_tsa_stays_held(tmp_path):
    frozen, statement = _files(tmp_path)
    with patch.object(p, "_run", return_value=type("Result", (), {"returncode": 0})()):
        result = p.verify(frozen=str(frozen), statement=str(statement), bundle="bundle",
                          signer="issuer@example", issuer="https://oidc.example")
    assert result["status"] == "HELD"
    assert "RECEIPT_INDEPENDENT_PREGAME_TIME_PROOF_MISSING" in result["reason_codes"]
    assert not result["cryptographic_pregame_verified"]


@pytest.mark.parametrize("stamp,should_pass", [
    ("2026-11-12T22:15:00Z", True),
    ("2026-11-12T23:00:00Z", False),
    ("2026-11-12T23:01:00Z", False),
    ("2026-11-12T21:59:00Z", False),
])
def test_trusted_time_must_be_between_freeze_and_kickoff(tmp_path, stamp, should_pass):
    frozen, statement = _files(tmp_path)
    with patch.object(p, "_run", return_value=type("Result", (), {"returncode": 0})()), \
         patch.object(p, "_tsa_time", return_value=datetime.fromisoformat(stamp.replace("Z", "+00:00"))):
        output = p.verify(frozen=str(frozen), statement=str(statement), bundle="bundle",
                          signer="expected", issuer="https://oidc.example",
                          tsa_response="timestamp.tsr", tsa_roots="trusted.pem")
    assert output["cryptographic_pregame_verified"] is should_pass
    assert output["model_certification_verified"] is False
    assert output["probability_publishable"] is False
    assert output["can_execute"] is False


def test_tsa_signature_or_imprint_failure_is_hold(tmp_path):
    frozen, statement = _files(tmp_path)
    calls = iter([0, 1])
    def run(_args):
        return type("Result", (), {"returncode": next(calls), "stdout": ""})()
    with patch.object(p, "_run", side_effect=run):
        out = p.verify(frozen=str(frozen), statement=str(statement), bundle="bundle",
                       signer="expected", issuer="https://oidc.example",
                       tsa_response="bad.tsr", tsa_roots="trusted.pem")
    assert "RECEIPT_TSA_SIGNATURE_OR_IMPRINT_INVALID" in out["reason_codes"]


def test_referenced_artifact_mismatch_blocks_even_valid_signer(tmp_path):
    frozen, statement = _files(tmp_path)
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"unmatched")
    with patch.object(p, "_run") as run:
        out = p.verify(frozen=str(frozen), statement=str(statement), bundle="bundle",
                       signer="expected", issuer="https://oidc.example",
                       artifact_paths={"model_artifact_sha256": str(artifact)})
    assert "RECEIPT_REFERENCED_ARTIFACT_HASH_MISMATCH" in out["reason_codes"]
    run.assert_not_called()


def test_unsupported_artifact_key_rejected(tmp_path):
    frozen, statement = _files(tmp_path)
    out = p.verify(frozen=str(frozen), statement=str(statement), bundle="bundle",
                   signer="expected", issuer="https://oidc.example",
                   artifact_paths={"unreviewed": str(frozen)})
    assert "RECEIPT_ARTIFACT_NAME_INVALID" in out["reason_codes"]


def test_verified_tsa_time_parser_follows_successful_imprint_verification():
    results = iter([
        type("Result", (), {"returncode": 0, "stdout": "Verification: OK\n"})(),
        type("Result", (), {"returncode": 0,
             "stdout": "Status info:\nTime stamp: Nov 12 22:12:34 2026 GMT\n"})(),
    ])
    with patch.object(p, "_run", side_effect=lambda _args: next(results)) as call:
        observed = p._tsa_time("trusted.tsr", "roots.pem", "exact-statement.json", None)
    assert observed.isoformat() == "2026-11-12T22:12:34+00:00"
    assert call.call_args_list[0].args[0][:6] == [
        "openssl", "ts", "-verify", "-data", "exact-statement.json", "-in"
    ]


def test_tsa_time_parser_rejects_invalid_or_duplicate_output():
    results = iter([
        type("Result", (), {"returncode": 0, "stdout": ""})(),
        type("Result", (), {"returncode": 0, "stdout":
             "Time stamp: Nov 12 22:12:34 2026 GMT\nTime stamp: Nov 12 22:12:34 2026 GMT\n"})(),
    ])
    with patch.object(p, "_run", side_effect=lambda _args: next(results)):
        with pytest.raises(p.ReceiptHold, match="TSA_TIME_UNREADABLE"):
            p._tsa_time("trusted.tsr", "roots.pem", "exact-statement.json", None)
