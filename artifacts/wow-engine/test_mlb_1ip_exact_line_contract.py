from test_pick_request_1ip_integration import _build, _post, _row


def test_non_grid_line_inside_min_max_is_rejected_as_certified_support_ood(monkeypatch):
    request_row = _row("non-grid")
    request_row["line"] = 16.5
    body = _post(_build(monkeypatch), [request_row]).json()
    assert body["rows_in"] == 1
    assert body["rows_completed"] == 0
    assert body["rows_rejected"] == 1
    assert body["rows_held"] == 0
    assert body["reconciliation_pass"] is True
    row = body["rows"][0]
    assert row["terminal_status"] == "REJECTED"
    assert row["terminal_label"] == "REJECT_OOD"
    assert row["code"] == "MLB_1IP_LINE_OUTSIDE_CERTIFIED_SUPPORT"
    assert row["model_evaluated"] is False
    assert row["pick_rejected"] is True
    assert row["infrastructure_blocked"] is False
    assert row["can_execute"] is False
    assert row["probability_publishable"] is False
