"""Deterministic source review for WOW's vendor-independent senior reviewer.

Pure analysis. Source is untrusted data, never executed. Findings are hypotheses,
not verified defects or independent release receipts. Zero provider calls.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Mapping

from v17.native_senior_review import preflight_review_finding

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_KEY = re.compile(r"^[0-9a-f]{40}$")
_KNOWN_SEVERITIES = {"P0", "P1", "P2"}
_MAX_FINDINGS = 24


def added_lines(patch: str) -> set[int]:
    """New-side line numbers from verified unified diff; no execution."""
    if not isinstance(patch, str) or len(patch) > 90_000:
        raise ValueError("REVIEW_PATCH_INVALID")
    result: set[int] = set()
    current = None
    for line in patch.splitlines():
        match = _HUNK.match(line)
        if match:
            current = int(match.group(1))
        elif current is not None and line.startswith("+") and not line.startswith("+++"):
            result.add(current)
            current += 1
        elif current is not None and line.startswith(" "):
            current += 1
        elif line.startswith("\\ No newline at end of file") or line.startswith("-"):
            continue
        elif current is not None:
            raise ValueError("REVIEW_PATCH_FORMAT_INVALID")
    return result


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _literal_true(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _literal_false(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def _python_findings(path: str, src: str, lines_added: set[int]):
    try:
        tree = ast.parse(src, filename=path)
    except (SyntaxError, RecursionError, ValueError, TypeError):
        return []
    out = []
    code_lines = src.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or node.lineno not in lines_added:
            continue
        target = _call_name(node.func)
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        rule, severity, why = "", "", ""
        if target in {"eval", "exec", "builtins.eval", "builtins.exec"}:
            rule, severity, why = ("DYNAMIC_CODE_EXECUTION", "P1",
                                   "Evaluate whether untrusted input reaches dynamic code evaluation")
        elif target in {"os.system", "subprocess.getoutput", "subprocess.getstatusoutput"}:
            rule, severity, why = ("SHELL_EXECUTION", "P1",
                                   "Review command injection, argument provenance and privilege boundary")
        elif target.startswith("subprocess.") and _literal_true(kwargs.get("shell")):
            rule, severity, why = ("SUBPROCESS_SHELL_TRUE", "P1",
                                   "Shell invocation needs bounded trusted command and injection proof")
        elif target == "yaml.load" and "Loader" not in kwargs:
            rule, severity, why = ("UNSAFE_YAML_LOAD", "P1",
                                   "YAML loading without explicit safe loader may instantiate objects")
        elif target in {"pickle.loads", "pickle.load"}:
            rule, severity, why = ("PICKLE_DESERIALIZATION", "P1",
                                   "Prove deserialization input is trusted before allowing pickle")
        elif _literal_false(kwargs.get("verify")) and target.startswith(
                ("requests.", "httpx.", "session.")):
            rule, severity, why = ("TLS_VERIFICATION_DISABLED", "P1",
                                   "Turning off TLS certificate verification may enable interception")
        elif target in {"requests.get", "requests.post", "requests.put",
                        "requests.delete", "urllib.request.urlopen"} and "timeout" not in kwargs:
            rule, severity, why = ("UNBOUNDED_HTTP_TIMEOUT", "P2",
                                   "Network operations should have a bounded timeout and response limit")
        if not rule:
            continue
        excerpt = code_lines[node.lineno - 1].strip() if node.lineno <= len(code_lines) else ""
        if len(excerpt) < 12:
            excerpt = src[max(0, node.col_offset):max(0, node.col_offset) + 180]
        if not (12 <= len(excerpt) <= 500):
            continue
        out.append((rule, severity, node.lineno, why, excerpt))
    return out


def _workflow_findings(path: str, src: str, lines_added: set[int]):
    if not (path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml"))):
        return []
    lines = src.splitlines()
    if not any(re.match(r"^\s*pull_request_target\s*:", x) for x in lines):
        return []
    if not any("actions/checkout" in x for x in lines):
        return []
    out = []
    for i, line in enumerate(lines, 1):
        if (i in lines_added
                and "ref:" in line
                and ("github.event.pull_request.head" in line
                     or "github.head_ref" in line)):
            out.append(("UNTRUSTED_PR_CHECKOUT_IN_PRIVILEGED_WORKFLOW", "P0", i,
                        "Privileged pull_request_target job checking out untrusted PR code "
                        "requires isolation and exact credential/command-flow review",
                        line.strip()))
    return [x for x in out if 12 <= len(x[-1]) <= 500]


def run_static_review(packet: Mapping) -> dict:
    """Analyze evidence from a verified-context packet. No semantic model or approvals."""
    if not isinstance(packet, Mapping) or not isinstance(packet.get("head_sha"), str) \
            or not _KEY.fullmatch(packet["head_sha"]):
        raise ValueError("REVIEW_PACKET_INVALID")
    canonical = dict(packet)
    saved_hash = canonical.pop("packet_sha256", None)
    computed_hash = hashlib.sha256(json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")).hexdigest()
    if not isinstance(saved_hash, str) or saved_hash != computed_hash:
        raise ValueError("REVIEW_PACKET_DRIFT")
    if packet.get("status") != "ADVISORY_REVIEW_REQUIRED":
        return {
            "status": "REVIEW_CONTEXT_INCOMPLETE",
            "head_sha": packet["head_sha"],
            "packet_sha256": packet.get("packet_sha256"),
            "findings": [],
            "finding_count": 0,
            "model_review": "NOT_RUN",
            "independent_qa": "NOT_PERFORMED",
            "can_execute": False,
            "amrt_handoff": [],
        }
    findings = []
    dropped = 0
    for path in packet["changed_paths"]:
        src = packet["source_context"].get(path)
        patch = packet["patches"].get(path)
        if not isinstance(src, str) or not isinstance(patch, str):
            raise ValueError("REVIEW_CONTEXT_MISMATCH")
        changed = added_lines(patch)
        candidates = (_python_findings(path, src, changed) if path.endswith(".py") else
                      _workflow_findings(path, src, changed))
        for rule, severity, lineno, reason, excerpt in candidates:
            claim = {"head_sha": packet["head_sha"],
                     "file": path, "source_excerpt": excerpt}
            evidence_status = preflight_review_finding(dict(packet), claim)
            if evidence_status != "EVIDENCE_LOCATED_NOT_SEMANTICALLY_VERIFIED":
                raise ValueError("REVIEW_FINDING_EVIDENCE_INVALID:" + evidence_status)
            stable = hashlib.sha256(json.dumps(
                [path, rule, lineno, packet["head_sha"]],
                separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
            finding = {
                "finding_id": "WOWNR-" + stable,
                "severity": severity,
                "rule_id": rule,
                "file": path,
                "line": lineno,
                "reason": reason,
                "source_excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
                "evidence_status": evidence_status,
                "verification": "STATIC_PATTERN_UNCONFIRMED",
                "head_sha": packet["head_sha"],
                "can_execute": False,
            }
            if len(findings) >= _MAX_FINDINGS:
                dropped += 1
            else:
                findings.append(finding)
    findings.sort(key=lambda x: (x["file"], x["line"], x["rule_id"]))
    return {
        "schema": "WOW_NATIVE_SENIOR_STATIC_REVIEW_V1",
        "status": ("REVIEW_FINDINGS_TRUNCATED" if dropped else
                   "ADVISORY_FINDINGS_REQUIRE_TRIAGE" if findings else "ADVISORY_STATIC_SCAN_COMPLETE"),
        "head_sha": packet["head_sha"],
        "packet_sha256": packet["packet_sha256"],
        "changed_paths": list(packet["changed_paths"]),
        "findings": findings,
        "finding_count": len(findings),
        "unreported_findings": dropped,
        "model_review": "NOT_RUN",
        "independent_qa": "NOT_PERFORMED",
        "can_execute": False,
        "amrt_handoff": [
            {"existing_incident_id": packet["incident_id"],
             "pr_number": packet["pr_number"],
             "head_sha": packet["head_sha"],
             "finding_id": finding["finding_id"],
             "next_owner": "ROOT_CAUSE_INVESTIGATOR",
             "status": "ADVISORY_PENDING_REPRODUCTION",
             "may_mutate": False}
            for finding in findings
        ],
    }
