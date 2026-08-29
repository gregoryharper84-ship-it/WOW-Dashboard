from __future__ import annotations

import pytest

from ncaaf_static_calibrator import (
    NCAAFStaticCalibrationUnavailable,
    apply_static_calibration,
    resolve_active_calibrator,
)


def calibrator():
    return {
        "ok": True,
        "calibrator_version": "cal-v1",
        "model_artifact_version": "model-v1",
        "calibration_method": "EMPIRICAL_WILSON_BINS_V1",
        "training_n": 60,
        "calibration_health_status": "PASS",
        "probability_publishable": True,
        "payload": {
            "method": "EMPIRICAL_WILSON_BINS_V1",
            "bins": [
                {"raw_min": 0.20, "raw_max": 0.49, "raw_mean": 0.35, "calibrated_probability": 0.40, "wilson_lower": 0.25, "wilson_upper": 0.57},
                {"raw_min": 0.50, "raw_max": 0.80, "raw_mean": 0.64, "calibrated_probability": 0.61, "wilson_lower": 0.44, "wilson_upper": 0.75},
            ],
        },
    }


def test_static_calibrator_returns_point_and_diagnostics_but_never_publication():
    result = apply_static_calibration(raw_probability=0.60, calibrator=calibrator())
    assert result.calibrated_probability == 0.61
    assert result.calibration_lower_diagnostic == 0.44
    assert result.calibration_upper_diagnostic == 0.75
    assert result.probability_publishable is False
    assert result.can_execute is False


def test_static_calibrator_rejects_exact_zero_or_one():
    for p in (0.0, 1.0):
        with pytest.raises(NCAAFStaticCalibrationUnavailable) as exc:
            apply_static_calibration(raw_probability=p, calibrator=calibrator())
        assert exc.value.code == "NCAAF_RAW_PROBABILITY_INVALID"


class Result:
    def __init__(self, data): self.data = data


class RPC:
    def __init__(self, data): self.data = data
    def execute(self): return Result(self.data)


class Client:
    def __init__(self, data): self.data = data
    def rpc(self, name, args):
        assert name == "wow_ncaaf_active_calibrator"
        assert args == {"p_model_artifact_version": "model-v1"}
        return RPC(self.data)


def test_registry_requires_pass_publishable_and_matching_model():
    assert resolve_active_calibrator(Client(calibrator()), model_artifact_version="model-v1")["calibrator_version"] == "cal-v1"
    bad = calibrator(); bad["calibration_health_status"] = "WATCH"
    with pytest.raises(NCAAFStaticCalibrationUnavailable) as exc:
        resolve_active_calibrator(Client(bad), model_artifact_version="model-v1")
    assert exc.value.code == "NCAAF_CALIBRATION_HEALTH_NOT_PASS"
