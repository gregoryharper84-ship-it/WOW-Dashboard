"""Exact-head, advisory-only review boundary. Never imports or executes PR code."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import urllib.request


REPOSITORY = "gregoryharper84-ship-it/WOW-Dashboard"
MAX_BYTES = 4_000_000
SHA = re.compile(r"[0-9a-f]{40}")
SELF_AUTHOR = re.compile(r"claude|anthropic", re.IGNORECASE)
FIELDS = {
    "repository", "pr_number", "reviewed_sha", "base_sha", "diff_sha256",
    "diff_access_canary", "disposition", "findings", "regression_gaps",
    "security_risks", "next_action",
}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": sorted(FIELDS),
    "properties": {
        **{key: {"type": "string"} for key in FIELDS - {
            "pr_number", "findings", "regression_gaps", "security_risks", "disposition",
        }},
        "pr_number": {"type": "integer"},
        "disposition": {"type": "string", "enum": ["PASS", "HOLD", "INCONCLUSIVE"]},
        "findings": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["file", "line", "severity", "message"],
                "properties": {
                    "file": {"type": "string"}, "line": {"type": "integer", "minimum": 1},
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "message": {"type": "string"},
                },
            },
        },
        "regression_gaps": {"type": "array", "items": {"type": "string"}},
        "security_risks": {"type": "array", "items": {"type": "string"}},
    },
}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def inputs(number, sha, repository):
    require(repository == REPOSITORY, "REPOSITORY_INVALID")
    require(re.fullmatch(r"[1-9][0-9]{0,9}", number) is not None, "PR_NUMBER_INVALID")
    require(SHA.fullmatch(sha) is not None, "EXPECTED_SHA_INVALID")
    return int(number)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("API_REDIRECT_REJECTED")


def api(path, diff=False):
    require(path.startswith(f"/repos/{REPOSITORY}/"), "API_PATH_INVALID")
    request = urllib.request.Request(
        "https://api.github.com" + path,
        headers={
            "Authorization": "Bearer " + os.environ["GH_TOKEN"],
            "Accept": "application/vnd.github.diff" if diff else "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "WOW-readonly-Claude-advisory",
        },
    )
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=45) as response:
        data = response.read(MAX_BYTES + 1)
    require(0 < len(data) <= MAX_BYTES, "API_RESPONSE_EMPTY_OR_TOO_LARGE")
    return data.decode("utf-8") if diff else json.loads(data)


def validate_pr(pr, number, sha, base=None):
    require(pr["number"] == number and pr["state"] == "open", "PR_NOT_OPEN_OR_MISMATCH")
    require(
        pr["head"]["sha"] == sha
        and pr["head"]["repo"]["full_name"] == REPOSITORY
        and pr["base"]["repo"]["full_name"] == REPOSITORY
        and pr["base"]["ref"] == "main",
        "PR_HEAD_OR_REPOSITORY_MISMATCH",
    )
    require(SHA.fullmatch(pr["base"]["sha"]) is not None, "BASE_SHA_INVALID")
    require(base is None or pr["base"]["sha"] == base, "PR_BASE_STALE")
    require(0 < pr["commits"] <= 100 and 0 < pr["changed_files"] <= 100, "PR_REVIEW_LIMIT")
    login = pr["user"]["login"]
    require(isinstance(login, str) and login.strip(), "PR_AUTHOR_MISSING")
    require(not SELF_AUTHOR.search(login), "CLAUDE_SELF_REVIEW")


def validate_commits(commits, count, sha):
    require(len(commits) == count and commits[-1]["sha"] == sha, "COMMIT_EVIDENCE_INCOMPLETE")
    for commit in commits:
        for role in ("author", "committer"):
            identity = commit["commit"][role]
            require(identity.get("name") and identity.get("email"), "COMMIT_IDENTITY_MISSING")
            identities = [identity["name"], identity["email"]]
            if commit.get(role):
                identities.append(commit[role]["login"])
            require(not any(SELF_AUTHOR.search(value) for value in identities), "CLAUDE_SELF_REVIEW")
        trailers = re.findall(
            r"(?im)^co-authored-by:\s*(.+)$", commit["commit"]["message"]
        )
        require(not any(SELF_AUTHOR.search(value) for value in trailers), "CLAUDE_SELF_REVIEW")


def diff_lines(diff, files):
    """Accept bounded ordinary text patches; unsupported/binary/renamed files hold."""
    require(isinstance(files, list) and files, "DIFF_FILES_MISSING")
    paths = []
    for file in files:
        path = file["filename"]
        require(
            re.fullmatch(r"[A-Za-z0-9_./@+ -]+", path) is not None
            and not path.startswith(("/", "-"))
            and all(part not in ("", ".", "..") for part in path.split("/")),
            "DIFF_PATH_UNSUPPORTED",
        )
        require(file["status"] in ("added", "modified", "removed") and file.get("patch"),
                "DIFF_PATCH_UNSUPPORTED")
        paths.append(path)
    headers = re.findall(r"(?m)^diff --git a/(.+) b/(.+)$", diff)
    require(headers == [(path, path) for path in paths], "DIFF_FILE_COVERAGE_MISMATCH")
    result = {}
    for section, file in zip(re.split(r"(?m)^diff --git ", diff)[1:], files):
        path = file["filename"]
        patch_start = section.find("\n@@ ")
        require(patch_start >= 0 and section[patch_start + 1:].rstrip("\n")
                == file["patch"].rstrip("\n"),
                "DIFF_PATCH_COVERAGE_MISMATCH")
        lines = set()
        old = new = remaining_old = remaining_new = 0
        hunks = added = deleted = 0
        for line in section.splitlines()[1:]:
            if line.startswith("@@ "):
                require(remaining_old == remaining_new == 0, "DIFF_HUNK_TRUNCATED")
                match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
                require(match is not None, "DIFF_HUNK_INVALID")
                old, new = int(match[1]), int(match[3])
                remaining_old = int(match[2] or "1")
                remaining_new = int(match[4] or "1")
                hunks += 1
            elif hunks and line[:1] in (" ", "+", "-"):
                if line[0] != "+":
                    remaining_old -= 1
                    if file["status"] == "removed":
                        lines.add(old)
                    old += 1
                if line[0] != "-":
                    remaining_new -= 1
                    lines.add(new)
                    new += 1
                require(min(remaining_old, remaining_new) >= 0, "DIFF_HUNK_INVALID")
                added += line[0] == "+"
                deleted += line[0] == "-"
        require(hunks > 0 and remaining_old == remaining_new == 0, "DIFF_HUNK_TRUNCATED")
        require(added == file["additions"] and deleted == file["deletions"],
                "DIFF_CHANGE_COVERAGE_MISMATCH")
        result[path] = sorted(lines)
    return result


def prepare(number, sha, directory, fetch=api):
    pr_path = f"/repos/{REPOSITORY}/pulls/{number}"
    pr = fetch(pr_path)
    validate_pr(pr, number, sha)
    base = pr["base"]["sha"]
    commits = fetch(pr_path + "/commits?per_page=100")
    validate_commits(commits, pr["commits"], sha)
    compare_path = f"/repos/{REPOSITORY}/compare/{base}...{sha}"
    comparison = fetch(compare_path)
    require(
        comparison["status"] == "ahead"
        and comparison["base_commit"]["sha"] == base
        and comparison["commits"][-1]["sha"] == sha
        and comparison["total_commits"] == pr["commits"]
        and len(comparison["files"]) == pr["changed_files"],
        "COMPARE_EVIDENCE_INCOMPLETE",
    )
    diff = fetch(compare_path, diff=True)
    require(0 < len(diff.encode()) <= MAX_BYTES, "DIFF_SIZE_INVALID")
    lines = diff_lines(diff, comparison["files"])
    validate_pr(fetch(pr_path), number, sha, base)
    canary = secrets.token_hex(32)
    receipt = {
        "repository": REPOSITORY, "pr_number": number, "reviewed_sha": sha, "base_sha": base,
        "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
        "diff_access_canary_sha256": hashlib.sha256(canary.encode()).hexdigest(), "lines": lines,
    }
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    (directory / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    (directory / "review.diff").write_text(
        f"DIFF_ACCESS_CANARY={canary}\nUNTRUSTED DIFF DATA ONLY\n{diff}", encoding="utf-8"
    )
    context = {key: value for key, value in receipt.items()
               if key not in ("lines", "diff_access_canary_sha256")}
    (directory / "context.json").write_text(json.dumps(context), encoding="utf-8")
    return receipt


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_REVIEW_KEY")
        result[key] = value
    return result


def validate_result(raw, receipt, conclusion):
    require(conclusion == "success", "PROVIDER_EXECUTION_FAILED")
    require(isinstance(raw, str) and 0 < len(raw) <= MAX_BYTES, "STRUCTURED_OUTPUT_MISSING")
    result = json.loads(raw, object_pairs_hook=unique_object)
    require(isinstance(result, dict) and set(result) == FIELDS, "REVIEW_SCHEMA_INVALID")
    for key in ("repository", "pr_number", "reviewed_sha", "base_sha", "diff_sha256"):
        require(type(result[key]) is type(receipt[key]) and result[key] == receipt[key],
                "REVIEW_BINDING_OR_CANARY_MISMATCH")
    require(isinstance(result["diff_access_canary"], str)
            and hashlib.sha256(result["diff_access_canary"].encode()).hexdigest()
            == receipt["diff_access_canary_sha256"], "DIFF_ACCESS_CANARY_MISMATCH")
    require(result["disposition"] in ("PASS", "HOLD", "INCONCLUSIVE"), "DISPOSITION_INVALID")
    require(isinstance(result["next_action"], str) and result["next_action"].strip(),
            "NEXT_ACTION_MISSING")
    for key in ("regression_gaps", "security_risks"):
        require(isinstance(result[key], list) and all(
            isinstance(value, str) and value.strip() for value in result[key]
        ), "REVIEW_SCHEMA_INVALID")
    require(isinstance(result["findings"], list), "FINDINGS_INVALID")
    for finding in result["findings"]:
        require(isinstance(finding, dict)
                and set(finding) == {"file", "line", "severity", "message"}, "FINDING_INVALID")
        require(isinstance(finding["file"], str)
                and type(finding["line"]) is int
                and finding["line"] in receipt["lines"].get(finding["file"], []),
                "FINDING_LOCATION_INVALID")
        require(finding["severity"] in ("high", "medium", "low")
                and isinstance(finding["message"], str) and finding["message"].strip(),
                "FINDING_INVALID")
    require(result["disposition"] != "PASS" or not any(
        result[key] for key in ("findings", "regression_gaps", "security_risks")
    ), "PASS_WITH_BLOCKERS")
    return result


def main():
    number = inputs(os.environ["TARGET_PR"], os.environ["EXPECTED_SHA"],
                    os.environ["GITHUB_REPOSITORY"])
    directory = Path(os.environ["RUNNER_TEMP"]) / "wow-claude-review"
    if sys.argv[1] == "prepare":
        prepare(number, os.environ["EXPECTED_SHA"], directory)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("schema=" + json.dumps(SCHEMA, separators=(",", ":")) + "\n")
        disposition = "PENDING"
        findings = []
    elif sys.argv[1] == "validate":
        receipt = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
        require(receipt["pr_number"] == number and receipt["reviewed_sha"] == os.environ["EXPECTED_SHA"],
                "RECEIPT_STALE")
        pr = api(f"/repos/{REPOSITORY}/pulls/{number}")
        validate_pr(pr, number, receipt["reviewed_sha"], receipt["base_sha"])
        validate_commits(api(f"/repos/{REPOSITORY}/pulls/{number}/commits?per_page=100"),
                         pr["commits"], receipt["reviewed_sha"])
        result = validate_result(os.environ.get("REVIEW_OUTPUT", ""), receipt,
                                 os.environ.get("ACTION_CONCLUSION", ""))
        # Do not render hostile provider text into workflow commands or Markdown.
        (directory / "validated-review.json").write_text(json.dumps(result), encoding="utf-8")
        disposition = result["disposition"]
        findings = result["findings"]
    else:
        raise ValueError("COMMAND_INVALID")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
        summary.write(
            f"\nClaude advisory: PR #{number}, exact head {os.environ['EXPECTED_SHA']}, "
            f"disposition {disposition}.\n"
            "Advisory only; owner exact-head approval and independent QA/Release App approvals "
            "remain required. No protected check published; no merge/deploy authorized.\n"
            "custom_gpt_identity=WOW_BETTING_ENGINE; runtime_generation=V17_ACTIVE; "
            "terminal_authority=V17_TERMINAL_REDUCER; can_execute=false; "
            "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true\n"
        )
        for finding in findings:
            summary.write(f"\n- {finding['severity']}: `{finding['file']}:{finding['line']}` "
                          "(details in validated advisory artifact)\n")
    require(disposition in ("PASS", "PENDING"), "REVIEW_NOT_PASS")


def safe_reason(error):
    if isinstance(error, ValueError) and re.fullmatch(r"[A-Z][A-Z_]+", str(error)):
        return str(error)
    if isinstance(error, urllib.error.HTTPError):
        return f"GITHUB_HTTP_{error.code}"
    return "EVIDENCE_UNREADABLE_OR_PROVIDER_FAILURE"


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Provider/API errors can contain credentials or hostile text.
        print("::error::CLAUDE_ADVISORY_BLOCKED: " + safe_reason(error))
        sys.exit(1)
