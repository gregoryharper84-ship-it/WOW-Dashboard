import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from v17.ncaaf_forward_candidate_shadow import (ShadowScoringBlocked, score_forward_shadow)

START = datetime(2026, 11, 25, 0, 0, tzinfo=timezone.utc)
AS_OF = datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)
NAMES = ("home_win_rate_prior", "away_win_rate_prior", "home_point_diff_prior", "away_point_diff_prior", "home_games_prior_log", "away_games_prior_log", "home_rest_days_capped", "away_rest_days_capped", "neutral_site")


def game(team, opponent, day, *, win, source_delay_hours=12, suffix=""):
    when = START - timedelta(days=day)
    return {
        "official_event_id": f"game-{team}-{day}{suffix}",
        "event_start_time": when.isoformat(),
        "home_team": team,
        "away_team": opponent,
        "home_points": 30 if win else 20,
        "away_points": 20 if win else 30,
        "home_won": win,
        "result_source": "CFBD:/games",
        "result_source_timestamp": (when + timedelta(hours=source_delay_hours)).isoformat(),
    }


def history():
    return [game('Home School', 'H' + str(i), 15+i*8, win=(i % 2 == 0)) for i in range(4)] + [game('Away School', 'A' + str(i), 16+i*8, win=(i % 2 == 1)) for i in range(4)]


def candidate():
    payload = {"intercept": 0.3, "coefficients": [0.04] * len(NAMES), "scaler_mean": [0.] * len(NAMES), "scaler_scale": [1.] * len(NAMES), "feature_names": list(NAMES), "model_family": "NCAAF_RESULT_FORM_LOGIT_V1", "artifact_format": "STANDARDIZED_LOGISTIC_JSON_V1", "split_policy": "CHRONOLOGICAL_60_20_20"}
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
    return {
      "candidate_id":"candidate-one", "sport":"NCAAF", "league":"NCAAF", "market_family":"OUTRIGHT_WINNER", "feature_schema_version":"NCAAF_RESULT_FORM_PRIOR_V1", "model_family":"NCAAF_RESULT_FORM_LOGIT_V1", "source_policy_id":"NCAAF_SETTLED_PRIOR_RESULTS_RECONSTRUCTION_V1", "artifact_payload":payload,"artifact_checksum":digest, "calibrator_payload":{"method":"IDENTITY_RAW_PROBABILITY_V1","bins":[],"selection_version":"FORWARD_CALIBRATION_SELECTION_V2","selection":{"test_block_used_for_selection":False}}, "lifecycle_state":"CANDIDATE", "research_screen_pass":True,"active":False,"promoted":False,"probability_publishable":False,"can_execute":False, "automatic_certification":False,"automatic_promotion":False,"created_at":"2026-11-17T00:00:00+00:00", "training_dataset_hash":"b"*64,"training_code_sha":"abcdef1234567890", "model_artifact_version":"NCAAF_RESULT_FORM_LOGIT_V1_"+("b"*16)+"_abcdef123456"
    }


def identity():
    return {"event_id":"cfbd-123", "home_team":"Home School", "away_team":"Away School", "neutral_site":False, "event_start_time":START.isoformat(), "identity_provider":"CFBD:/games", "identity_resolution":"CFBD_EXACT_PARTICIPANTS_START_MATCH"}


def score(*, rows=None, model=None, event=None):
    return score_forward_shadow(identity() if event is None else event, history() if rows is None else rows, candidate() if model is None else model, as_of_utc=AS_OF)


def test_genuine_fitted_inference_remains_read_only_nonpublishable():
    receipt = score()
    assert receipt["status"] == "FITTED_SHADOW_ONLY"
    assert 0 < receipt["research_probability_home"] < 1
    assert receipt["model_family"] == "NCAAF_RESULT_FORM_LOGIT_V1"
    assert receipt["model_artifact_version"] == candidate()["model_artifact_version"]
    assert receipt["historical_home_games"] == 4 and receipt["historical_away_games"] == 4
    assert receipt["calibrated_lower_bound"] is None
    assert receipt["probability_publishable"] is False and receipt["rank_eligible"] is False
    assert receipt["can_execute"] is False and receipt["automatic_certification"] is False


