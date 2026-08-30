"""Outermost WOW production API with calibration/publication lane separation.

Keeps the full current NCAAF/prop/settlement/pick-request route stack, replacing
only POST /score-prop after every inherited wrapper has been installed.
"""
from __future__ import annotations

import api_ncaaf_acceptance as base
import calibration_publication_api as lane_patch

app = base.app
# FastAPI resolves postponed route annotations from the defining module's global
# namespace. Expose the actual production market module there before installing
# the route so ``market_api.ScorePropRequest`` resolves deterministically.
lane_patch.market_api = base.base.market_api
lane_patch.install_calibration_publication_lane_separation(app, base.base.market_api)
