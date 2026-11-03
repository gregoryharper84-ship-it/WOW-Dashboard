"""Read-only P0 delivery collector regression and hostile-condition tests."""
from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from v17 import delivery_receipt as d

NOW = datetime(2026, 11, 2, 23, 59, tzinfo=timezone.utc)
SHA = "e" * 40
CFG = {
    "SUPABASE_URL": "https://iczfhsmjrrafhvcpmqhr.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "fake-test-only",
    "RENDER_API_KEY": "fake-test-only",
    "RENDER_SERVICE_ID": "srv-da7sa9gu01pc73brt80g",
    "WOW_DELIVERY_EXPECTED_SHA": SHA,
}


class StubDB:
    def __init__(self):
        self.calls = []

    def count(self, table, **filters):
        self.calls.append((table, filters))
        if table == "wow_event_source_attempts":
            return 21
        if table == "wow_calibrators":
            return 0 if str(filters.get("fitted_at", "")).startswith("gt.") else 10
        if table == "wow_action_invocation_receipts" and "caller_class" in filters:
            if str(filters.get("occurred_at", "")).startswith("gt."):
                return 0
            return 23 if filters["caller_class"] == "neq.SELF_ACCEPTANCE" else 5
        if filters and any(key in filters for key in ("created_at", "occurred_at")):
            if any(str(v).startswith("gt.") for v in filters.values()):
                return 0
            return {
                "wow_predictions": 30, "wow_event_predictions": 0,
                "wow_nfl_event_predictions": 228,
                "wow_action_invocation_receipts": 53,
            }.get(table, 0)
        return {
            "wow_predictions": 4655, "wow_event_predictions": 1989,
            "wow_nfl_event_predictions": 1534,
            "wow_action_invocation_receipts": 4044,
        }.get(table, 0)

    def rows(self, table, select, **filters):
        self.calls.append((table, filters))
        if table == "wow_calibrators":
            return [{"fitted_at": "2026-09-14T02:31:00Z"}], 1
        if table == "wow_nfl_forward_calibration_health":
            return [{"graded_n": 79, "minimum_forward_required": 100,
                     "status": "INSUFFICIENT_FORWARD_EVIDENCE",
                     "certification_recommendation": "DO_NOT_CERTIFY_YET"}], 1
        return [{"created_at": "2026-11-02T21:00:00Z",
                 "occurred_at": "2026-11-02T21:00:00Z"}], 1


def collect(**overrides):
    db = StubDB()
    output = d.build_receipt(
        {**CFG, **overrides}, at=NOW, db=db,
        render_deploy=lambda *_: SHA,
        render_env=lambda *_: "iczfhsmjrrafhvcpmqhr",
    )
    return output, db


def test_missing_credentials_fails_closed_before_any_network():
    result = d.build_receipt({})
    assert result["status"] == "BLOCKED"
    assert result["code"] == "DELIVERY_CONFIGURATION_MISSING"
    assert "RENDER_API_KEY" in result["missing_config_names"]
    assert result["can_execute"] is False


def test_all_event_ledgers_present_and_no_false_mlb_only_outage():
    out, db = collect()
    assert set(out["supabase"]["lanes"]) == set(d._ACTIVITY_TIME)
    assert out["supabase"]["lanes"]["wow_event_predictions"]["recent"] == 0
    assert out["supabase"]["lanes"]["wow_nfl_event_predictions"]["recent"] == 228
    assert any(name == "wow_nfl_event_predictions" for name, _ in db.calls)
    assert out["interpretation"]["mlb_event_idle_is_not_global_event_outage"] is True


def test_calibrators_use_independent_180_day_fitted_at_window():
    out, db = collect()
    assert out["supabase"]["activity_window_hours"] == 72
    assert out["supabase"]["calibrators"]["fit_window_days"] == 180
    assert out["supabase"]["calibrators"]["within_fit_window"] == 10
    assert ("wow_calibrators", {"fitted_at": "gte.2026-05-06T23:59:00Z"}) in db.calls


def test_activity_and_calibrator_windows_independently_configurable():
    out, db = collect(WOW_DELIVERY_ACTIVITY_WINDOW_HOURS="24",
                      WOW_DELIVERY_CALIBRATION_WINDOW_DAYS="90")
    assert out["supabase"]["activity_window_hours"] == 24
    assert out["supabase"]["calibrators"]["fit_window_days"] == 90
    assert ("wow_calibrators", {"fitted_at": "gte.2026-08-04T23:59:00Z"}) in db.calls