def test_model_coefficient_change_changes_fitted_probability():
    before = score()["research_probability_home"]
    changed = candidate()
    changed["artifact_payload"]["intercept"] = -2
    changed["artifact_checksum"] = hashlib.sha256(json.dumps(changed["artifact_payload"],sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
    assert score(model=changed)["research_probability_home"] != before


@pytest.mark.parametrize("mutation,code", [
    (lambda rows: rows+[dict(rows[0])],"NCAAF_SHADOW_HISTORY_DUPLICATE"),
    (lambda rows: rows[:-3],"NCAAF_SHADOW_PRIOR_SAMPLE_INSUFFICIENT"),
    (lambda rows: [dict(rows[0], result_source_timestamp="2026-11-26T00:00:00+00:00")]+rows[1:],"NCAAF_SHADOW_HISTORY_NOT_AS_OF"),
    (lambda rows: [dict(rows[0], home_points=10)]+rows[1:],"NCAAF_SHADOW_HISTORY_OUTCOME_INCONSISTENT"),
])
def test_historical_source_fail_closed(mutation,code):
    with pytest.raises(ShadowScoringBlocked) as error:
        score(rows=mutation(history()))
    assert error.value.code == code


@pytest.mark.parametrize("mutation,code", [
    (lambda c: c.update(active=True),"NCAAF_SHADOW_CANDIDATE_LIFECYCLE_INVALID"),
    (lambda c: c.update(artifact_checksum='0'*64),"NCAAF_SHADOW_ARTIFACT_CHECKSUM_MISMATCH"),
    (lambda c: c.update(calibrator_payload={"method":"UNKNOWN"}),"NCAAF_SHADOW_CALIBRATOR_UNSUPPORTED"),
    (lambda c: c["artifact_payload"].update(feature_names=["wrong"]*len(NAMES)),"NCAAF_SHADOW_ARTIFACT_CHECKSUM_MISMATCH"),
])
def test_candidate_fails_closed(mutation,code):
    c=candidate();mutation(c)
    with pytest.raises(ShadowScoringBlocked) as error:
        score(model=c)
    assert error.value.code == code


def test_future_event_and_unverified_identity_hold():
    i=identity();i["identity_resolution"]="DISCOVERY_ALIAS_ONLY"
    with pytest.raises(ShadowScoringBlocked,match="NCAAF_SHADOW_CANONICAL_IDENTITY_UNVERIFIED"):
        score(event=i)
    i=identity();i["event_start_time"]="2026-11-17T00:00:00+00:00"
    with pytest.raises(ShadowScoringBlocked,match="NCAAF_SHADOW_NOT_PREGAME"):
        score(event=i)

class FakeQuery:
    def __init__(self, rows):
        self.rows = list(rows)
        self.rg = None
        self.lim = None
    def select(self, *args, **kwargs): return self
    def eq(self, key, value):
        self.rows = [row for row in self.rows if row.get(key)==value]
        return self
    def order(self, key, **kwargs):
        self.rows.sort(key=lambda r: str(r.get(key) or ''), reverse=bool(kwargs.get('desc',False)))
        return self
    def range(self, start, end): self.rg=(start,end);return self
    def limit(self, n): self.lim=n; return self
    def execute(self):
        from types import SimpleNamespace
        rows = self.rows
        if self.rg is not None: rows=rows[self.rg[0]:self.rg[1]+1]
        if self.lim is not None: rows=rows[:self.lim]
        return SimpleNamespace(data=list(rows))

class FakeDB:
    def __init__(self, *, games, candidates):
        self.games=games;self.candidates=candidates;self.seen=[]
    def table(self,name):
        self.seen.append(name)
        if name=='wow_ncaaf_training_games': return FakeQuery(self.games)
        if name=='wow_d1_candidate_artifacts': return FakeQuery(self.candidates)
        raise AssertionError('UNEXPECTED_DB_TABLE:'+name)


def test_read_only_governed_database_shadow_requires_canonical_resolver():
    from v17.ncaaf_forward_candidate_shadow import run_fitted_ncaaf_shadow
    db=FakeDB(games=history(),candidates=[candidate()])
    def source_resolver(**params):
        assert params['home_team']=='Home School'
        assert params['away_team']=='Away School'
        return identity()
    result=run_fitted_ncaaf_shadow(db, event_start_time=START.isoformat(), home_team='Home School', away_team='Away School', as_of_utc=AS_OF, identity_resolver=source_resolver)
    assert result['status']=='FITTED_SHADOW_ONLY'
    assert result['research_probability_home']>0
    assert result['probability_publishable'] is False
    assert set(db.seen)=={'wow_ncaaf_training_games','wow_d1_candidate_artifacts'}


def test_zero_candidate_preserves_true_fitted_unavailable():
    from v17.ncaaf_forward_candidate_shadow import run_fitted_ncaaf_shadow
    db=FakeDB(games=history(),candidates=[])
    with pytest.raises(ShadowScoringBlocked,match='NCAAF_SHADOW_FITTED_CANDIDATE_NOT_FOUND'):
        run_fitted_ncaaf_shadow(db, event_start_time=START.isoformat(), home_team='Home School',away_team='Away School',as_of_utc=AS_OF,identity_resolver=lambda **_: identity())


def test_database_does_not_score_with_provider_unverified_identity():
    from v17.ncaaf_forward_candidate_shadow import run_fitted_ncaaf_shadow
    db=FakeDB(games=history(),candidates=[candidate()])
    bad=identity();bad['identity_provider']='ESPN_DISCOVERY'
    with pytest.raises(ShadowScoringBlocked,match='NCAAF_SHADOW_CANONICAL_IDENTITY_UNVERIFIED'):
        run_fitted_ncaaf_shadow(db,event_start_time=START.isoformat(),home_team='Home School',away_team='Away School',as_of_utc=AS_OF,identity_resolver=lambda **_: bad)