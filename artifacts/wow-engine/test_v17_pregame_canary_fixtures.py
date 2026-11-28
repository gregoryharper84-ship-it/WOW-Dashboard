"""Contract tests for the shared canary fixture adapter and no-write sink."""

import json
from datetime import datetime, timedelta, timezone

from v17 import multisport_prop_live_canary as canary
from v17 import pregame_canary_fixtures as subject
from v17.prop_snapshot_sink import NoWriteSnapshotStore

NOW = datetime(2026, 11, 22, 15, tzinfo=timezone.utc)


def _receipt(**overrides):
    base = {
        "event_id": "2026_12_DAL_PHI",
        "event_start_time": "2026-11-22T20:25:00+00:00",
        "sport": "NFL",
        "player": "Test Player",
        "stat_type": "RECEIVING_YARDS",
        "line": 55.5,
        "side": "MORE",
        "source_type": "AUTONOMOUS_DISCOVERY",
        "platform": "ESPN_NFL_IDENTITY_NFLVERSE_STATS_FORWARD_V1",
        "opponent": "PHI",
        "source_snapshot_id": "snap-1",
        "write_status": "SNAPSHOT_WRITE_SUCCEEDED",
        "terminal_reason": "PERSISTED_AWAITING_CANONICAL_READBACK",
        "can_execute": False,
    }
    base.update(overrides)
    return base


def test_receipt_maps_to_exact_canary_row_contract_without_extra_fields():
    row = subject.receipt_to_canary_row(_receipt())
    assert row is not None
    assert tuple(row) == subject.CANARY_ROW_FIELDS
    assert row["direction"] == "MORE"
    assert row["league"] == "NFL"
    assert row["source_type"] == "AUTONOMOUS_DISCOVERY"
    assert row["line"] == 55.5
    assert row["row_key"].startswith("acquired:nfl:")
    for forbidden in ("side", "write_status", "terminal_reason", "source_snapshot_id", "can_execute"):
        assert forbidden not in row


def test_discovery_provenance_is_preserved_and_never_relabelled_as_market_data():
    row = subject.receipt_to_canary_row(_receipt())
    assert row["source_type"] == "AUTONOMOUS_DISCOVERY"
    assert row["source_type"] != "NORMALIZED"


def test_incomplete_receipts_are_dropped_rather_than_repaired():
    assert subject.receipt_to_canary_row(_receipt(line=None)) is None
    assert subject.receipt_to_canary_row(_receipt(event_start_time="not-a-timestamp")) is None
    assert subject.receipt_to_canary_row(_receipt(side="SIDEWAYS", direction=None)) is None
    assert subject.receipt_to_canary_row(_receipt(source_type="MADE_UP")) is None
    assert subject.receipt_to_canary_row(_receipt(event_id="")) is None
    assert subject.receipt_to_canary_row(_receipt(sport="NHL")) is None


def test_only_future_rows_are_retained_and_duplicates_collapse():
    past = _receipt(event_start_time="2026-11-21T20:25:00+00:00")
    future = _receipt()
    rows = [subject.receipt_to_canary_row(past), subject.receipt_to_canary_row(future),
            subject.receipt_to_canary_row(future)]
    kept = subject.future_rows([row for row in rows if row], now=NOW)
    assert len(kept) == 1
    assert kept[0]["event_id"] == "2026_12_DAL_PHI"


def test_sport_is_eligible_only_with_four_complete_future_rows():
    three = [_receipt(player=f"Player {index}") for index in range(3)]
    mapped = subject.fixtures_from_receipts({"NFL": three}, now=NOW)
    assert mapped["configs"] == {}
    assert mapped["sports"]["NFL"]["future_rows"] == 3

    four = [_receipt(player=f"Player {index}") for index in range(4)]
    mapped = subject.fixtures_from_receipts({"NFL": four}, now=NOW)
    assert mapped["sports_eligible"] == ("NFL",)
    assert len(mapped["configs"]["NFL"]) == 4


def test_no_write_sink_answers_existence_probes_empty_and_records_suppressed_writes():
    sink = NoWriteSnapshotStore()
    probe = (
        sink.table("wow_prop_evidence_snapshots")
        .select("source_snapshot_id")
        .eq("event_id", "evt")
        .eq("sport", "NFL")
        .order("captured_at", desc=True)
        .limit(1)
        .execute()
    )
    assert probe.data == []
    assert sink.write_count == 0

    sink.table("wow_prop_evidence_snapshots").upsert({"source_snapshot_id": "snap-1"},
                                                     on_conflict="source_snapshot_id").execute()
    assert sink.write_count == 1
    assert sink.writes[0]["row"]["source_snapshot_id"] == "snap-1"
    assert sink.writes[0]["table"] == "wow_prop_evidence_snapshots"
    assert sink.writes[0]["on_conflict"] == "source_snapshot_id"
    assert sink.mode == "NO_WRITE"


