"""One-shot branch patcher for canonical MLB 1IP ingress integration.

This script exists only to make a deterministic, reviewable source edit from CI
because the repository connector exposes whole-file replacement rather than
line patches. Remove it after the generated source commit is verified.
"""
from pathlib import Path

# Trigger marker: 2026-09-01 canonical-ingress pass.
path = Path(__file__).resolve().parents[1] / "pick_request_runtime.py"
text = path.read_text()

import_anchor = "from mlb_1ip_specialist import score_mlb_1ip, starter_changed\n"
import_line = "from mlb_1ip_ingress_runtime import score_mlb_1ip_ingress\n"
if import_line not in text:
    if import_anchor not in text:
        raise SystemExit("IMPORT_ANCHOR_NOT_FOUND")
    text = text.replace(import_anchor, import_anchor + import_line, 1)

start_marker = 'def _score_mlb_1ip_row(row: "PickRequestRow", row_key: str) -> dict[str, Any]:\n'
end_marker = "\ndef install_pick_request_routes(\n"
start = text.find(start_marker)
end = text.find(end_marker, start)
if start < 0 or end < 0:
    raise SystemExit("ONE_IP_FUNCTION_BOUNDARY_NOT_FOUND")

replacement = '''def _score_mlb_1ip_row(\n    row: "PickRequestRow",\n    row_key: str,\n    *,\n    market_api: Any,\n    request_id: Optional[str],\n) -> dict[str, Any]:\n    """Canonical MLB 1IP path after specialist/capability/artifact preflight.\n\n    Acquisition, mandatory Scout -> Research, controlling-specialist scoring,\n    and provisional final-refresh queuing are delegated to the dedicated 1IP\n    ingress helper. The preflight remains in score_pick_request so a genuinely\n    missing certified artifact still terminates as MODEL_UNAVAILABLE before\n    expensive acquisition begins.\n    """\n    return score_mlb_1ip_ingress(\n        row=row,\n        row_key=row_key,\n        market_api=market_api,\n        request_id=request_id,\n        run_research=_run_mandatory_scout_research,\n        terminal=_terminal,\n        reduce_terminal=reduce_prop_terminal,\n    )\n\n'''
text = text[:start] + replacement + text[end + 1 :]

old_call = "outcomes.append(_score_mlb_1ip_row(row, row_key))"
new_call = "outcomes.append(_score_mlb_1ip_row(row, row_key, market_api=market_api, request_id=batch.request_id))"
if old_call in text:
    text = text.replace(old_call, new_call, 1)
elif new_call not in text:
    raise SystemExit("ONE_IP_CALL_SITE_NOT_FOUND")

path.write_text(text)
print("patched", path)
