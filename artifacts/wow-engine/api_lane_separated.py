"""Outermost WOW production API with calibration/publication lane separation.

Keeps the full current NCAAF/prop/settlement/pick-request route stack, replacing
only POST /score-prop after every inherited wrapper has been installed.
"""
from __future__ import annotations

import api_ncaaf_acceptance as base
from calibration_publication_api import install_calibration_publication_lane_separation

app = base.app
install_calibration_publication_lane_separation(app, base.base.market_api)