def _configs(days_ahead=3):
    start = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).isoformat()
    rows = [subject.receipt_to_canary_row(_receipt(event_start_time=start, player=f"P{index}"))
            for index in range(4)]
    return {"NFL": rows}


def test_check_live_fixtures_reports_acquired_rows(monkeypatch, capsys):
    module = canary._fixture_module()
    assert module is subject
    monkeypatch.setattr(module, "acquisition_enabled", lambda: True)
    monkeypatch.setattr(module, "acquire_canary_rows", lambda: {
        "status": "ACQUIRED",
        "configs": _configs(),
        "sports_eligible": ["NFL"],
        "snapshot_write_mode": "NO_WRITE",
        "snapshot_writes_suppressed": 4,
        "sports": {},
        "blockers": {},
        "can_execute": False,
    })
    assert canary._check_live_fixtures() == 0
    output = capsys.readouterr().out
    assert '"status": "FUTURE_ACQUIRED_ROWS_PRESENT"' in output
    assert '"fixture_source": "ACQUIRED"' in output
    assert '"can_execute": false' in output


def test_check_live_fixtures_distinguishes_empty_slate_from_acquisition_failure(monkeypatch, capsys):
    module = canary._fixture_module()
    monkeypatch.setattr(module, "acquisition_enabled", lambda: True)
    monkeypatch.setattr(module, "acquire_canary_rows", lambda: {
        "status": "NO_FUTURE_EVENT", "configs": {}, "sports_eligible": [],
        "snapshot_write_mode": "NO_WRITE", "snapshot_writes_suppressed": 0,
        "sports": {}, "blockers": {}, "can_execute": False,
    })
    assert canary._check_live_fixtures() == 78
    assert '"status": "LIVE_CANARY_NOT_RUN_NO_FUTURE_EVENT"' in capsys.readouterr().out

    monkeypatch.setattr(module, "acquire_canary_rows", lambda: {
        "status": "ACQUISITION_FAILED", "configs": {}, "sports_eligible": [],
        "snapshot_write_mode": "NO_WRITE", "snapshot_writes_suppressed": 0,
        "sports": {}, "blockers": {"MLB": "CANARY_ACQUISITION_FAILED:TimeoutError"},
        "can_execute": False,
    })
    assert canary._check_live_fixtures() == 75
    assert '"status": "LIVE_CANARY_ACQUISITION_FAILED"' in capsys.readouterr().out


def test_acquire_canary_rows_wires_all_three_producers_through_no_write_sink(monkeypatch):
    import v17.mlb_prop_evidence_control_plane as mlb
    import v17.nfl_prop_evidence_control_plane as nfl
    import v17.wnba_prop_evidence_acquisition as wnba

    start = (NOW + timedelta(days=2)).isoformat()
    calls = {sport: [] for sport in subject.SPORTS}

    def receipts(sport, stat_type):
        return [
            _receipt(
                sport=sport,
                event_id=f"{sport}:EVT",
                event_start_time=start,
                player=f"{sport} Player {index}",
                stat_type=stat_type,
                line=10.5 + index,
            )
            for index in range(5)
        ]

    def fake_mlb(req, *, db, now, http_get):
        assert isinstance(db, NoWriteSnapshotStore)
        assert req.requested_date and req.max_candidates >= subject.ROWS_PER_SPORT
        calls["MLB"].append(req)
        return {"status": "COMPLETED", "receipts": receipts("MLB", "PITCHER_STRIKEOUTS")}

    def fake_nfl(req, **kwargs):
        assert isinstance(kwargs["db"], NoWriteSnapshotStore)
        assert req.requested_date and req.max_candidates >= subject.ROWS_PER_SPORT
        calls["NFL"].append(req)
        return {"status": "COMPLETED", "receipts": receipts("NFL", "RECEIVING_YARDS")}

    def fake_wnba(**kwargs):
        assert isinstance(kwargs["db"], NoWriteSnapshotStore)
        assert kwargs["requested_date"] and kwargs["max_candidates"] >= subject.ROWS_PER_SPORT
        calls["WNBA"].append(kwargs)
        return {"status": "COMPLETED", "receipts": receipts("WNBA", "POINTS")}

    monkeypatch.setattr(mlb, "acquire_mlb_forward_evidence_batch", fake_mlb)
    monkeypatch.setattr(nfl, "acquire_nfl_forward_evidence_batch", fake_nfl)
    monkeypatch.setattr(wnba, "acquire_wnba_prop_snapshots", fake_wnba)

    result = subject.acquire_canary_rows(now=NOW, http_get=lambda *a, **k: None)

    assert result["status"] == "ACQUIRED"
    assert result["sports_eligible"] == list(subject.SPORTS)
    assert result["snapshot_write_mode"] == "NO_WRITE"
    assert result["snapshot_writes_suppressed"] == 0
    assert result["can_execute"] is False
    for sport in subject.SPORTS:
        assert len(result["configs"][sport]) == subject.ROWS_PER_SPORT
        assert all(row["sport"] == sport for row in result["configs"][sport])
        assert all(row["direction"] == "MORE" for row in result["configs"][sport])
        assert all(row["source_type"] == "AUTONOMOUS_DISCOVERY" for row in result["configs"][sport])
        assert len(calls[sport]) == 1  # stops after the first date with enough rows


