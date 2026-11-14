from dataclasses import replace
import pytest
from v17.nhl_total_goals_parity_challenger import (
    CAN_EXECUTE,PROBABILITY_PUBLISHABLE,fit_parity_calibration,parity_adjusted_research_outcomes,
)
from v17.nhl_total_goals_challenger import TotalRow,NHLTotalsResearchError


def test_real_cohort_rate_reference_math_no_certification():
    a=parity_adjusted_research_outcomes(6.22561,0.682927,line=5.5,side="UNDER")
    b=parity_adjusted_research_outcomes(6.22561,0.682927,line=6.0,side="UNDER")
    assert a["research_win_probability"] == pytest.approx(0.440121,abs=1e-5)
    assert a["research_push_probability"] == 0
    assert b["research_push_probability"] == pytest.approx(0.101438,abs=1e-5)
    assert a["calibrated_lower_bound"] is None
    assert not CAN_EXECUTE and not PROBABILITY_PUBLISHABLE


def test_overs_and_unders_mass_normalized_for_exact_line():
    for line in (5.5,6.0,6.5):
        u=parity_adjusted_research_outcomes(6.1,0.68,line=line,side="UNDER")
        o=parity_adjusted_research_outcomes(6.1,0.68,line=line,side="OVER")
        assert sum(u[k] for k in ("research_win_probability","research_push_probability","research_loss_probability")) == pytest.approx(1)
        assert u["research_win_probability"] == pytest.approx(o["research_loss_probability"])
        assert u["research_push_probability"] == pytest.approx(o["research_push_probability"])
        assert not u["probability_publishable"]


def test_parity_fit_uses_only_named_calibration_cohort():
    row=TotalRow("e1","2024-01-01T01:00:00+00:00",2023,"BOS","NYR",7,"2024-01-01T00:59:59+00:00",(1.,)*6,"a"*64)
    data=[replace(row,event_id=f"e{i}",total_goals=6+(i%3)) for i in range(120)]
    data.append(replace(row,event_id="holdout",season_start_year=2024,total_goals=101))
    fit=fit_parity_calibration(data)
    assert fit["calibration_n"]==120
    assert fit["odd_total_share"]==pytest.approx(40/120)
    assert fit["calibrated_lower_bound"] is None
    assert fit["probability_publishable"] is False


@pytest.mark.parametrize("mu,share,line,side",[(0,.68,5.5,"UNDER"),(5,1.0,5.5,"UNDER"),(5,float("nan"),5.5,"UNDER"),(5,.6,5.25,"UNDER"),(5,.6,5.5,"MORE"),(5,.6,True,"UNDER")])
def test_invalid_parity_inputs_fail_closed(mu,share,line,side):
    with pytest.raises(NHLTotalsResearchError):
        parity_adjusted_research_outcomes(mu,share,line=line,side=side)
