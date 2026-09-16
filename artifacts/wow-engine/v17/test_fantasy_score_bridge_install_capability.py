from fastapi import Depends, FastAPI

from v17.fantasy_score_forward_cohort_route import (
    install_fantasy_score_candidate_runtime_bridge,
)


def test_bridge_installer_noops_when_market_api_has_no_prop_scorer():
    class MinimalMarketApi:
        pass

    app = FastAPI()
    before = list(app.router.routes)

    installed = install_fantasy_score_candidate_runtime_bridge(
        app,
        auth_dependency=Depends(lambda: None),
        market_api=MinimalMarketApi(),
    )

    assert installed is False
    assert app.router.routes == before
    assert not getattr(app.state, "wow_fantasy_score_candidate_runtime_bridge_installed", False)
