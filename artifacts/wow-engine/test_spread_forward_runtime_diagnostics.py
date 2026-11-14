"""Spread-forward shadow runtime failures must be diagnosable without leaking secrets (Class R, observability only)."""
from v17 import spread_margin_replay_route as route


class APIError(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def _boom():
    raise APIError('relation "wow_ncaaf_spread_forward_shadow" does not exist', "42P01")


def test_forward_runtime_failure_exposes_bounded_diagnostic_and_stays_fail_closed():
    out = route._execute_forward("NCAAF", "EVT-1", _boom)
    assert out["status"] == "BLOCKED"
    assert out["code"] == "NCAAF_SPREAD_FORWARD_SHADOW_RUNTIME_FAILED"
    assert out["error_type"] == "APIError"
    assert out["error_code"] == "42P01"
    assert "does not exist" in out["error_message"]
    assert out["can_execute"] is False and out["probability_publishable"] is False


def test_diagnostic_message_is_truncated_and_redacts_bearer_and_jwt_material():
    secret = "Bearer abc.def.ghi eyJhbGciOiJIUzI1NiJ9.payload.sig " + "x" * 2000

    def _leak():
        raise RuntimeError(secret)

    out = route._execute_forward("NFL", "EVT-2", _leak)
    assert len(out["error_message"]) <= 300
    assert "eyJ" not in out["error_message"] and "abc.def.ghi" not in out["error_message"]


def test_quoted_credential_fields_are_redacted():
    def _leak():
        raise RuntimeError("""upstream {"password": "plain-secret"} and {'api_key': 'plain-secret2'} and "token":"plain-secret3" """)

    out = route._execute_forward("NCAAF", "EVT-3", _leak)
    assert "plain-secret" not in out["error_message"]
    assert "plain-secret2" not in out["error_message"]
    assert "plain-secret3" not in out["error_message"]


def test_delimiter_led_and_spaced_secret_values_are_fully_redacted():
    def _leak():
        raise RuntimeError('a password=,lead-comma-secret b token=}brace-secret c secret=]bracket-secret d {"password": "two words secret"} e api_key=mid,comma-secret')

    out = route._execute_forward("NCAAF", "EVT-4", _leak)
    msg = out["error_message"]
    for leaked in ("lead-comma-secret", "brace-secret", "bracket-secret", "two words secret", "words", "mid,comma-secret", "comma-secret"):
        assert leaked not in msg, leaked
