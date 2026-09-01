from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

# Load the existing final production entrypoint under a private module name so
# every current NCAAF/prop/live/pick-request route remains exactly as-is. Render
# resolves this shim first only when PYTHONSAFEPATH=1 and
# PYTHONPATH=prospective_entrypoint:. are configured.
_root = Path(__file__).resolve().parents[1] / "api_ncaaf_acceptance.py"
_spec = importlib.util.spec_from_file_location("_wow_api_ncaaf_acceptance_base", _root)
if _spec is None or _spec.loader is None:
    raise RuntimeError("base api_ncaaf_acceptance entrypoint unavailable")
_base = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _base
_spec.loader.exec_module(_base)

from mlb_event_prospective_runtime import install_mlb_prospective_event_routes

app = _base.app
install_mlb_prospective_event_routes(app)
app.openapi_schema = None
