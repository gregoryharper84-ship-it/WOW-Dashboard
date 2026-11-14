"""End-to-end offline reader: can never report publishable probability."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "kalshi_weather_v2"
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("standalone_noaa_probe_testing", ROOT / "noaa_evidence_probe.py")
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def inputs(tmp_path):
    metar = tmp_path / "metar.json"
    index = tmp_path / "nbm.idx"
    metar.write_text('[{"icaoId":"KDFW","obsTime":1793136600,"temp":28.0}]')
    index.write_text('1:0:d=2026102712:TMP:2 m above ground:3 hour fcst:\n')
    return ["--station", "KDFW", "--as-of", "2026-10-27T22:00:00Z",
            "--metar-json", str(metar), "--nbm-index", str(index),
            "--nbm-cycle", "2026-10-27T12:00:00Z", "--lead-hours", "3"]


def test_end_to_end_no_probabilities(tmp_path, capsys):
    assert m.main(inputs(tmp_path)) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "CORROBORATING_EVIDENCE_INSPECTED"
    assert output["metar"]["station_icao"] == "KDFW"
    assert output["nbm"]["two_meter_temperature_records"] == 1
    assert output["settlement_authority_verified"] is False
    assert output["model_probability_available"] is False
    assert output["probability_publishable"] is False
    assert output["can_execute"] is False


def test_rejects_wrong_station_and_naive_time(tmp_path, capsys):
    argv = inputs(tmp_path)
    argv[1] = "KDAL"
    assert m.main(argv) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "EVIDENCE_REJECTED"
    argv = inputs(tmp_path)
    argv[3] = "2026-10-27T22:00:00"
    assert m.main(argv) == 2
    assert json.loads(capsys.readouterr().out)["probability_publishable"] is False


def test_no_temperature_nbm_is_rejected_not_false_green(tmp_path, capsys):
    argv = inputs(tmp_path)
    Path(argv[7]).write_text('1:0:d=2026102712:RH:2 m above ground:3 hour fcst:\n')
    assert m.main(argv) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "EVIDENCE_REJECTED"
    assert out["probability_publishable"] is False
