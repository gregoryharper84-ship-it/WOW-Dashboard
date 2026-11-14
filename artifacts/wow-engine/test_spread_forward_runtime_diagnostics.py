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