def test_future_only_calibrators_and_actions_do_not_look_current():
    class FutureOnlyDB(StubDB):
        def count(self, table, **filters):
            if table == "wow_calibrators":
                self.calls.append((table, filters))
                return 11  # All 11 records are future dated, including the total.
            if table == "wow_action_invocation_receipts" and "caller_class" in filters:
                self.calls.append((table, filters))
                return 23 if filters["caller_class"] == "neq.SELF_ACCEPTANCE" else 5
            return super().count(table, **filters)

        def rows(self, table, select, **filters):
            if table == "wow_calibrators":
                self.calls.append((table, filters))
                assert filters["fitted_at"] == "lte.2026-11-03T00:04:00Z"
                return [], 0  # Only existing fitter records are future dated.
            return super().rows(table, select, **filters)

    db = FutureOnlyDB()
    result = d.build_receipt(
        CFG, at=NOW, db=db, render_deploy=lambda *_: SHA,
        render_env=lambda *_: "iczfhsmjrrafhvcpmqhr",
    )
    assert result["status"] == "BLOCKED"
    assert result["supabase"]["calibrators"]["within_fit_window"] == 0
    assert result["supabase"]["calibrators"]["latest_fit"] is None
    assert result["supabase"]["action_receipt_composition"] == {
        "non_self_acceptance": 0, "chatgpt_action": 0,
    }
    assert ("wow_calibrators", {"fitted_at": "gt.2026-11-03T00:04:00Z"}) in db.calls
    assert ("wow_action_invocation_receipts", {
        "occurred_at": "gt.2026-11-03T00:04:00Z",
        "caller_class": "eq.CHATGPT_ACTION",
    }) in db.calls
    assert result["can_execute"] is False


def test_non_subset_future_count_does_not_produce_negative_recent_counts():
    class InvalidCountDB(StubDB):
        def count(self, table, **filters):
            if table == "wow_calibrators" and str(filters.get("fitted_at", "")).startswith("gt."):
                return 999
            return super().count(table, **filters)

    with pytest.raises(d.DeliveryError, match="DELIVERY_COUNT_NOT_PROVEN"):
        d.build_receipt(
            CFG, at=NOW, db=InvalidCountDB(),
            render_deploy=lambda *_: SHA,
            render_env=lambda *_: "iczfhsmjrrafhvcpmqhr",
        )


def test_calibrator_subset_cannot_exceed_reported_total():
    class ContradictoryDB(StubDB):
        def count(self, table, **filters):
            if table == "wow_calibrators":
                return 1 if not filters else 11
            return super().count(table, **filters)

    with pytest.raises(d.DeliveryError, match="DELIVERY_COUNT_NOT_PROVEN"):
        d.build_receipt(
            CFG, at=NOW, db=ContradictoryDB(),
            render_deploy=lambda *_: SHA,
            render_env=lambda *_: "iczfhsmjrrafhvcpmqhr",
        )


def test_render_env_pagination_finds_second_page_without_network(monkeypatch):
    visited = []
    first = [
        {"envVar": {"key": f"DUMMY_{i}", "value": "safe"}, "cursor": f"cursor_{i}"}
        for i in range(100)
    ]
    second = [
        {"envVar": {"key": "SUPABASE_URL", "value": CFG["SUPABASE_URL"]}}
    ]

    def fake_get(url, _headers):
        visited.append(url)
        return (first if len(visited) == 1 else second), {}

    monkeypatch.setattr(d, "_request_json", fake_get)
    assert d._render_supabase_ref(CFG["RENDER_SERVICE_ID"], "test-key") == (
        "iczfhsmjrrafhvcpmqhr"
    )
    assert len(visited) == 2
    assert "limit=100" in visited[0]
    assert "cursor=cursor_99" in visited[1]


def test_render_env_pagination_rejects_missing_cursor_and_malformed_pages(monkeypatch):
    first = [
        {"envVar": {"key": f"DUMMY_{i}", "value": "safe"}}
        for i in range(100)
    ]
    monkeypatch.setattr(d, "_request_json", lambda *_: (first, {}))
    with pytest.raises(d.DeliveryError, match="DELIVERY_RENDER_ENV_UNVERIFIED"):
        d._render_supabase_ref(CFG["RENDER_SERVICE_ID"], "test-key")
    monkeypatch.setattr(d, "_request_json", lambda *_: ([None], {}))
    with pytest.raises(d.DeliveryError, match="DELIVERY_RENDER_ENV_UNVERIFIED"):
        d._render_supabase_ref(CFG["RENDER_SERVICE_ID"], "test-key")


def test_bogus_window_and_wrong_database_hold():
    with pytest.raises(d.DeliveryError, match="DELIVERY_WINDOW_INVALID"):
        collect(WOW_DELIVERY_CALIBRATION_WINDOW_DAYS="-1")
    with pytest.raises(d.DeliveryError, match="DELIVERY_SUPABASE_PROJECT_MISMATCH"):
        collect(SUPABASE_URL="https://abcdefghijklmnopqrst.supabase.co")


def test_render_database_binding_and_live_sha_fenced():
    with pytest.raises(d.DeliveryError, match="DELIVERY_RENDER_SUPABASE_TARGET_MISMATCH"):
        d.build_receipt(CFG, at=NOW, db=StubDB(), render_env=lambda *_: "wrong",
                        render_deploy=lambda *_: SHA)
    with pytest.raises(d.DeliveryError, match="DELIVERY_RENDER_SHA_MISMATCH"):
        d.build_receipt(CFG, at=NOW, db=StubDB(),
                        render_env=lambda *_: "iczfhsmjrrafhvcpmqhr",
                        render_deploy=lambda *_: "0" * 40)