def _typed_hold_payload():
    return {
        "request_id": "acquired-live",
        "reconciliation_pass": True,
        "can_execute": False,
        "_canary_elapsed_seconds": 1.0,
        "rows": [
            {
                "row_key": f"r{index}",
                "terminal_status": "HELD",
                "code": "MODEL_UNAVAILABLE",
                "model_evaluated": False,
                "probability_publishable": False,
                "can_execute": False,
            }
            for index in range(4)
        ],
    }


def test_main_acquired_mode_passes_acquired_rows_into_primary_and_stress(monkeypatch):
    configs = _configs()
    captured = {}

    monkeypatch.setattr(canary, "_select_fixtures", lambda: {
        "source": "ACQUIRED", "configs": configs, "report": {"status": "ACQUIRED"},
    })

    def fake_primary(*, sports, fixtures=None):
        captured["primary"] = (tuple(sports), fixtures)
        return [{"sport": sport} for sport in sports]

    def fake_stress(*, sports, fixtures=None):
        captured["stress"] = (tuple(sports), fixtures)
        return {"sports": list(sports)}

    monkeypatch.setattr(canary, "run_primary", fake_primary)
    monkeypatch.setattr(canary, "run_stress", fake_stress)
    monkeypatch.setattr(canary.sys, "argv", ["canary", "all"])

    assert canary.main() == 0
    for phase in ("primary", "stress"):
        sports, fixtures = captured[phase]
        assert sports == ("NFL",)
        assert fixtures is configs
        assert fixtures is not canary.CONFIGS
        assert [row["event_id"] for row in fixtures["NFL"]] != [
            row["event_id"] for row in canary.CONFIGS["NFL"]
        ]


def test_main_acquired_mode_sends_acquired_rows_to_the_request_boundary(monkeypatch):
    configs = _configs()
    posted = []

    monkeypatch.setattr(canary, "_select_fixtures", lambda: {
        "source": "ACQUIRED", "configs": configs, "report": {"status": "ACQUIRED"},
    })
    monkeypatch.setattr(canary, "_post", lambda payload, **_kwargs: posted.append(payload) or _typed_hold_payload())
    monkeypatch.setattr(canary.sys, "argv", ["canary", "primary"])

    assert canary.main() == 0
    assert len(posted) == 1
    assert posted[0]["rows"] == configs["NFL"]
    assert posted[0]["rows"] != canary.CONFIGS["NFL"]
    assert all(row["source_type"] == "AUTONOMOUS_DISCOVERY" for row in posted[0]["rows"])


def test_main_exits_75_without_posting_when_acquisition_returns_no_configs(monkeypatch, capsys):
    monkeypatch.setattr(canary, "_select_fixtures", lambda: {
        "source": "ACQUIRED",
        "configs": None,
        "report": {"status": "ACQUISITION_FAILED", "blockers": {"MLB": "CANARY_ACQUISITION_FAILED:TimeoutError"}},
    })
    monkeypatch.setattr(canary, "_post", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("acquisition failure must not fall back to static rows")
    ))
    monkeypatch.setattr(canary, "run_primary", lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("acquisition failure must not run a live phase")
    ))
    monkeypatch.setattr(canary.sys, "argv", ["canary", "all"])

    assert canary.main() == 75
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "LIVE_CANARY_ACQUISITION_FAILED"
    assert output["fixture_source"] == "ACQUIRED"
    assert output["future_fixture_sports"] == []
    assert output["can_execute"] is False
    assert "CANARY_ACQUISITION_FAILED:TimeoutError" in output["detail"]["blockers"]["MLB"]


