"""Read-only CLI for one fitted team-state candidate and frozen feature receipts.

Input is a JSON object: {"artifact": {...}, "features": [{...}, ...]}.
The CLI does not look up, train, certify, deploy or place a wager.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from opponent_challenger_bridge import score_existing_team_state_candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_json", type=Path)
    parser.add_argument("--output", type=Path, help="optional local research JSON report")
    args = parser.parse_args()
    try:
        data = json.loads(args.input_json.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        parser.error(f"invalid input file: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("artifact"), dict) or not isinstance(data.get("features"), list):
        parser.error("expected object with artifact object and features list")
    rows = []
    for feature in data["features"]:
        if not isinstance(feature, dict):
            parser.error("each feature receipt must be a JSON object")
        rows.append(score_existing_team_state_candidate(feature, data["artifact"]))
    output = {"status":"RESEARCH_ONLY", "can_execute":False,
              "model_promoted":False,"sport":"NFL", "rows":rows,
              "candidate_scores":sum(r.get("candidate_probability") is not None for r in rows),
              "data_provenance_verified_externally":False}
    result = json.dumps(output, sort_keys=True, indent=2, allow_nan=False)+"\n"
    if args.output:
        args.output.write_text(result,encoding="utf-8")
    print(result,end="")
    return 0 if all(r.get("candidate_probability") is not None for r in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
