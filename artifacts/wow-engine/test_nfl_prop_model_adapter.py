from nfl_prop_model_adapter import feature_vector, distribution_for_vector
def features(binary=False):
    vals=[0,1,0,1,0,1,0,1,0,1] if binary else [80,92,75,110,88,95,101,84,97,90]
    return {'game_log':vals,'box_score_log':[{'opportunity':20+i%5} for i in range(10)]}
def payload(kind,stat):
    p={'model_family':'NFL_PROP_ROLLING_FITTED_V1','model_kind':kind,'stat_type':stat,'feature_names':['l10_stat_mean','l5_stat_mean','last_stat','l10_opportunity_mean','l5_opportunity_mean','last_opportunity'],'feature_mean':[50,50,50,20,20,20],'feature_scale':[25,25,25,5,5,5],'coef':[1,0,0,0,0,0],'intercept':80 if 'GAUSSIAN' in kind else 0,'blend_weight_fitted':0.5,'max_abs_z_for_coverage':6.0,'feature_transform_version':'NFL_PROP_ROLLING_FORM_V1'}
    if 'GAUSSIAN' in kind: p.update(residual_sigma=18.0,support_min=-50,support_max=400)
    return p
def test_yardage_pmf():
    s,_=distribution_for_vector(payload('GAUSSIAN_RIDGE_BLEND_V1','RUSHING_YARDS'),feature_vector(features())); assert abs(sum(s.values())-1)<1e-9
def test_td_pmf():
    s,_=distribution_for_vector(payload('BERNOULLI_LOGISTIC_BLEND_V1','ANYTIME_TD'),feature_vector(features(True))); assert set(s)=={0,1}; assert abs(sum(s.values())-1)<1e-12