def test_check_step_emits_bundle_and_live_step_consumes_it_without_reacquiring(monkeypatch, tmp_path, capsys):
    module = canary._fixture_module()
    configs = _configs()
    acquisitions = []

    def acquire():
        acquisitions.append(True)
        return {
            "status": "ACQUIRED", "configs": configs, "sports_eligible": ["NFL"],
            "snapshot_write_mode": "NO_WRITE", "snapshot_writes_suppressed": 0,
            "sports": {}, "blockers": {}, "can_execute": False,
        }

    monkeypatch.setattr(module, "acquisition_enabled", lambda: True)
    monkeypatch.setattr(module, "acquire_canary_rows", acquire)
    bundle_path = tmp_path / "acquired-fixtures.json"

    assert canary._check_live_fixtures(str(bundle_path)) == 0
    check_output = json.loads(capsys.readouterr().out)
    assert check_output["bundle_emitted"] is True
    assert bundle_path.exists()
    assert len(acquisitions) == 1

    # The live step must not acquire again; consuming a broken provider proves it.
    monkeypatch.setattr(module, "acquire_canary_rows", lambda: (_ for _ in ()).throw(
        AssertionError("live step re-acquired instead of consuming the bundle")
    ))
    posted = []
    monkeypatch.setattr(canary, "_post", lambda payload, **_kwargs: posted.append(payload) or _typed_hold_payload())
    monkeypatch.setattr(canary.sys, "argv", ["canary", "primary", "--fixtures-from", str(bundle_path)])

    assert canary.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["fixture_input"] == "BUNDLE"
    assert posted[0]["rows"] == configs["NFL"]
    assert len(acquisitions) == 1


def test_live_step_fails_closed_when_bundle_is_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(module := canary._fixture_module(), "acquisition_enabled", lambda: True)
    monkeypatch.setattr(module, "acquire_canary_rows", lambda: (_ for _ in ()).throw(
        AssertionError("missing bundle must not trigger re-acquisition")
    ))
    monkeypatch.setattr(canary, "_post", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("missing bundle must not post")
    ))
    monkeypatch.setattr(canary.sys, "argv", [
        "canary", "all", "--fixtures-from", str(tmp_path / "absent.json"),
    ])

    assert canary.main() == 75
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "LIVE_CANARY_ACQUISITION_FAILED"
    assert output["detail"]["code"] == "CANARY_FIXTURE_BUNDLE_UNREADABLE"
    assert output["can_execute"] is False


def test_live_step_rejects_bundle_that_claims_snapshot_writes(monkeypatch, tmp_path, capsys):
    module = canary._fixture_module()
    path = tmp_path / "bundle.json"
    bundle = {
        "bundle_version": canary.FIXTURE_BUNDLE_VERSION,
        "fixture_source": "ACQUIRED",
        "snapshot_write_mode": "WRITE",
        "snapshot_writes_suppressed": 0,
        "acquired_at": datetime.now(timezone.utc).isoformat(),
        "sports_eligible": ["NFL"],
        "configs": _configs(),
        "blockers": {},
        "can_execute": False,
    }
    path.write_text(json.dumps(bundle), encoding="utf-8")
    monkeypatch.setattr(module, "acquisition_enabled", lambda: True)
    monkeypatch.setattr(canary, "_post", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("a non-no-write bundle must not be posted")
    ))
    monkeypatch.setattr(canary.sys, "argv", ["canary", "primary", "--fixtures-from", str(path)])

    assert canary.main() == 75
    output = json.loads(capsys.readouterr().out)
    assert output["detail"]["code"] == "CANARY_FIXTURE_BUNDLE_WRITE_MODE_NOT_NO_WRITE"


def test_live_step_reports_stale_bundle_as_no_future_event(monkeypatch, tmp_path, capsys):
    module = canary._fixture_module()
    past = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    stale_rows = [subject.receipt_to_canary_row(_receipt(event_start_time=past, player=f"P{index}"))
                  for index in range(4)]
    path = tmp_path / "stale.json"
    canary._write_fixture_bundle(str(path), {
        "snapshot_write_mode": "NO_WRITE", "snapshot_writes_suppressed": 0,
        "sports_eligible": ["NFL"], "configs": {"NFL": stale_rows},
        "blockers": {},
    })
    monkeypatch.setattr(module, "acquisition_enabled", lambda: True)
    monkeypatch.setattr(canary, "_post", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("a stale bundle must not post")
    ))
    monkeypatch.setattr(canary.sys, "argv", ["canary", "all", "--fixtures-from", str(path)])

    assert canary.main() == 78
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "LIVE_CANARY_NOT_RUN_NO_FUTURE_EVENT"
    assert output["future_fixture_sports"] == []