def test_forward_certificate_is_not_promoted_by_receipt():
    out, _ = collect()
    assert out["supabase"]["nfl_forward_health"]["certification_recommendation"] == "DO_NOT_CERTIFY_YET"
    assert out["interpretation"]["nfl_numerical_package_is_not_final_publication"] is True
    assert out["interpretation"]["no_automatic_model_or_artifact_promotion"] is True
    assert out["can_execute"] is False


def test_future_dated_attempts_reported_separately():
    out, db = collect()
    assert out["supabase"]["future_source_attempts_excluded"] == 21
    assert ("wow_event_source_attempts",
            {"attempted_at": "gt.2026-11-03T00:04:00Z"}) in db.calls


def test_server_must_return_an_exact_count():
    assert d._count_from_header({"content-range": "0-0/79"}) == 79
    assert d._count_from_header({"content-range": "*/0"}) == 0
    with pytest.raises(d.DeliveryError, match="DELIVERY_COUNT_NOT_PROVEN"):
        d._count_from_header({})


def test_non_live_render_deployment_is_not_proof(monkeypatch):
    monkeypatch.setattr(d, "_request_json", lambda *_: (
        [{"deploy": {"status": "build_in_progress", "commit": {"id": SHA}}}], {}
    ))
    with pytest.raises(d.DeliveryError, match="DELIVERY_RENDER_DEPLOY_UNVERIFIED"):
        d._render_deploy_sha(CFG["RENDER_SERVICE_ID"], "key")


def test_cli_missing_config_exit_two_without_counts_or_secret(tmp_path, monkeypatch):
    for name in d.REQUIRED_CONFIG:
        monkeypatch.delenv(name, raising=False)
    file = tmp_path / "receipt.json"
    assert d.main(["--out", str(file)]) == 2
    doc = json.loads(file.read_text())
    assert doc["code"] == "DELIVERY_CONFIGURATION_MISSING"
    assert "supabase" not in doc
    assert "fake-test-only" not in file.read_text()


def test_external_action_evidence_is_not_self_acceptance():
    out, db = collect()
    composition = out["supabase"]["action_receipt_composition"]
    assert composition == {"non_self_acceptance": 23, "chatgpt_action": 5}
    assert ("wow_action_invocation_receipts", {
        "occurred_at": "gte.2026-10-30T23:59:00Z",
        "caller_class": "neq.SELF_ACCEPTANCE",
    }) in db.calls


def test_saved_render_settings_cannot_certify_running_process_binding():
    # An operator can edit saved SUPABASE_URL without redeploying. Matching
    # settings plus the same live SHA cannot establish runtime attribution.
    out, _ = collect()
    assert out["status"] == "BLOCKED"
    assert out["code"] == "DELIVERY_RENDER_RUNTIME_BINDING_UNVERIFIED"
    assert out["render"]["saved_supabase_project_ref_matches"] is True
    assert out["render"]["runtime_supabase_binding_verified"] is False
    assert out["interpretation"]["saved_config_is_not_running_process_proof"] is True
    assert out["can_execute"] is False


@pytest.mark.parametrize("failure", [
    ConnectionResetError("connection reset while streaming response"),
    __import__("http.client", fromlist=["IncompleteRead"]).IncompleteRead(b"", 50),
])
def test_transport_body_failures_are_typed_and_redacted(monkeypatch, failure):
    class BrokenResponse:
        headers = {}
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self, _max_bytes):
            raise failure
    monkeypatch.setattr(d, "urlopen", lambda *_args, **_kwargs: BrokenResponse())
    with pytest.raises(d.DeliveryError, match="^DELIVERY_SOURCE_READ_FAILED$"):
        d._request_json("https://example.com", {})


def test_invalid_render_service_id_holds_before_any_api_call():
    def must_not_query(*_args):
        raise AssertionError("attempted network before validating service identifier")
    with pytest.raises(d.DeliveryError, match="^DELIVERY_RENDER_SERVICE_ID_INVALID$"):
        d.build_receipt({**CFG, "RENDER_SERVICE_ID": "srv-../another"},
                        db=StubDB(), at=NOW,
                        render_env=must_not_query, render_deploy=must_not_query)


def test_invalid_render_service_id_holds_in_direct_env_reader(monkeypatch):
    monkeypatch.setattr(d, "_request_json", lambda *_args: (_ for _ in ()).throw(
        AssertionError("unexpected request")
    ))
    with pytest.raises(d.DeliveryError, match="^DELIVERY_RENDER_SERVICE_ID_INVALID$"):
        d._render_supabase_ref("srv-id/../../env-vars", "test-key")


@pytest.mark.parametrize("redacted", ["****", "", "REDACTED", "https://redacted.example"])
def test_redacted_render_env_returns_env_unverified_not_bad_target(monkeypatch, redacted):
    monkeypatch.setattr(d, "_request_json", lambda *_args: (
        [{"envVar": {"key": "SUPABASE_URL", "value": redacted}}], {}
    ))
    with pytest.raises(d.DeliveryError, match="^DELIVERY_RENDER_ENV_UNVERIFIED$"):
        d._render_supabase_ref(CFG["RENDER_SERVICE_ID"], "test-key")
