from nfl_prop_model_adapter import feature_vector, distribution_for_vector
from prop_distribution_contract import CoverageDecision, RawDiscreteDistribution


def features(binary=False):
    vals=[0,1,0,1,0,1,0,1,0,1] if binary else [80,92,75,110,88,95,101,84,97,90]
    return {'game_log':vals,'box_score_log':[{'opportunity':20+i%5} for i in range(10)]}


def payload(kind,stat):
    p={'model_family':'NFL_PROP_ROLLING_FITTED_V1','model_kind':kind,'stat_type':stat,'feature_names':['l10_stat_mean','l5_stat_mean','last_stat','l10_opportunity_mean','l5_opportunity_mean','last_opportunity'],'feature_mean':[50,50,50,20,20,20],'feature_scale':[25,25,25,5,5,5],'coef':[1,0,0,0,0,0],'intercept':80 if 'GAUSSIAN' in kind else 0,'blend_weight_fitted':0.5,'max_abs_z_for_coverage':6.0,'feature_transform_version':'NFL_PROP_ROLLING_FORM_V1'}
    if 'GAUSSIAN' in kind: p.update(residual_sigma=18.0,support_min=-50,support_max=400)
    return p


def test_yardage_pmf():
    s,_=distribution_for_vector(payload('GAUSSIAN_RIDGE_BLEND_V1','RUSHING_YARDS'),feature_vector(features()))
    assert min(s) == 0
    assert all(isinstance(k, int) and k >= 0 for k in s)
    assert abs(sum(s.values())-1)<1e-9
    # The adapter output must satisfy the same immutable provider contract used
    # by production scoring even when a legacy certified artifact says -50.
    RawDiscreteDistribution(
        support=s,
        coverage=CoverageDecision(in_distribution=True,ood_score=0.0,coverage_failures=()),
        model_artifact_version='test',training_code_sha='a',training_dataset_hash='b',
        feature_schema_version='PROP_FEATURES_V1',feature_transform_sha='c',
        feature_snapshot_hash='d',artifact_checksum='e',inference_timestamp='2026-09-21T00:00:00+00:00'
    )


def test_td_pmf():
    s,_=distribution_for_vector(payload('BERNOULLI_LOGISTIC_BLEND_V1','ANYTIME_TD'),feature_vector(features(True)))
    assert set(s)=={0,1}
    assert abs(sum(s.values())-1)<1e-12