def test_corrupt_bundle_rows_are_a_typed_error_not_an_empty_slate(monkeypatch, tmp_path, capsys):
    module = canary._fixture_module()
    configs = _configs()
    tampered = dict(configs["NFL"][0])
    tampered["unexpected_field"] = "tampered"

    cases = {
        "tampered-row.json": {"NFL": [tampered, *configs["NFL"][1:]]},
        "short-sport.json": {"NFL": configs["NFL"][:3]},
        "unknown-sport.json": {"NHL": configs["NFL"]},
    }
    for filename, broken in cases.items():
        path = tmp_path / filename
        canary._write_fixture_bundle(str(path), {
            "snapshot_write_mode": "NO_WRITE", "snapshot_writes_suppressed": 0,
            "sports_eligible": ["NFL"], "configs": broken, "blockers": {},
        })
        monkeypatch.setattr(module, "acquisition_enabled", lambda: True)
        monkeypatch.setattr(canary, "_post", lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("a corrupt bundle must not be posted")
        ))
        monkeypatch.setattr(canary.sys, "argv", ["canary", "all", "--fixtures-from", str(path)])

        assert canary.main() == 75, filename
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "LIVE_CANARY_ACQUISITION_FAILED", filename
        assert output["detail"]["code"].startswith("CANARY_FIXTURE_BUNDLE_"), filename
        assert output["future_fixture_sports"] == [], filename


def test_check_step_emits_no_bundle_when_acquisition_yields_no_rows(monkeypatch, tmp_path, capsys):
    module = canary._fixture_module()
    monkeypatch.setattr(module, "acquisition_enabled", lambda: True)
    monkeypatch.setattr(module, "acquire_canary_rows", lambda: {
        "status": "NO_FUTURE_EVENT", "configs": {}, "sports_eligible": [],
        "snapshot_write_mode": "NO_WRITE", "snapshot_writes_suppressed": 0,
        "sports": {}, "blockers": {}, "can_execute": False,
    })
    path = tmp_path / "never-written.json"

    assert canary._check_live_fixtures(str(path)) == 78
    output = json.loads(capsys.readouterr().out)
    assert output["bundle_emitted"] is False
    assert not path.exists()


def test_producer_data_unobtainable_is_acquisition_failure_not_an_empty_slate(monkeypatch):
    import v17.mlb_prop_evidence_control_plane as mlb
    import v17.nfl_prop_evidence_control_plane as nfl
    import v17.wnba_prop_evidence_acquisition as wnba

    monkeypatch.setattr(mlb, "acquire_mlb_forward_evidence_batch", lambda *_a, **_k: {
        "status": "DATA_UNOBTAINABLE", "receipts": [],
        "blockers": ["MLB_PROP_SCHEDULE_ACQUISITION_FAILED:HTTPStatusError"],
    })
    monkeypatch.setattr(nfl, "acquire_nfl_forward_evidence_batch", lambda *_a, **_k: {
        "status": "COMPLETED", "receipts": [], "blockers": [],
    })
    monkeypatch.setattr(wnba, "acquire_wnba_prop_snapshots", lambda **_k: {
        "status": "COMPLETED", "receipts": [], "blockers": [],
    })

    result = subject.acquire_canary_rows(now=NOW, http_get=lambda *a, **k: None)

    assert result["status"] == "ACQUISITION_FAILED"
    assert result["configs"] == {}
    assert result["blockers"]["MLB"].startswith("CANARY_ACQUISITION_FAILED:")
    assert "HTTPStatusError" in result["blockers"]["MLB"]
    assert "NFL" not in result["blockers"]
    assert result["snapshot_writes_suppressed"] == 0
    assert result["can_execute"] is False


def test_candidate_dates_probe_three_local_days():
    from datetime import timezone as _tz

    dates = subject._candidate_dates(datetime(2026, 11, 27, 21, 0, tzinfo=_tz.utc), "America/Chicago")
    assert dates == ["2026-11-27", "2026-11-28", "2026-11-29"]


def test_static_mode_is_unchanged_and_never_claims_live_coverage(monkeypatch, capsys):
    module = canary._fixture_module()
    monkeypatch.setattr(module, "acquisition_enabled", lambda: False)
    assert canary._check_live_fixtures() == 78
    output = capsys.readouterr().out
    assert '"status": "LIVE_CANARY_NOT_RUN_STALE_STATIC_FIXTURES"' in output
    assert '"fixture_source": "STATIC"' in output
    assert '"live_coverage_certified": false' in output
