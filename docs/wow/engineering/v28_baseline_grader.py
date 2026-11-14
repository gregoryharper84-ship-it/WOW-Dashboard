#!/usr/bin/env python3
"""Read-only local Git census. Proxies are NEVER certified engineering metrics."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

BOUNDARY = "46de7e7b2d9880db5c9c6f936afdd0b017a60601"
START = "2026-08-06T00:00:00+00:00"
END = "2026-11-04T14:07:05+00:00"
CAUSE = re.compile(r"\b(?:because|caused by|root cause|due to|fails? when|fails? because|starv(?:es|ation)|race condition|deadlock)\b", re.I)
REVERT_TITLE = re.compile(r'^Revert(?:\s+"|\s+\S)', re.I)
REVERT_BODY = re.compile(r"(?im)^This reverts commit [0-9a-f]{7,40}\.?$")
CODE = re.compile(r"\b(?:[A-Z][A-Z0-9]+_){1,}[A-Z0-9]+\b")
PROTECTED_PREFIX = (".github/", ".agents/", ".claude/")
PROTECTED_FRAGMENT = ("release", "governance", "security", "auth", "writer_lease", "remote_writer", "trust_root", "probability_routing")
MODEL_FRAGMENT = ("model", "calibrat", "predict", "fitted", "probabilit", "scorer", "specialist")

def git(*args, cwd=None):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if p.returncode:
        raise RuntimeError(f"git {args[0]} failed: {p.stderr.strip()}")
    return p.stdout

def model_named_python_path(paths):
    """Independent path flag; P commits may also change probability-producing code."""
    return any(p.endswith(".py") and any(t in p.lower() for t in MODEL_FRAGMENT) for p in paths)


def production_model_named_python_path(paths):
    """Path-only lead; excludes clearly test-only Python filenames."""
    return any(p.endswith(".py") and any(t in p.lower() for t in MODEL_FRAGMENT)
               and not (p.rsplit("/", 1)[-1].startswith("test_") or "/tests/" in p)
               for p in paths)


def class_from_paths(paths):
    """Safety triage, not final C classification. Inspect actual diff for C."""
    if any(p.startswith(PROTECTED_PREFIX) or any(t in p.lower() for t in PROTECTED_FRAGMENT) for p in paths):
        return "P"
    if model_named_python_path(paths):
        return "C_REVIEW_REQUIRED"
    return "R"

def indicators(message):
    subject = message.splitlines()[0] if message else ""
    return {"revert_explicit": bool(REVERT_TITLE.match(subject) or REVERT_BODY.search(message)),
            "mechanism_lexical": bool(CAUSE.search(message)),
            "failure_codes": sorted(set(CODE.findall(message))),
            "red_first": "UNAVAILABLE_FROM_GIT",
            "mechanism_before_first_edit": "UNAVAILABLE_FROM_GIT",
            "force_push": "UNAVAILABLE_FROM_GIT",
            "verified_done": "UNAVAILABLE_FROM_GIT"}

def parse_commit(raw):
    fields = raw.split("\x1f", 3)
    if len(fields) != 4:
        raise ValueError("bad commit record")
    sha, stamp, parents, message = fields
    return {"sha": sha, "committed_at": stamp, "parents": parents.split() if parents else [], "message": message}

def parse_time(value):
    """Accept Git ISO-8601 UTC Z on Python 3.9+ without altering the instant."""
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))

def is_within(stamp, start=START, end=END):
    t = parse_time(stamp.replace("Z", "+00:00"))
    return parse_time(start) <= t <= parse_time(end)

def red_first_from_evidence(fix_sha, first_edit_at, evidence):
    """Cross-check pre/post test evidence; authenticity still needs independent audit."""
    if not evidence:
        raise ValueError("RED_FIRST_UNAVAILABLE: no recorded execution evidence")
    pre, post = evidence.get("pre"), evidence.get("post")
    if not isinstance(pre, dict) or not isinstance(post, dict):
        return False
    try:
        return (pre.get("test_id") == post.get("test_id")
                and isinstance(pre.get("test_id"), str) and bool(pre["test_id"])
                and isinstance(pre.get("commit_sha"), str) and len(pre["commit_sha"]) == 40
                and pre["commit_sha"] != fix_sha and post.get("commit_sha") == fix_sha
                and type(pre.get("exit_code")) is int and pre["exit_code"] != 0
                and post.get("exit_code") == 0
                and isinstance(pre.get("executed_at"), str)
                and isinstance(post.get("executed_at"), str)
                and parse_time(pre["executed_at"]) < parse_time(first_edit_at)
                and parse_time(post["executed_at"]) >= parse_time(first_edit_at))
    except (KeyError, ValueError, TypeError):
        return False

def same_cause_candidate(a, b):
    """Candidate only, NOT verified rework."""
    if a["sha"] == b["sha"]:
        return False
    if not set(a["indicators"]["failure_codes"]) & set(b["indicators"]["failure_codes"]):
        return False
    if not set(a["paths"]) & set(b["paths"]):
        return False
    delta = abs((parse_time(a["committed_at"]) - parse_time(b["committed_at"])).total_seconds())
    return delta <= 72 * 3600


def build_review_packet(records, pairs):
    """Create SHA-specific audit leads; never decide causal rework or C class."""
    lookup = {r["sha"]: r for r in records}
    potential_c = []
    for r in records:
        if r["class"] != "C_REVIEW_REQUIRED":
            continue
        potential_c.append({
            "sha": r["sha"], "committed_at": r["committed_at"],
            "subject": r.get("subject", ""), "changed_paths": r["paths"],
            "additions": r["diff"]["additions"], "deletions": r["diff"]["deletions"],
            "class_verdict": "UNREVIEWED",
            "required_evidence": "Inspect exact diff; probability, fitting, calibration, routing, thresholds may require C/P classification",
        })
    potential_c.sort(key=lambda r: (parse_time(r["committed_at"]), r["sha"]))
    rework = []
    for p in pairs:
        older, newer = lookup[p["older"]], lookup[p["newer"]]
        shared_files = sorted(set(older["paths"]) & set(newer["paths"]))
        common_codes = sorted(set(older["indicators"]["failure_codes"]) & set(newer["indicators"]["failure_codes"]))
        if not shared_files or not common_codes:
            raise ValueError("CANDIDATE_DOSSIER_INCONSISTENT")
        rework.append({
            "older_sha": older["sha"], "newer_sha": newer["sha"],
            "older_at": older["committed_at"], "newer_at": newer["committed_at"],
            "older_subject": older.get("subject", ""), "newer_subject": newer.get("subject", ""),
            "shared_failure_codes": common_codes, "shared_paths": shared_files,
            "hours_between": round(abs((parse_time(newer["committed_at"]) - parse_time(older["committed_at"])).total_seconds()) / 3600, 3),
            "causal_rework_verdict": "UNREVIEWED",
            "required_evidence": "Read both full patches and original red/green evidence; code/path/time overlap is not proof of a re-fix",
        })
    # Model-named code that was collapsed under P precedence must remain visible.
    overlap = []
    for r in records:
        if r["class"] != "P" or not production_model_named_python_path(r["paths"]):
            continue
        overlap.append({
            "sha": r["sha"], "committed_at": r["committed_at"],
            "subject": r.get("subject", ""), "changed_paths": r["paths"],
            "class_verdict": "P_AND_POSSIBLE_C_UNREVIEWED",
            "required_evidence": "Inspect exact diff for both protected surface and actual probability-producing code",
        })
    overlap.sort(key=lambda r: (parse_time(r["committed_at"]), r["sha"]))
    rework.sort(key=lambda r: (parse_time(r["older_at"]), r["older_sha"], r["newer_sha"]))
    return {
        "C_REVIEW_REQUIRED": potential_c, "P_WITH_MODEL_CODE_PATH_UNREVIEWED": overlap,
        "same_code_same_file_72h": rework,
        "instructions": "All rows UNREVIEWED. Inspect full commit diffs with git show --format=fuller <SHA>; never infer causal proof from a match.",
    }

def census(repo, head=BOUNDARY, start=START, end=END, include_review_packet=False):
    if git("rev-parse", "--is-shallow-repository", cwd=repo).strip() == "true":
        raise RuntimeError("SHALLOW_CLONE: obtain full object history; no denominator emitted")
    sha = git("rev-parse", "--verify", head + "^{commit}", cwd=repo).strip()
    # Two batched Git history reads rather than two subprocesses per commit.
    # Raw commit bodies cannot share a framing stream with NUL-delimited paths.
    fmt = "%x1e%H%x1f%cI%x1f%P%x1f"
    def parse_batch(data):
        """Parse Git's -z path records; never C-unquote filenames or frame commit bodies."""
        rows = {}
        current = None
        for token in data.split("\x00"):
            if not token:
                continue
            if token.startswith("\x1e"):
                fields = token[1:].split("\x1f")
                if len(fields) != 4 or fields[-1] != "":
                    raise ValueError("invalid batched git metadata")
                oid, stamp, parents, _ = fields
                if not re.fullmatch(r"[0-9a-f]{40}", oid) or oid in rows:
                    raise ValueError("invalid or duplicate git commit id")
                rows[oid] = {"committed_at": stamp, "parents": parents.split(), "lines": []}
                current = oid
                continue
            if current is None:
                raise ValueError("orphan Git path record")
            # Git writes one line separator after the pretty-format header.
            # Keep every subsequent byte of the NUL-delimited filename.
            if token.startswith("\n"):
                token = token[1:]
            if token:
                rows[current]["lines"].append(token)
        return rows

    stats = parse_batch(git("log", "-z", "--no-show-signature", "--no-renames", "--root",
                            "--numstat", f"--format={fmt}", sha, cwd=repo))
    new_files = parse_batch(git("log", "-z", "--no-show-signature", "--no-renames", "--root",
                                "--diff-filter=A", "--name-only", f"--format={fmt}", sha, cwd=repo))
    records = []
    for oid, raw in stats.items():
        if not is_within(raw["committed_at"], start, end):
            continue
        paths, added, deleted = [], 0, 0
        for line in raw["lines"]:
            fields = line.split("\t", 2)
            if len(fields) != 3:
                if line.strip():
                    raise ValueError("unparseable numstat at " + oid + ": " + repr(line[:70]))
                continue
            add, remove, filename = fields
            paths.append(filename)
            added += int(add) if add.isdigit() else 0
            deleted += int(remove) if remove.isdigit() else 0
        new = [line for line in new_files.get(oid, {}).get("lines", []) if line]
        # Fetch each in-window body separately: arbitrary text cannot corrupt
        # the batched record framing. This is a one-shot offline census.
        message = git("show", "-s", "--no-show-signature", "--format=%B", oid, cwd=repo)
        item = {"sha": oid, "committed_at": raw["committed_at"], "parents": raw["parents"],
                "diff": {"additions": added, "deletions": deleted,
                         "files": len(paths), "new_files": len(new)},
                "paths": paths,
                "class": class_from_paths(paths) if len(raw["parents"]) <= 1 else "MERGE_UNCLASSIFIED",
                "indicators": indicators(message),
                "subject": message.splitlines()[0] if message else ""}
        records.append(item)
    overlap_counts = Counter(x["class"] for x in records if production_model_named_python_path(x["paths"]))
    groups = Counter(x["class"] for x in records)
    lexical = Counter(x["class"] for x in records if x["indicators"]["mechanism_lexical"])
    reverts = Counter(x["class"] for x in records if x["indicators"]["revert_explicit"])
    sizes = {c: {"changed_lines": sum(x["diff"]["additions"] + x["diff"]["deletions"] for x in records if x["class"] == c),
                 "added_files": sum(x["diff"]["new_files"] for x in records if x["class"] == c)} for c in groups}
    by_code = defaultdict(list)
    for x in records:
        for code in x["indicators"]["failure_codes"]:
            by_code[code].append(x)
    pairs = {}
    for code, matches in by_code.items():
        matches.sort(key=lambda x: parse_time(x["committed_at"]))
        for i, a in enumerate(matches):
            for b in matches[i+1:]:
                delta = (parse_time(b["committed_at"]) - parse_time(a["committed_at"])).total_seconds()
                if delta > 72*3600:
                    break
                if same_cause_candidate(a,b):
                    pairs[(a["sha"],b["sha"])] = {"older":a["sha"],"newer":b["sha"],"shared_code":code}
    result = {"boundary_sha":sha,"start_utc":start,"end_utc":end,
            "population_commits":len(records),"merges":sum(len(x["parents"])>1 for x in records),
            "classes":dict(groups),"model_named_production_python_paths_by_primary_bucket":dict(overlap_counts),
            "mechanism_lexical_counts":dict(lexical),
            "explicit_revert_counts":dict(reverts),"diff_totals_by_class":sizes,
            "same_code_same_file_72h_candidate_count":len(pairs),
            "same_code_same_file_72h_candidates":list(pairs.values()),
            "red_first":"UNAVAILABLE: no pre-edit failed test execution proof in git",
            "force_push":"UNAVAILABLE: rewritten refs/events not retained in ordinary commit DAG",
            "verified_done":"UNAVAILABLE: no independent production SHA/route proof in git",
            "stop_behavior":"UNAVAILABLE: handoff-ending corpus absent",
            "interpretation":"All lexical/candidate counts are screening proxies, NOT causal rates. Manually diff-classify C_REVIEW_REQUIRED; merge commits remain unclassified."}
    if include_review_packet:
        result["review_packet"] = build_review_packet(records, list(pairs.values()))
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--repo",default=".")
    p.add_argument("--head",default=BOUNDARY)
    p.add_argument("--start",default=START)
    p.add_argument("--end",default=END)
    p.add_argument("--output",type=Path)
    p.add_argument("--review-output",type=Path, help="Optional local SHA-specific leads, all UNREVIEWED")
    a=p.parse_args()
    data=census(a.repo,a.head,a.start,a.end,include_review_packet=bool(a.review_output))
    if a.review_output:
        packet=data.pop("review_packet")
        a.review_output.write_text(json.dumps(packet,indent=2,sort_keys=True)+"\n")
    report=json.dumps(data,indent=2,sort_keys=True)+"\n"
    if a.output: a.output.write_text(report)
    else: print(report,end="")

if __name__=="__main__":
    main()
