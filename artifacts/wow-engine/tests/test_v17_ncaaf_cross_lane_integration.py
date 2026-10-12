"""Combined only: CFBD identity -> historical features -> fitted research shadow.

No publishable team/event probability, calibration or betting route is created.
This regression belongs to the non-release integration draft #1631.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from v17.ncaaf_current_event_result_form import (
    NCAAFForwardFeatureUnavailable,
    current_event_feature_package_from_cfbd,
)
from v17.ncaaf_result_form_candidate import (
    FEATURE_NAMES, FEATURE_SCHEMA_VERSION, MODEL_FAMILY,
)
from v17.ncaaf_result_form_shadow_scorer import (
    NCAAFShadowScoreBlocked, score_ncaaf_research_shadow,
)

START = "2026-10-10T18:00:00+00:00"


def _digest(obj):
    return sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class _CFBD:
    def __init__(self, neutral=False, home="Louisville"):
        self.neutral = neutral
        self.home = home

    def games(self, *, year, classification):
        assert year == 2026
        assert classification == "fbs"
        return SimpleNamespace(rows=[{
            "id": "401899800",
            "startDate": START,
            "homeTeam": self.home,
            "awayTeam": "Florida State",
            "neutralSite": self.neutral,
        }])


def _settled():
    out = []
    base = datetime(2026, 9, 1, 16, tzinfo=timezone.utc)
    for i in range(4):
        t = base + timedelta(days=7*i)
        out.append({
            "official_event_id": f"lou-{i}",
            "event_start_time": t.isoformat(),
            "home_team": "Louisville",
            "away_team": f"Lou prior opponent {i}",
            "home_points": 26+i,
            "away_points": 12,
            "home_won": True,
            "result_source": "CFBD:/games",
            "result_source_timestamp": (t+timedelta(hours=4)).isoformat(),
        })
        out.append({
            "official_event_id": f"fsu-{i}",
            "event_start_time": t.isoformat(),
            "home_team": f"FSU prior opponent {i}",
            "away_team": "Florida State",
            "home_points": 10,
            "away_points": 24+i,
            "home_won": False,
            "result_source": "CFBD:/games",
            "result_source_timestamp": (t+timedelta(hours=4)).isoformat(),
        })
    return out


def _candidate():
    artifact = {
        "artifact_format": "STANDARDIZED_LOGISTIC_JSON_V1",
        "model_family": MODEL_FAMILY,
        "feature_names": list(FEATURE_NAMES),
        "scaler_mean": [0.0]*len(FEATURE_NAMES),
        "scaler_scale": [1.0]*len(FEATURE_NAMES),
        "coefficients": [0.02]*len(FEATURE_NAMES),
        "intercept": 0.0,
    }
    return {
        "sport": "NCAAF",
        "model_family": MODEL_FAMILY,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "model_artifact_version": "NCAAF_RESULT_FORM_LOGIT_V1_abcdef1234_abcdef5678",
        "training_dataset_hash": "a"*64,
        "artifact_payload": artifact,
        "artifact_checksum": _digest(artifact),
        "research_screen_pass": True,
        "lifecycle_state": "CANDIDATE",
        "promoted": False,
        "active": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def _features(cfbd=None):
    return current_event_feature_package_from_cfbd(
        _settled(), event_start_time=START,
        home_team="Louisville Cardinals",
        away_team="Florida State Seminoles",
        neutral_site=False, client=cfbd or _CFBD(),
    )


def test_real_resolver_handoff_to_fitted_shadow_stays_nonpublishing():
    features = _features()
    shadow = score_ncaaf_research_shadow(_candidate(), features)
    assert features["source_manifest"]["home_team"] == "Louisville"
    assert features["source_manifest"]["away_team"] == "Florida State"
    assert features["source_manifest"]["canonical_neutral_site"] is False
    assert shadow["official_event_id"] == "401899800"
    assert shadow["source_manifest_sha256"] == features["source_manifest_sha256"]
    assert shadow["features_sha256"] == features["features_sha256"]
    assert 0 <= shadow["research_raw_probability"] <= 1
    assert shadow["model_probability"] is None
    assert shadow["calibrated_probability"] is None
    assert shadow["calibrated_lower_bound"] is None
    assert shadow["rank_eligible"] is False
    assert shadow["probability_publishable"] is False
    assert shadow["can_execute"] is False


def test_cross_lane_cfbd_neutral_contradiction_blocks_before_fitted_shadow():
    with pytest.raises(NCAAFForwardFeatureUnavailable) as exc:
        _features(_CFBD(neutral=True))
    assert exc.value.code == "NCAAF_FORWARD_NEUTRAL_SITE_SOURCE_CONTRADICTION"


def test_cross_lane_wrong_cfbd_school_refuses_prior_form():
    with pytest.raises(NCAAFForwardFeatureUnavailable) as exc:
        _features(_CFBD(home="Michigan State"))
    assert exc.value.code == "NCAAF_CANONICAL_EVENT_NOT_FOUND"


def test_cross_lane_feature_tampering_cannot_reuse_valid_source_manifest():
    features = _features()
    features["features"]["home_win_rate_prior"] = 0.0
    with pytest.raises(NCAAFShadowScoreBlocked) as exc:
        score_ncaaf_research_shadow(_candidate(), features)
    assert exc.value.code == "NCAAF_SHADOW_FEATURE_HASH_MISMATCH"
