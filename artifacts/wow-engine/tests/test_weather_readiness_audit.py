"""Offline, network-free tests for Kalshi readiness audit. No certification implied."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "kalshi_weather_v2" / "readiness_audit.py"
spec = importlib.util.spec_from_file_location("kalshi_readiness_audit", MODULE)
mod = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def fixture(*, fresh=False):
    old = "2026-10-07T08:16:00Z"
    new = "2026-10-27T20:50:00+00:00"
    return {
        "as_of": "2026-10-27T21:20:00Z",
        "counts": dict(rules=1738, rules_with_settlement_source=1738, sources=2615,
                       predictions=421, numeric_probability_pairs=0,
                       publishable_predictions=0, calibration_profiles=0,
                       certified_calibration_profiles=0, outcomes=12,
                       scored_outcomes=0, market_snapshots=484,
                       friction_verified_snapshots=0),
        "latest": dict(rules=new if fresh else "2026-10-07T12:01:00Z", sources=new if fresh else old,
                       predictions=new if fresh else old, outcomes="2026-09-14T02:18:00Z",
                       market_snapshots=new if fresh else old),
    }


def test_stale_shadow_fail_closed():
    r = mod.evaluate(fixture())
    assert r["capture_health"] == "CAPTURE_STALE_OR_ABSENT"
    assert r["empirical_readiness"] == "UNVERIFIED_NO_PLAY"
    assert r["probability_publishable"] is False
    assert r["can_execute"] is False
    codes = {g["code"] for g in r["gates"] if g["state"] == "FAIL"}
    assert {"RULES_STALE", "SOURCES_STALE", "PREDICTIONS_STALE", "MARKET_SNAPSHOTS_STALE", "NO_CERTIFIED_CALIBRATION", "NO_SCORED_OUTCOMES", "NO_INDEPENDENT_NUMERIC_PROBABILITIES"}.issubset(codes)


def test_recent_capture_does_not_imply_certification():
    r = mod.evaluate(fixture(fresh=True))
    assert r["capture_health"] == "RECENT_SHADOW_EVIDENCE"
    assert r["probability_publishable"] is False
    assert r["empirical_readiness"] == "UNVERIFIED_NO_PLAY"


def test_even_flagged_publishable_requires_independent_v17_proof():
    data = fixture(fresh=True)
    data["counts"].update(certified_calibration_profiles=2, calibration_profiles=2, numeric_probability_pairs=12,
                          scored_outcomes=4, friction_verified_snapshots=8, publishable_predictions=2)
    r = mod.evaluate(data)
    assert r["probability_publishable"] is False
    assert r["edge_publishable"] is False
    assert r["can_execute"] is False
    assert r["capability_status"] == "NOT_ATTESTED_BY_THIS_TOOL"


@pytest.mark.parametrize("change", [
    lambda d: d.update(as_of="2026-10-27T21:20:00"),
    lambda d: d["latest"].update(sources="2026-10-29T21:20:00Z"),
    lambda d: d["counts"].update(predictions=-1),
    lambda d: d["counts"].update(predictions=True),
    lambda d: d["counts"].update(numeric_probability_pairs=600),
    lambda d: d["latest"].update(market_snapshots=None),
    lambda d: d["counts"].update(sources=0),
    lambda d: d.pop("counts"),
])
def test_bad_snapshot_rejected(change):
    data = fixture()
    change(data)
    with pytest.raises(mod.InvalidSnapshot):
        mod.evaluate(data)


def test_missing_and_zero_counts_fail_closed():
    data = fixture()
    for k in ("sources", "predictions", "market_snapshots", "outcomes", "rules"):
        data["counts"][k] = 0
        data["latest"][k] = None
    data["counts"]["rules_with_settlement_source"] = 0
    data["counts"]["publishable_predictions"] = 0
    r = mod.evaluate(data)
    assert r["capture_health"] == "CAPTURE_STALE_OR_ABSENT"
    assert "SOURCES_ABSENT" in [x["code"] for x in r["gates"]]


def test_command_line_produces_typed_hold(tmp_path, capsys):
    f = tmp_path / "data.json"
    f.write_text(json.dumps({"report": fixture()}), encoding="utf-8")
    exit_code = mod.main(["--input", str(f)])
    out = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert out["probability_publishable"] is False


def test_command_line_fails_closed_on_corrupt_payload(tmp_path, capsys):
    f = tmp_path / "corrupt.json"
    f.write_text("not json", encoding="utf-8")
    assert mod.main(["--input", str(f)]) == 2
    assert json.loads(capsys.readouterr().out)["can_execute"] is False


def test_staleness_boundary():
    data = fixture(fresh=True)
    data["latest"].update(sources="2026-10-27T19:20:00Z", predictions="2026-10-27T19:20:00Z", market_snapshots="2026-10-27T19:20:00Z")
    assert mod.evaluate(data)["capture_health"] == "RECENT_SHADOW_EVIDENCE"
    data["latest"]["sources"] = "2026-10-27T19:19:59Z"
    assert mod.evaluate(data)["capture_health"] == "CAPTURE_STALE_OR_ABSENT"
