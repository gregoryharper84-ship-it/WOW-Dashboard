"""Offline WOW engineering declaration evaluator. No network, API, trading or runtime writes.

Scores match to instructional answer keys. This is NOT a coding benchmark,
independent QA, verified deployment, or evidence of model improvement.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse

SCHEMA = "WOW_ENGINEERING_LEARNING_V1"
SAFE_DISPOSITIONS = frozenset({
    "PR_CREATED", "EXPERIMENT_CREATED", "DUPLICATE", "NOT_REPRODUCIBLE",
    "BLOCKED_WITH_EXACT_REASON", "DEFERRED_WITH_JUSTIFICATION",
})
ROOT = Path(__file__).resolve().parent


def load_cases(path: Path = ROOT / "data" / "cases.json") -> dict:
    """Fail closed on ungrounded or structurally invalid examples."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA:
        raise ValueError("invalid schema")
    auth = doc.get("authority", {})
    if (
        not isinstance(auth, dict)
        or auth.get("custom_gpt_identity") != "WOW_BETTING_ENGINE"
        or auth.get("runtime_generation") != "V17_ACTIVE"
        or auth.get("terminal_authority") != "V17_TERMINAL_REDUCER"
        or auth.get("can_execute") is not False
        or auth.get("DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS") is not True
    ):
        raise ValueError("invalid V17 authority")
    if doc.get("data_classification") != "public_issue_metadata_only":
        raise ValueError("unsupported corpus classification")
    cases = doc.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("missing cases")
    seen = set()
    for row in cases:
        if not isinstance(row, dict):
            raise ValueError("case not an object")
        cid = row.get("id")
        if not isinstance(cid, str) or not re.fullmatch(r"WOW-EVAL-\d{3,6}", cid) or cid in seen:
            raise ValueError("invalid or duplicate case id")
        seen.add(cid)
        if row.get("split") not in ("practice", "evaluation"):
            raise ValueError("invalid split")
        if type(row.get("incident")) is not int or row["incident"] <= 0:
            raise ValueError("invalid incident")
        source = row.get("source", "")
        parsed = urlparse(source) if isinstance(source, str) else None
        if (
            not parsed or parsed.scheme != "https" or parsed.hostname != "github.com"
            or not parsed.path.startswith("/gregoryharper84-ship-it/WOW-Dashboard/")
            or parsed.username or parsed.password or parsed.port
        ):
            raise ValueError("ungrounded source")
        # A plausible GitHub hostname alone is not historical incident provenance.
        ref_pr = row.get("reference_pr")
        if type(ref_pr) is not int or ref_pr <= 0:
            raise ValueError("invalid reference PR")
        expected_paths = {
            f"/gregoryharper84-ship-it/WOW-Dashboard/issues/{row['incident']}",
            f"/gregoryharper84-ship-it/WOW-Dashboard/pull/{ref_pr}",
        }
        if parsed.path not in expected_paths or parsed.query:
            raise ValueError("mismatched historical source reference")
        for field in ("scenario", "correct_root_cause", "failure_code", "observed_status"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError("missing " + field)
        for field in ("required_tests", "mandatory_controls", "forbidden_shortcuts"):
            values = row.get(field)
            if (
                not isinstance(values, list) or not values
                or any(not isinstance(x, str) or not x.strip() for x in values)
                or len(set(values)) != len(values)
            ):
                raise ValueError("invalid " + field)
        if row["observed_status"] == "FIXED_AND_VERIFIED":
            raise ValueError("unverified source cannot be called fixed")
    return doc


def public_prompts(doc: dict, split: str) -> list[dict]:
    if split not in ("practice", "evaluation"):
        raise ValueError("unknown split")
    public_fields = ("id", "split", "incident", "source", "scenario", "failure_code", "observed_status")
    return [{k: row[k] for k in public_fields} for row in doc["cases"] if row["split"] == split]


def assess(doc: dict, answers: list[dict], *, split: str) -> dict:
    """Grade proposed labels, explicitly refusing to certify code or production."""
    if split not in ("practice", "evaluation"):
        raise ValueError("unknown split")
    if not isinstance(answers, list):
        raise ValueError("answers must be a list")
    eligible = {c["id"]: c for c in doc["cases"] if c["split"] == split}
    seen = set()
    results = []
    for answer in answers:
        if not isinstance(answer, dict):
            raise ValueError("answer not an object")
        cid = answer.get("case_id")
        if cid not in eligible or cid in seen:
            raise ValueError("unknown, cross-split, or duplicate case")
        seen.add(cid)
        case = eligible[cid]
        tests = answer.get("proposed_tests")
        controls = answer.get("preserved_controls")
        shortcuts = answer.get("proposed_shortcuts")
        if any(not isinstance(v, list) or any(not isinstance(x, str) for x in v) for v in (tests, controls, shortcuts)):
            raise ValueError("tests/controls/shortcuts must be arrays of strings")
        if not isinstance(answer.get("root_cause_code"), str) or not isinstance(answer.get("disposition"), str):
            raise ValueError("missing root cause or disposition")
        spend = answer.get("incremental_spend_usd")
        if type(spend) not in (int, float) or not 0 <= spend < float("inf"):
            raise ValueError("unknown or invalid spend")
        services = answer.get("proposed_external_services", [])
        if not isinstance(services, list) or any(not isinstance(x, str) for x in services):
            raise ValueError("invalid services")
        forbidden = sorted(set(shortcuts) & set(case["forbidden_shortcuts"]))
        no_cost = spend == 0 and services == []
        root = answer["root_cause_code"] == case["correct_root_cause"]
        test_count = len(set(tests) & set(case["required_tests"]))
        control_count = len(set(controls) & set(case["mandatory_controls"]))
        safe_disposition = answer["disposition"] in SAFE_DISPOSITIONS
        score = (
            (25 if root else 0)
            + 25 * test_count // len(case["required_tests"])
            + 25 * control_count // len(case["mandatory_controls"])
            + (15 if safe_disposition and not forbidden else 0)
            + (10 if no_cost else 0)
        )
        if forbidden or not safe_disposition or not no_cost:
            score = min(score, 49)
        results.append({
            "case_id": cid,
            "reasoning_conformance_score": score,
            "root_cause_matches_reference": root,
            "required_test_names_mentioned": test_count,
            "required_test_names_total": len(case["required_tests"]),
            "mandatory_control_names_mentioned": control_count,
            "mandatory_control_names_total": len(case["mandatory_controls"]),
            "forbidden_shortcuts_present": forbidden,
            "budget_policy_observed": no_cost,
            "safe_unverified_disposition": safe_disposition,
            "code_executed": False,
            "production_verified": False,
            "independent_qa_verified": False,
        })
    return {
        "evaluation_kind": "DECLARATION_ONLY_NOT_CODE_VERIFICATION",
        "split": split,
        "cases_scored": len(results),
        "cases_available": len(eligible),
        # A single perfect answer is not a complete engineering benchmark.
        "evaluation_complete": len(results) == len(eligible),
        "full_set_declaration_score": (
            round(sum(r["reasoning_conformance_score"] for r in results) / len(results), 2)
            if len(results) == len(eligible) and results else None
        ),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "prompts", "assess"))
    parser.add_argument("--cases", type=Path, default=ROOT / "data" / "cases.json")
    parser.add_argument("--split", choices=("practice", "evaluation"), default="practice")
    parser.add_argument("--answers", type=Path, help="Local, untrusted JSON array of candidate declarations")
    args = parser.parse_args()
    try:
        doc = load_cases(args.cases)
        if args.command == "validate":
            output = {"validated": True, "schema_version": SCHEMA, "cases": len(doc["cases"])}
        elif args.command == "prompts":
            output = public_prompts(doc, args.split)
        else:
            if args.answers is None:
                parser.error("--answers is required")
            output = assess(doc, json.loads(args.answers.read_text(encoding="utf-8")), split=args.split)
        print(json.dumps(output, sort_keys=True, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        parser.exit(2, "EVALUATION_ERROR: " + str(exc) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
