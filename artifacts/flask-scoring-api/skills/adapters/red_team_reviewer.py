"""
skills/adapters/red_team_reviewer.py
WOW Governed Red-Team Reviewer — skill adapter.

AUTHORITY BOUNDARIES (unconditional, non-negotiable):
  can_execute              = False
  PRODUCTION_AUTHORITY     = False
  USER_OUTPUT_AUTHORITY    = False
  TERMINAL_LABEL_AUTHORITY = False

This reviewer is advisory and downgrade-only. It is never a terminal-label,
production, deployment, capital, exception, or execution authority.

FINAL_AUTHORITY: CHATGPT_ONLY.
Replit must never self-approve based on this review.

DESIGN PRINCIPLES:
  1. Formulate falsification hypotheses BEFORE evaluating claims.
  2. Do not trust or repeat an implementer's persuasive explanation.
  3. Attempt to identify bypasses and fail-open paths.
  4. Generate adversarial test proposals.
  5. Fail closed on malformed or incomplete review packets.
  6. Never emit an aggregate confidence score (confidence=0.0 always).
"""
from __future__ import annotations

import re
from ..contracts import SkillLabel, SkillResult
from .base import BaseSkillAdapter
from .review_packet import (
    validate_packet_structure,
    validate_packet_hash,
    max_severity,
    severity_rank,
)

SKILL_ID      = "wow.governed-red-team-reviewer"
SKILL_VERSION = "1.0.0"

can_execute              = False
PRODUCTION_AUTHORITY     = False
USER_OUTPUT_AUTHORITY    = False
TERMINAL_LABEL_AUTHORITY = False

# ---------------------------------------------------------------------------
# Level-3 mandatory external review trigger configuration
# ---------------------------------------------------------------------------

_LEVEL3_CADENCE_THRESHOLD: int = 10   # every N APPROVED_CLOSED patches

def set_level3_cadence(n: int) -> None:
    """Configure the APPROVED_CLOSED patch cadence for Level-3 review (default 10)."""
    global _LEVEL3_CADENCE_THRESHOLD
    _LEVEL3_CADENCE_THRESHOLD = max(1, int(n))

# File-path substrings whose presence in diff_manifest triggers Level-3
_L3_PATH_TRIGGERS: dict[str, list[str]] = {
    "governance_authority_change": [
        "governance.py", "governance_resilience.py",
        "can_execute", "FINAL_AUTHORITY", "PRODUCTION_AUTHORITY",
        "USER_OUTPUT_AUTHORITY", "TERMINAL_LABEL_AUTHORITY",
    ],
    "authentication_security_change": [
        "auth", "session", "login", "token", "credential",
        "api_key", "password", "secret",
    ],
    "irreversible_migration": [
        "migration", "alembic", "drop_table", "alter_column", "schema",
    ],
    "production_execution_path": [
        "app.py", "gunicorn_conf.py", "artifact.toml",
    ],
    "probability_calibration_methodology_change": [
        "hit_probability.py", "model_registry.py",
        "prob_ledger", "calibrat", "binomial", "poisson",
    ],
    "capital_authority_change": [
        "bankroll", "stake_tier", "bet_size", "capital", "kelly",
    ],
    "reviewer_self_modification": [
        "red_team_reviewer.py", "review_packet.py",
        "red-team-reviewer/SKILL.md",
    ],
}

# ---------------------------------------------------------------------------
# Standard falsification hypotheses (formulated before evaluation)
# ---------------------------------------------------------------------------

_STANDARD_HYPOTHESES: list[str] = [
    "H1: The diff manifest may not reflect all files actually changed "
    "(coverage gap — untracked changes bypass the review).",
    "H2: Test expected values may mirror implementation constants "
    "(tautological tests that cannot falsify the implementation).",
    "H3: Acceptance criteria may lack at least one falsifying test per criterion "
    "(criteria are verified positively but never negated).",
    "H4: Negative / failure-path tests may be absent or trivial "
    "(only happy-path scenarios exercised).",
    "H5: Claimed remediation of prior blockers may not be substantiated by "
    "new packet evidence (resubmission with same artifacts).",
    "H6: Authority constants (can_execute, PRODUCTION_AUTHORITY) may have "
    "been modified or weakened in the diff.",
    "H7: Packet hash may not match current packet content "
    "(candidate was mutated after freeze).",
    "H8: Specification may contain ambiguous terms that different implementors "
    "interpret differently (spec defect masquerading as implementation choice).",
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _f(fid: str, dim_id: str, defect_class: str, severity: str,
        description: str, evidence: str = "(see packet)",
        hypothesis: str = "") -> dict:
    """Finding factory."""
    return {
        "finding_id": fid,
        "dim_id": dim_id,
        "defect_class": defect_class,
        "severity": severity,
        "description": description,
        "cited_evidence": evidence,
        "falsification_hypothesis": hypothesis,
    }


def _dim(dim_id: str, name: str, verdict: str, evidence: list[str],
         reasoning: str, findings: list[dict]) -> dict:
    """Dimension result factory."""
    return {
        "dim_id": dim_id,
        "name": name,
        "verdict": verdict,
        "cited_evidence": evidence,
        "reasoning": reasoning,
        "findings": findings,
    }


def _diff_files(packet: dict) -> list[str]:
    """Return list of file paths from diff_manifest."""
    return [
        e.get("file", "") for e in (packet.get("diff_manifest") or [])
        if isinstance(e, dict)
    ]


def _text_search(*fields: str, packet: dict) -> str:
    """Concatenate text from packet fields for substring searching."""
    parts = []
    for field in fields:
        v = packet.get(field, "")
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
        elif isinstance(v, str):
            parts.append(v)
    return " ".join(parts).lower()


def _artifact_text(packet: dict) -> str:
    """Concatenate raw_output from all test_artifacts."""
    out = []
    for a in (packet.get("test_artifacts") or []):
        if isinstance(a, dict):
            out.append(str(a.get("raw_output", "")))
    return " ".join(out).lower()


# ---------------------------------------------------------------------------
# 13 Review Dimensions
# ---------------------------------------------------------------------------

def _dim01_spec_quality(packet: dict) -> tuple[dict, list[dict]]:
    """DIM-01: Specification quality / ambiguity."""
    findings: list[dict] = []
    evidence: list[str] = []
    _VAGUE = frozenset({"works", "correct", "proper", "appropriate",
                        "reasonable", "etc.", "and more", "if needed"})

    spec_hash = packet.get("spec_hash", "")
    criteria  = packet.get("acceptance_criteria") or []

    if not spec_hash or len(str(spec_hash)) < 8:
        findings.append(_f(
            "F-01-001", "DIM-01", "specification_defect", "P1",
            "spec_hash is absent or trivial — specification version cannot be "
            "bound to this review attempt.",
            evidence=f"spec_hash={spec_hash!r}",
            hypothesis="H8: Spec may differ between the time the work was done and "
                       "this review.",
        ))
        evidence.append(f"spec_hash={spec_hash!r} (insufficient)")
    else:
        evidence.append(f"spec_hash={spec_hash!r} (present)")

    if not criteria:
        findings.append(_f(
            "F-01-002", "DIM-01", "specification_defect", "P1",
            "acceptance_criteria is empty — no verifiable criteria provided.",
            evidence="acceptance_criteria=[]",
            hypothesis="H3: With no criteria, every test is vacuously passing.",
        ))
        evidence.append("acceptance_criteria=[] (empty)")
    else:
        vague_found = []
        for i, c in enumerate(criteria):
            words = set(re.findall(r"\b\w+\b", str(c).lower()))
            overlap = words & _VAGUE
            if overlap:
                vague_found.append(f"criterion[{i}]={str(c)!r} (vague: {overlap})")
        if vague_found:
            findings.append(_f(
                "F-01-003", "DIM-01", "specification_defect", "P2",
                f"Acceptance criteria contain vague language: {vague_found[:3]}",
                evidence="; ".join(vague_found[:3]),
                hypothesis="H8: Vague criteria allow non-conforming implementations "
                           "to pass silently.",
            ))
            evidence.append(f"Vague criteria detected: {len(vague_found)}")
        else:
            evidence.append(f"{len(criteria)} criteria (no vague language detected)")

    verdict = "PASS" if not findings else (
        "FAIL" if any(f["severity"] == "P1" for f in findings) else "PARTIAL"
    )
    return _dim("DIM-01", "Specification Quality / Ambiguity",
                verdict, evidence,
                "Evaluated spec_hash binding and acceptance criteria specificity.",
                findings), findings


def _dim02_spec_match(packet: dict) -> tuple[dict, list[dict]]:
    """DIM-02: Specification match."""
    findings: list[dict] = []
    evidence: list[str] = []

    criteria   = packet.get("acceptance_criteria") or []
    diff_files = _diff_files(packet)

    evidence.append(f"diff_manifest: {len(diff_files)} file(s)")
    evidence.append(f"acceptance_criteria: {len(criteria)} criterion/criteria")

    if criteria and not diff_files:
        findings.append(_f(
            "F-02-001", "DIM-02", "evidence_defect", "P1",
            "Acceptance criteria exist but diff_manifest is empty — no code "
            "changes are evidenced against the stated criteria.",
            evidence="diff_manifest=[] with non-empty acceptance_criteria",
            hypothesis="H1: Changes may have been made outside the tracked diff.",
        ))
    elif len(criteria) > 0 and len(diff_files) < len(criteria) // 3:
        # Heuristic: fewer than 1 diff file per 3 criteria is suspicious
        findings.append(_f(
            "F-02-002", "DIM-02", "evidence_defect", "P2",
            f"Suspicious coverage ratio: {len(criteria)} criteria with only "
            f"{len(diff_files)} diff file(s). Some criteria may have no "
            f"corresponding implementation.",
            evidence=f"criteria={len(criteria)}, diff_files={len(diff_files)}",
            hypothesis="H1: Not all claimed changes are reflected in the manifest.",
        ))

    verdict = "PASS" if not findings else (
        "FAIL" if any(f["severity"] == "P1" for f in findings) else "PARTIAL"
    )
    return _dim("DIM-02", "Specification Match",
                verdict, evidence,
                "Evaluated whether diff_manifest covers stated acceptance criteria.",
                findings), findings


def _dim03_scope_integrity(packet: dict) -> tuple[dict, list[dict]]:
    """DIM-03: Scope integrity."""
    findings: list[dict] = []
    evidence: list[str] = []
    diff_files = _diff_files(packet)

    _SENSITIVE_INFRA = [
        "artifact.toml", "gunicorn_conf.py", ".replit",
        "requirements.txt", "pyproject.toml",
    ]
    touched_infra = [f for f in diff_files
                     if any(s in f for s in _SENSITIVE_INFRA)]
    if touched_infra:
        findings.append(_f(
            "F-03-001", "DIM-03", "implementation_defect", "P2",
            f"Diff touches infrastructure configuration files: {touched_infra}. "
            f"Verify these changes are intentional and within stated scope.",
            evidence=f"diff_manifest contains: {touched_infra}",
            hypothesis="H1: Infrastructure changes may have unintended side effects "
                       "on production or other artifacts.",
        ))
        evidence.append(f"Infrastructure files touched: {touched_infra}")
    else:
        evidence.append("No infrastructure configuration files in diff.")

    evidence.append(f"Total diff files: {len(diff_files)}")
    verdict = "PASS" if not findings else "PARTIAL"
    return _dim("DIM-03", "Scope Integrity",
                verdict, evidence,
                "Checked diff_manifest for unexpected infrastructure touches.",
                findings), findings


def _dim04_authority_integrity(packet: dict) -> tuple[dict, list[dict]]:
    """DIM-04: Authority integrity / self-approval detection."""
    findings: list[dict] = []
    evidence: list[str] = []
    diff_files = _diff_files(packet)

    # Check for self-modification (reviewer's own files in diff)
    self_files = [f for f in diff_files
                  if "red_team_reviewer" in f or "review_packet" in f
                  or "red-team-reviewer" in f]
    if self_files:
        findings.append(_f(
            "F-04-001", "DIM-04", "governance_defect", "P0",
            f"Diff modifies this reviewer's own files: {self_files}. "
            f"Self-modification requires mandatory Level-3 external review.",
            evidence=f"diff_manifest contains reviewer files: {self_files}",
            hypothesis="H6: Self-modification could weaken or bypass the reviewer's "
                       "own authority guards.",
        ))
        evidence.append(f"SELF-MODIFICATION detected: {self_files}")

    # Check for authority bypass patterns in any text
    all_text = _text_search("acceptance_criteria", packet=packet) + \
               " " + _artifact_text(packet)
    _BYPASS_PATTERNS = [
        "can_execute=true", "can_execute = true",
        "production_authority=true", "production_authority = true",
        "user_output_authority=true", "user_output_authority = true",
        "terminal_label_authority=true", "terminal_label_authority = true",
        "self.can_execute = true",
    ]
    hits = [p for p in _BYPASS_PATTERNS if p in all_text]
    if hits:
        findings.append(_f(
            "F-04-002", "DIM-04", "governance_defect", "P0",
            f"Authority bypass pattern detected in packet text: {hits}. "
            f"can_execute and authority flags must always be False.",
            evidence=f"Patterns found: {hits}",
            hypothesis="H6: Authority constants may have been modified.",
        ))
        evidence.append(f"AUTHORITY BYPASS patterns found: {hits}")
    else:
        evidence.append("No authority bypass patterns detected in packet text.")

    # Check for self-approval language in work_item_id or prior_review_history
    prh_text = str(packet.get("prior_review_history", "")).lower()
    wid_text  = str(packet.get("work_item_id", "")).lower()
    _SELF_APPROVAL = ["self-approved", "self_approved", "replit approved",
                      "auto-approved", "approved by replit"]
    sa_hits = [p for p in _SELF_APPROVAL
               if p in prh_text or p in wid_text]
    if sa_hits:
        findings.append(_f(
            "F-04-003", "DIM-04", "governance_defect", "P0",
            f"Self-approval language detected: {sa_hits}. "
            f"FINAL_AUTHORITY is CHATGPT_ONLY. Replit must not self-approve.",
            evidence=f"Self-approval patterns: {sa_hits}",
            hypothesis="H6: Self-approval claim violates FINAL_AUTHORITY=CHATGPT_ONLY.",
        ))
        evidence.append(f"SELF-APPROVAL language detected: {sa_hits}")
    else:
        evidence.append("No self-approval language detected.")

    if not findings:
        evidence.append("Authority invariants appear intact in packet.")
    verdict = "PASS" if not findings else "FAIL"
    return _dim("DIM-04", "Authority Integrity / Self-Approval Detection",
                verdict, evidence,
                "Checked for authority bypass, self-modification, and self-approval.",
                findings), findings


def _dim05_governance_integrity(packet: dict) -> tuple[dict, list[dict]]:
    """DIM-05: Governance integrity."""
    findings: list[dict] = []
    evidence: list[str] = []

    gov_hash = packet.get("runtime_governance_hash", "")
    if not gov_hash or len(str(gov_hash)) < 16:
        findings.append(_f(
            "F-05-001", "DIM-05", "governance_defect", "P1",
            "runtime_governance_hash is absent or trivially short — governance "
            "state at review time cannot be verified.",
            evidence=f"runtime_governance_hash={gov_hash!r}",
            hypothesis="H7: If governance hash is unrecorded, the review cannot be "
                       "bound to a known engine state.",
        ))
        evidence.append(f"runtime_governance_hash={gov_hash!r} (insufficient)")
    else:
        evidence.append(f"runtime_governance_hash present ({len(str(gov_hash))} chars)")

    # Check diff for governance constant modifications
    diff_files = _diff_files(packet)
    gov_files  = [f for f in diff_files if "governance" in f]
    if gov_files:
        findings.append(_f(
            "F-05-002", "DIM-05", "governance_defect", "P1",
            f"Diff touches governance files: {gov_files}. Governance changes "
            f"require mandatory Level-3 external review.",
            evidence=f"governance files in diff: {gov_files}",
            hypothesis="H6: Governance file changes may weaken engine constraints.",
        ))
        evidence.append(f"Governance files in diff: {gov_files}")
    else:
        evidence.append("No governance files touched in diff.")

    verdict = "PASS" if not findings else (
        "FAIL" if any(f["severity"] in ("P0", "P1") for f in findings) else "PARTIAL"
    )
    return _dim("DIM-05", "Governance Integrity",
                verdict, evidence,
                "Verified runtime_governance_hash presence and governance file touches.",
                findings), findings


def _dim06_packet_integrity(packet: dict,
                            hash_valid: bool, hash_detail: str,
                            struct_valid: bool) -> tuple[dict, list[dict]]:
    """DIM-06: Packet integrity / drift."""
    findings: list[dict] = []
    evidence: list[str] = []

    if not hash_valid:
        findings.append(_f(
            "F-06-001", "DIM-06", "evidence_defect", "P0",
            f"Packet hash validation failed: {hash_detail}. The review packet "
            f"has drifted from its frozen state.",
            evidence=hash_detail,
            hypothesis="H7: Candidate was mutated after the packet was assembled.",
        ))
        evidence.append(f"PACKET DRIFT: {hash_detail}")
    else:
        evidence.append("packet_hash verified — no drift detected.")

    if not packet.get("candidate_commit_sha", "").strip():
        findings.append(_f(
            "F-06-002", "DIM-06", "evidence_defect", "P0",
            "candidate_commit_sha is absent or empty — cannot identify "
            "what was reviewed.",
            evidence="candidate_commit_sha=''",
            hypothesis="H1: Without a commit SHA, the diff cannot be reproduced.",
        ))
        evidence.append("candidate_commit_sha missing")

    # Check diff_manifest entries have sha256
    dm = packet.get("diff_manifest") or []
    no_hash = [e.get("file", f"[{i}]") for i, e in enumerate(dm)
               if isinstance(e, dict) and not e.get("sha256")]
    if no_hash:
        findings.append(_f(
            "F-06-003", "DIM-06", "evidence_defect", "P1",
            f"diff_manifest entries lack sha256 field: {no_hash[:5]}. File "
            f"integrity cannot be verified.",
            evidence=f"Files without sha256: {no_hash[:5]}",
            hypothesis="H1: Files without hashes can be substituted without detection.",
        ))
        evidence.append(f"{len(no_hash)} diff entry/entries lack sha256")

    verdict = "PASS" if not findings else "FAIL"
    return _dim("DIM-06", "Packet Integrity / Drift",
                verdict, evidence,
                "Verified packet_hash, candidate_commit_sha, and diff entry hashes.",
                findings), findings


def _dim07_evidence_integrity(packet: dict) -> tuple[dict, list[dict]]:
    """DIM-07: Evidence integrity / provenance."""
    findings: list[dict] = []
    evidence: list[str] = []

    artifacts  = packet.get("test_artifacts") or []
    test_counts = packet.get("test_counts") or {}
    commands   = packet.get("test_commands") or []

    if not artifacts:
        findings.append(_f(
            "F-07-001", "DIM-07", "evidence_defect", "P1",
            "test_artifacts is empty — no raw test output is evidenced. "
            "Test results cannot be independently verified.",
            evidence="test_artifacts=[]",
            hypothesis="H5: Claimed test results may not reflect actual execution.",
        ))
        evidence.append("test_artifacts=[] (MISSING)")
    else:
        no_hash = [a.get("artifact_id", f"[{i}]") for i, a in enumerate(artifacts)
                   if isinstance(a, dict) and not a.get("content_hash")]
        if no_hash:
            findings.append(_f(
                "F-07-002", "DIM-07", "evidence_defect", "P2",
                f"test_artifacts lack content_hash: {no_hash[:3]}. "
                f"Artifact substitution cannot be detected.",
                evidence=f"Artifacts without content_hash: {no_hash[:3]}",
                hypothesis="H5: Unhashed artifacts may be replaced between reviews.",
            ))
        evidence.append(f"{len(artifacts)} test artifact(s), "
                        f"{len(no_hash) if no_hash else 0} missing content_hash")

    failed = test_counts.get("failed", 0)
    passed = test_counts.get("passed")
    if isinstance(failed, int) and failed > 0:
        findings.append(_f(
            "F-07-003", "DIM-07", "evidence_defect", "P1",
            f"test_counts reports {failed} failing test(s) with no documented "
            f"explanation in the packet.",
            evidence=f"test_counts.failed={failed}",
            hypothesis="H4: Failing tests may indicate unresolved defects.",
        ))
        evidence.append(f"test_counts: passed={passed}, failed={failed} (FAILING)")
    else:
        evidence.append(f"test_counts: passed={passed}, failed={failed}")

    # Vague command detection
    vague_cmds = [c for c in commands
                  if isinstance(c, str) and c.strip().lower() in
                  ("run tests", "test", "pytest", "python -m pytest")]
    if vague_cmds:
        findings.append(_f(
            "F-07-004", "DIM-07", "evidence_defect", "P2",
            f"test_commands are insufficiently specific: {vague_cmds}. "
            f"An independent reviewer cannot reproduce the exact test run.",
            evidence=f"Vague commands: {vague_cmds}",
            hypothesis="H5: Vague commands may not reproduce the evidenced test run.",
        ))
        evidence.append(f"Vague test commands detected: {vague_cmds}")

    verdict = "PASS" if not findings else (
        "FAIL" if any(f["severity"] in ("P0", "P1") for f in findings) else "PARTIAL"
    )
    return _dim("DIM-07", "Evidence Integrity / Provenance",
                verdict, evidence,
                "Verified test_artifacts, test_counts, and command specificity.",
                findings), findings


def _dim08_test_quality(packet: dict) -> tuple[dict, list[dict]]:
    """DIM-08: Test quality / test independence."""
    findings: list[dict] = []
    evidence: list[str] = []

    artifact_text = _artifact_text(packet)
    cmd_text      = " ".join(str(c) for c in (packet.get("test_commands") or [])).lower()
    neg_cases     = packet.get("tested_negative_cases") or []
    edge_cases    = packet.get("tested_edge_cases") or []

    # Snapshot / bless pattern detection
    _SNAPSHOT_SIGNALS = ["snapshot", "bless", "update_golden", "--update-snapshots",
                         "blessed output", "golden file"]
    snap_hits = [s for s in _SNAPSHOT_SIGNALS
                 if s in artifact_text or s in cmd_text]
    if snap_hits:
        findings.append(_f(
            "F-08-001", "DIM-08", "evidence_defect", "P2",
            f"Snapshot/golden-file test pattern detected: {snap_hits}. "
            f"Tests that merely bless current output cannot falsify the implementation.",
            evidence=f"Snapshot signals in artifact/commands: {snap_hits}",
            hypothesis="H2: Snapshot tests are tautological — they pass by definition "
                       "once the snapshot is written.",
        ))
        evidence.append(f"Snapshot pattern detected: {snap_hits}")
    else:
        evidence.append("No snapshot/golden-file patterns detected.")

    # Mirror test detection (expected values copied from implementation)
    _MIRROR_SIGNALS = ["# from implementation", "# matches source",
                       "assert result == implementation_constant",
                       "copied from source", "mirror of source"]
    mirror_hits = [s for s in _MIRROR_SIGNALS if s in artifact_text]
    if mirror_hits:
        findings.append(_f(
            "F-08-002", "DIM-08", "evidence_defect", "P2",
            f"Mirror test pattern detected: {mirror_hits}. Test expected values "
            f"appear to be derived from the implementation rather than the spec.",
            evidence=f"Mirror signals: {mirror_hits}",
            hypothesis="H2: Mirror tests cannot detect when the implementation "
                       "deviates from the specification.",
        ))
        evidence.append(f"Mirror test pattern: {mirror_hits}")
    else:
        evidence.append("No mirror-test patterns detected.")

    # Negative test coverage
    if not neg_cases:
        findings.append(_f(
            "F-08-003", "DIM-08", "evidence_defect", "P1",
            "tested_negative_cases is empty — failure-path and adversarial "
            "inputs are not evidenced.",
            evidence="tested_negative_cases=[]",
            hypothesis="H4: Without negative tests, fail-open paths may pass silently.",
        ))
        evidence.append("tested_negative_cases=[] (MISSING)")
    else:
        evidence.append(f"{len(neg_cases)} negative case(s) documented.")

    # Edge case coverage
    if not edge_cases:
        findings.append(_f(
            "F-08-004", "DIM-08", "evidence_defect", "P2",
            "tested_edge_cases is empty — boundary conditions are not evidenced.",
            evidence="tested_edge_cases=[]",
            hypothesis="H3: Untested edge cases may expose boundary violations.",
        ))
        evidence.append("tested_edge_cases=[] (none documented)")
    else:
        evidence.append(f"{len(edge_cases)} edge case(s) documented.")

    # Mutation reasoning (lightweight P3 flag)
    criteria = packet.get("acceptance_criteria") or []
    if criteria and not neg_cases:
        findings.append(_f(
            "F-08-005", "DIM-08", "evidence_defect", "P3",
            f"Mutation reasoning gap: {len(criteria)} acceptance criteria but no "
            f"negative cases. Consider: would flipping each criterion's primary "
            f"condition leave tests green?",
            evidence=f"criteria={len(criteria)}, negative_cases=0",
            hypothesis="H3: Criteria without negation tests cannot be falsified.",
        ))

    verdict = "PASS" if not findings else (
        "FAIL" if any(f["severity"] in ("P0", "P1") for f in findings) else "PARTIAL"
    )
    return _dim("DIM-08", "Test Quality / Test Independence",
                verdict, evidence,
                "Checked for snapshot tests, mirror tests, and negative coverage.",
                findings), findings


def _dim09_reproducibility(packet: dict) -> tuple[dict, list[dict]]:
    """DIM-09: Reproducibility."""
    findings: list[dict] = []
    evidence: list[str] = []

    commands  = packet.get("test_commands") or []
    gov_hash  = packet.get("runtime_governance_hash", "")
    ts        = packet.get("packet_creation_timestamp", "")
    base_sha  = packet.get("base_commit_sha", "")
    cand_sha  = packet.get("candidate_commit_sha", "")

    if not commands:
        findings.append(_f(
            "F-09-001", "DIM-09", "evidence_defect", "P1",
            "test_commands is empty — the test run cannot be reproduced.",
            evidence="test_commands=[]",
            hypothesis="H5: Without exact commands, reproduction is impossible.",
        ))
        evidence.append("test_commands=[] (cannot reproduce)")
    else:
        evidence.append(f"{len(commands)} test command(s) provided.")

    if not base_sha.strip() or not cand_sha.strip():
        findings.append(_f(
            "F-09-002", "DIM-09", "evidence_defect", "P1",
            "base_commit_sha or candidate_commit_sha missing — cannot reproduce "
            "the exact diff under review.",
            evidence=f"base={base_sha!r}, candidate={cand_sha!r}",
            hypothesis="H1: Missing SHAs make the diff unreproducible.",
        ))
        evidence.append("Commit SHAs incomplete")
    else:
        evidence.append(f"base_sha={base_sha[:12]}… candidate_sha={cand_sha[:12]}…")

    if not gov_hash:
        findings.append(_f(
            "F-09-003", "DIM-09", "governance_defect", "P2",
            "runtime_governance_hash missing — test run cannot be pinned to "
            "a known engine state.",
            evidence="runtime_governance_hash absent",
            hypothesis="H7: Without the governance hash, the engine state at test "
                       "time is unknown.",
        ))
        evidence.append("runtime_governance_hash absent")

    if not ts.strip():
        findings.append(_f(
            "F-09-004", "DIM-09", "evidence_defect", "P3",
            "packet_creation_timestamp missing — cannot determine when the "
            "review packet was assembled.",
            evidence="packet_creation_timestamp absent",
            hypothesis="H7: Undated packets may be reused across different engine states.",
        ))

    verdict = "PASS" if not findings else (
        "FAIL" if any(f["severity"] in ("P0", "P1") for f in findings) else "PARTIAL"
    )
    return _dim("DIM-09", "Reproducibility",
                verdict, evidence,
                "Verified commit SHAs, test commands, governance hash, and timestamp.",
                findings), findings


def _dim10_failure_path(packet: dict) -> tuple[dict, list[dict]]:
    """DIM-10: Failure-path review."""
    findings: list[dict] = []
    evidence: list[str] = []

    neg_cases = packet.get("tested_negative_cases") or []

    if not neg_cases:
        findings.append(_f(
            "F-10-001", "DIM-10", "evidence_defect", "P1",
            "No failure-path tests documented. The implementation's behavior "
            "under error conditions, malformed input, and resource unavailability "
            "is not evidenced.",
            evidence="tested_negative_cases=[]",
            hypothesis="H4: Untested failure paths may be fail-open.",
        ))
        evidence.append("tested_negative_cases=[] — no failure paths evidenced")
    else:
        _FAILURE_KEYWORDS = frozenset(
            {"error", "fail", "missing", "invalid", "empty", "reject",
             "malform", "unauthori", "unavailabl", "timeout", "exception"}
        )
        meaningful = [c for c in neg_cases
                      if any(kw in str(c).lower() for kw in _FAILURE_KEYWORDS)]
        if len(meaningful) < len(neg_cases) // 2 and len(neg_cases) > 2:
            findings.append(_f(
                "F-10-002", "DIM-10", "evidence_defect", "P2",
                f"Fewer than half the negative cases ({len(meaningful)}/{len(neg_cases)}) "
                f"describe genuine failure modes. Some may be restated happy-path tests.",
                evidence=f"Meaningful failure cases: {len(meaningful)}/{len(neg_cases)}",
                hypothesis="H4: Nominal-positive cases relabeled as 'negative' "
                           "do not exercise failure paths.",
            ))
        evidence.append(f"{len(neg_cases)} negative case(s): {len(meaningful)} "
                        f"appear to cover genuine failure modes.")

    verdict = "PASS" if not findings else (
        "FAIL" if any(f["severity"] in ("P0", "P1") for f in findings) else "PARTIAL"
    )
    return _dim("DIM-10", "Failure-Path Review",
                verdict, evidence,
                "Checked tested_negative_cases for genuine failure-mode coverage.",
                findings), findings


def _dim11_regression_risk(packet: dict) -> tuple[dict, list[dict]]:
    """DIM-11: Backward compatibility / regression risk."""
    findings: list[dict] = []
    evidence: list[str] = []
    diff_files = _diff_files(packet)

    _SHARED_IFACES = [
        "contracts.py", "orchestrator.py", "pipeline.py",
        "governance.py", "registry.py", "__init__.py",
        "base.py", "app.py",
    ]
    touched_shared = [f for f in diff_files
                      if any(s in f for s in _SHARED_IFACES)]
    edge_cases = packet.get("tested_edge_cases") or []
    commands   = " ".join(str(c) for c in (packet.get("test_commands") or [])).lower()

    has_regression_evidence = (
        any("regression" in str(c).lower() for c in edge_cases) or
        "regression" in commands or
        any("regression" in str(a.get("raw_output", "")).lower()
            for a in (packet.get("test_artifacts") or []))
    )

    if touched_shared and not has_regression_evidence:
        findings.append(_f(
            "F-11-001", "DIM-11", "implementation_defect", "P1",
            f"Diff touches shared interface files {touched_shared[:4]} but no "
            f"regression test evidence is present in the packet.",
            evidence=f"Shared files: {touched_shared[:4]}; regression evidence: None",
            hypothesis="H1: Interface changes without regression coverage may silently "
                       "break callers.",
        ))
        evidence.append(f"Shared interface touched: {touched_shared[:4]}; "
                        f"no regression evidence found.")
    elif touched_shared:
        findings.append(_f(
            "F-11-002", "DIM-11", "implementation_defect", "P2",
            f"Diff touches shared interface files {touched_shared[:4]}. "
            f"Regression evidence present — flag for downstream monitoring.",
            evidence=f"Shared files: {touched_shared[:4]}; regression evidence found.",
            hypothesis="H1: Shared interface changes carry inherent compatibility risk "
                       "even with regression tests.",
        ))
        evidence.append(f"Shared interface touched with regression evidence: "
                        f"{touched_shared[:4]}")
    else:
        evidence.append("No shared interface files in diff.")

    verdict = "PASS" if not findings else (
        "FAIL" if any(f["severity"] == "P1" for f in findings) else "PARTIAL"
    )
    return _dim("DIM-11", "Backward Compatibility / Regression Risk",
                verdict, evidence,
                "Checked for shared interface touches and regression coverage.",
                findings), findings


def _dim12_resubmission_pattern(packet: dict) -> tuple[dict, list[dict]]:
    """DIM-12: Resubmission-pattern detection."""
    findings: list[dict] = []
    evidence: list[str] = []

    attempt       = packet.get("review_attempt", 1)
    prior_history = packet.get("prior_review_history") or []
    prior_blockers = packet.get("prior_blockers") or []

    evidence.append(f"review_attempt={attempt}")

    if attempt <= 1:
        evidence.append("First attempt — no resubmission checks required.")
        verdict = "PASS"
        return _dim("DIM-12", "Resubmission-Pattern Detection",
                    verdict, evidence,
                    "First review attempt; no resubmission patterns to evaluate.",
                    findings), findings

    # Resubmission checks
    if not prior_history:
        findings.append(_f(
            "F-12-001", "DIM-12", "evidence_defect", "P1",
            f"review_attempt={attempt} but prior_review_history is empty. "
            f"Cannot verify that prior findings were reviewed.",
            evidence="prior_review_history=[]",
            hypothesis="H5: Prior review results may have been stripped, preventing "
                       "remediation verification.",
        ))
        evidence.append("prior_review_history=[] despite resubmission")

    if attempt > 1 and not prior_blockers:
        findings.append(_f(
            "F-12-002", "DIM-12", "evidence_defect", "P1",
            f"review_attempt={attempt} but prior_blockers is empty. "
            f"Prior review blockers must be individually tracked on resubmission.",
            evidence="prior_blockers=[]",
            hypothesis="H5: Blockers may have been omitted to avoid remediation "
                       "scrutiny.",
        ))
        evidence.append("prior_blockers=[] despite resubmission")

    # Detect same artifacts resubmitted without change
    if prior_history and isinstance(prior_history, list):
        prior_hashes = set()
        for prev in prior_history:
            if isinstance(prev, dict):
                for a in (prev.get("test_artifacts") or []):
                    h = (a.get("content_hash") or "").strip()
                    if h:
                        prior_hashes.add(h)
        current_hashes = {
            (a.get("content_hash") or "").strip()
            for a in (packet.get("test_artifacts") or [])
            if isinstance(a, dict)
        }
        repeated = prior_hashes & current_hashes - {""}
        if repeated:
            findings.append(_f(
                "F-12-003", "DIM-12", "evidence_defect", "P1",
                f"Identical test artifact hash(es) from prior review resubmitted "
                f"without change: {len(repeated)} artifact(s). "
                f"New test runs are required on resubmission.",
                evidence=f"{len(repeated)} repeated content_hash(es)",
                hypothesis="H5: Reusing prior test artifacts without new execution "
                           "does not demonstrate that defects were fixed.",
            ))
            evidence.append(f"{len(repeated)} artifact(s) unchanged from prior review")

    verdict = "PASS" if not findings else (
        "FAIL" if any(f["severity"] in ("P0", "P1") for f in findings) else "PARTIAL"
    )
    return _dim("DIM-12", "Resubmission-Pattern Detection",
                verdict, evidence,
                "Verified prior history, prior blockers, and artifact novelty.",
                findings), findings


def _dim13_blocker_remediation(packet: dict) -> tuple[dict, list[dict]]:
    """DIM-13: Prior-blocker remediation tracking."""
    findings: list[dict] = []
    evidence: list[str] = []

    attempt        = packet.get("review_attempt", 1)
    prior_blockers = packet.get("prior_blockers") or []
    diff_files     = _diff_files(packet)
    artifact_text  = _artifact_text(packet)

    if attempt == 1 and not prior_blockers:
        evidence.append("First review attempt with no prior blockers — PASS.")
        return _dim("DIM-13", "Prior-Blocker Remediation Tracking",
                    "PASS", evidence,
                    "No prior blockers to evaluate on first attempt.",
                    findings), findings

    for i, blocker in enumerate(prior_blockers):
        if not isinstance(blocker, dict):
            continue
        bid    = blocker.get("blocker_id", f"[{i}]")
        status = blocker.get("status", "")
        sev    = blocker.get("severity", "P2")
        desc   = str(blocker.get("description", ""))[:80]

        if status == "REGRESSED":
            findings.append(_f(
                f"F-13-{i+1:03d}", "DIM-13", "implementation_defect", "P0",
                f"Prior blocker {bid!r} has REGRESSED — was previously resolved "
                f"but is now failing again: {desc}",
                evidence=f"blocker[{i}].status=REGRESSED, severity={sev}",
                hypothesis="H5: Regression indicates the prior fix was incomplete or "
                           "a new change re-introduced the problem.",
            ))
            evidence.append(f"REGRESSED: {bid!r}")

        elif status == "STILL_PRESENT":
            # If original severity was P0 or P1 → now P0 (hard block)
            new_sev = "P0" if sev in ("P0", "P1") else "P2"
            findings.append(_f(
                f"F-13-{i+1:03d}", "DIM-13", "implementation_defect", new_sev,
                f"Prior blocker {bid!r} is STILL_PRESENT: {desc}. "
                f"Resubmission without resolving this blocker is not permitted.",
                evidence=f"blocker[{i}].status=STILL_PRESENT, original_severity={sev}",
                hypothesis="H5: Blocker was not addressed before resubmission.",
            ))
            evidence.append(f"STILL_PRESENT: {bid!r} (original severity={sev})")

        elif status == "NOT_EVIDENCED":
            findings.append(_f(
                f"F-13-{i+1:03d}", "DIM-13", "evidence_defect", "P1",
                f"Prior blocker {bid!r} is marked NOT_EVIDENCED: {desc}. "
                f"Remediation was claimed but no packet evidence supports it.",
                evidence=f"blocker[{i}].status=NOT_EVIDENCED",
                hypothesis="H5: Claimed remediation without evidence may mean the "
                           "blocker was not actually addressed.",
            ))
            evidence.append(f"NOT_EVIDENCED: {bid!r}")

        elif status == "RESOLVED":
            # Check there's some evidence the resolution is in the packet
            remediation = str(blocker.get("remediation_claim", "")).lower()
            has_evidence = (
                any(remediation[:20] in f for f in diff_files if remediation) or
                (remediation and remediation[:20] in artifact_text)
            )
            if has_evidence:
                evidence.append(f"RESOLVED with evidence: {bid!r}")
            else:
                # Weak evidence but not a hard block — P3 advisory
                findings.append(_f(
                    f"F-13-{i+1:03d}", "DIM-13", "evidence_defect", "P3",
                    f"Prior blocker {bid!r} marked RESOLVED but the remediation "
                    f"claim cannot be cross-referenced to packet evidence.",
                    evidence=f"remediation_claim={remediation[:60]!r}",
                    hypothesis="H5: Resolution claim should be traceable to a diff "
                               "entry or test artifact.",
                ))
                evidence.append(f"RESOLVED (evidence not cross-referenced): {bid!r}")
        else:
            evidence.append(f"Unknown status {status!r} for blocker {bid!r}")

    verdict = "PASS" if not findings else (
        "FAIL" if any(f["severity"] in ("P0", "P1") for f in findings) else "PARTIAL"
    )
    return _dim("DIM-13", "Prior-Blocker Remediation Tracking",
                verdict, evidence,
                "Evaluated status of each prior blocker (RESOLVED/STILL_PRESENT/"
                "REGRESSED/NOT_EVIDENCED).",
                findings), findings


# ---------------------------------------------------------------------------
# Level-3 trigger detection
# ---------------------------------------------------------------------------

def _classify_level3_triggers(packet: dict, all_findings: list[dict]) -> tuple[bool, list[str]]:
    """
    Determine whether this review requires mandatory Level-3 external review.

    Returns (level3_required: bool, reasons: list[str]).
    """
    reasons: list[str] = []
    diff_files = _diff_files(packet)
    all_diff_text = " ".join(diff_files).lower()

    for trigger_name, path_patterns in _L3_PATH_TRIGGERS.items():
        if any(p.lower() in all_diff_text for p in path_patterns):
            reasons.append(trigger_name)

    # Unresolved P0 findings (hard block — always escalates to Level 3).
    # P1 findings are Level 2 territory; only P0 forces external red-team review.
    if any(f.get("severity") == "P0" for f in all_findings):
        reasons.append("unresolved_p0_findings")

    # Cadence check
    approved_count = packet.get("approved_closed_patch_count", 0) or 0
    if (isinstance(approved_count, int) and approved_count > 0
            and approved_count % _LEVEL3_CADENCE_THRESHOLD == 0):
        reasons.append(
            f"cadence_threshold_reached:"
            f"approved_closed={approved_count},"
            f"threshold={_LEVEL3_CADENCE_THRESHOLD}"
        )

    return bool(reasons), reasons


# ---------------------------------------------------------------------------
# Level 1 / 2 / 3 risk routing
# ---------------------------------------------------------------------------

def _classify_risk_level(
    packet: dict, all_findings: list[dict]
) -> tuple[int, str, list[str]]:
    """
    Classify the review into one of three risk levels for routing.

    Level 1 – LEVEL_1_ROUTINE:
        No significant risk triggers. Standard advisory review.

    Level 2 – LEVEL_2_GOVERNANCE_MODEL_IMPACT:
        Changes to model methodology, gate engine core, shared skill interfaces,
        or presence of P1 findings. Elevated review scrutiny warranted.

    Level 3 – LEVEL_3_CRITICAL_EXTERNAL_RED_TEAM:
        Mandatory external review. Triggers: governance/authority-boundary changes,
        auth/security, irreversible migrations, production execution paths, major
        probability/calibration methodology changes, capital-authority changes,
        unresolved P0/P1 disagreements, material changes to this reviewer, and
        every 10 APPROVED_CLOSED patches.

    Returns (level: int, label: str, reasons: list[str]).
    """
    level3_required, level3_reasons = _classify_level3_triggers(packet, all_findings)
    if level3_required:
        return 3, "LEVEL_3_CRITICAL_EXTERNAL_RED_TEAM", level3_reasons

    diff_files = _diff_files(packet)
    all_diff_text = " ".join(diff_files).lower()

    _L2_PATTERNS: dict[str, list[str]] = {
        "model_or_calibration_methodology": [
            "hit_probability", "model_registry", "prob_ledger",
            "calibrat", "binomial", "poisson",
        ],
        "gate_engine_core_pipeline": [
            "pipeline.py", "classifier.py", "gate_engine/governance",
            "gate_engine/llp", "gate_engine/full_model",
        ],
        "shared_skill_interface": [
            "contracts.py", "orchestrator.py", "adapters/__init__.py",
        ],
    }

    l2_reasons: list[str] = []
    for reason_name, patterns in _L2_PATTERNS.items():
        if any(p in all_diff_text for p in patterns):
            l2_reasons.append(reason_name)

    if any(f.get("severity") == "P1" for f in all_findings):
        l2_reasons.append("p1_findings_present")

    if l2_reasons:
        return 2, "LEVEL_2_GOVERNANCE_MODEL_IMPACT", l2_reasons

    return 1, "LEVEL_1_ROUTINE", []


# ---------------------------------------------------------------------------
# Adversarial test proposals + bypass attempts
# ---------------------------------------------------------------------------

def _generate_adversarial_proposals(
    packet: dict, all_findings: list[dict]
) -> dict:
    """
    Generate concrete adversarial test proposals based on packet contents and findings.

    DESIGN:
      - Proposals specify tests that, if implemented, would increase confidence
        that the implementation is correct — or expose defects.
      - For authority/state-machine changes, at least one bypass attempt (AP-BYPASS-*)
        is MANDATORY and must be evidenced in the packet on resubmission.
      - Proposals never execute code; they are advisory output only.
      - can_execute = False unconditionally.

    Returns a structured dict for inclusion in calculations[].
    """
    proposals: list[dict] = []
    diff_files = _diff_files(packet)
    all_diff_text = " ".join(diff_files).lower()

    # ── Bypass attempts for authority/state-machine changes (MANDATORY) ───────
    _AUTH_SM_KEYWORDS = [
        "authority", "can_execute", "governance", "classifier",
        "state_machine", "pipeline", "orchestrator", "final_approved",
        "terminal_label",
    ]
    auth_sensitive = [f for f in diff_files
                      if any(kw in f.lower() for kw in _AUTH_SM_KEYWORDS)]
    if auth_sensitive:
        proposals.append({
            "proposal_id": "AP-BYPASS-001",
            "test_type":   "authority_bypass_attempt",
            "mandatory":   True,
            "description": (
                f"Authority/state-machine bypass attempt (MANDATORY): "
                f"diff touches authority-sensitive paths {auth_sensitive[:3]}. "
                f"Construct a context where the changed code receives a dict "
                f"with authority keys (can_execute=True, PRODUCTION_AUTHORITY=True) "
                f"and verify the engine rejects or ignores them. "
                f"Also verify no call path reaches FINAL_APPROVED status through "
                f"the changed code without full gate compliance."
            ),
            "targeted_files": auth_sensitive[:3],
            "expected_outcome": (
                "Authority invariants hold: can_execute remains False, "
                "FINAL_APPROVED not reachable without all gates passing, "
                "no authority key from caller input propagates to output."
            ),
            "bypass_hypothesis": (
                "H6: Changed code may fail to validate authority keys on "
                "certain input shapes (empty dict, missing keys, extra keys), "
                "allowing authority to be injected via context."
            ),
        })

    # ── SHA / candidate-commit integrity test (MANDATORY) ─────────────────────
    proposals.append({
        "proposal_id": "AP-SHA-001",
        "test_type":   "integrity",
        "mandatory":   True,
        "description": (
            "Verify that mutating candidate_commit_sha after packet freeze "
            "is detected by the packet_hash check. "
            "Construct a packet with a valid hash, then change candidate_commit_sha "
            "without recomputing the hash. "
            "Expected: reviewer returns P0 PACKET_DRIFT_DETECTED + BLOCKED."
        ),
        "expected_outcome": "P0 PACKET_DRIFT_DETECTED on any post-freeze SHA mutation.",
        "bypass_hypothesis": "H7: Packet drift must be cryptographically detectable.",
    })

    # ── Spec ambiguity proposal ────────────────────────────────────────────────
    criteria = packet.get("acceptance_criteria") or []
    if criteria:
        proposals.append({
            "proposal_id": "AP-SPEC-001",
            "test_type":   "spec_ambiguity",
            "mandatory":   False,
            "description": (
                "Construct an alternative implementation that satisfies "
                "each acceptance criterion as literally stated but violates "
                "the obvious intended behavior. If such an implementation passes "
                "all tests, the criterion or test suite is under-specified."
            ),
            "criteria_count": len(criteria),
            "expected_outcome": (
                "No adversarial reading of any criterion passes the full test suite."
            ),
            "bypass_hypothesis": "H8: Ambiguous criteria allow non-conforming implementations.",
        })

    # ── Negative-path proposal (MANDATORY when neg_cases empty) ───────────────
    neg_cases = packet.get("tested_negative_cases") or []
    if not neg_cases:
        proposals.append({
            "proposal_id": "AP-NEG-001",
            "test_type":   "failure_path",
            "mandatory":   True,
            "description": (
                "No negative cases are evidenced. For each acceptance criterion, "
                "add a test that submits input violating the primary condition. "
                "Expected: system rejects or fails safely (not silently succeeds)."
            ),
            "expected_outcome": "System fails closed on criterion violation.",
            "bypass_hypothesis": "H4: Untested failure paths may be fail-open.",
        })

    # ── Mutation proposals for P1 findings ────────────────────────────────────
    p1_findings = [f for f in all_findings if f.get("severity") == "P1"]
    for i, finding in enumerate(p1_findings[:3]):
        fid = finding.get("finding_id", f"UNKNOWN-{i}")
        proposals.append({
            "proposal_id": f"AP-MUT-{i+1:03d}",
            "test_type":   "mutation",
            "mandatory":   False,
            "description": (
                f"Mutation test for finding {fid!r}: remove or invert "
                f"the primary condition responsible for this finding. "
                f"Determine whether any existing test catches the mutation. "
                f"If no test catches it, the test suite is under-powered for "
                f"this dimension."
            ),
            "target_finding_id": fid,
            "target_description": finding.get("description", "")[:120],
            "expected_outcome": (
                "At least one existing test fails when the condition is "
                "removed or inverted."
            ),
            "bypass_hypothesis": "H3/H4: Conditions without negation tests cannot be falsified.",
        })

    mandatory_count = sum(1 for p in proposals if p.get("mandatory"))
    return {
        "adversarial_proposals": proposals,
        "total_proposals":       len(proposals),
        "mandatory_proposals":   mandatory_count,
        "note": (
            "Mandatory proposals (mandatory=True) must be evidenced in the packet "
            "on resubmission. The reviewer will flag absent mandatory evidence as P1."
        ),
        "can_execute": False,
    }


# ---------------------------------------------------------------------------
# Recommendation and label
# ---------------------------------------------------------------------------

def _compute_recommendation(findings: list[dict],
                            level3_required: bool,
                            packet_valid: bool) -> str:
    if not packet_valid:
        return "BLOCKED"
    p0 = [f for f in findings if f.get("severity") == "P0"]
    p1 = [f for f in findings if f.get("severity") == "P1"]
    if p0:
        return "BLOCKED"
    if level3_required and p1:
        return "BLOCKED"
    if p1:
        classes = {f.get("defect_class", "") for f in p1}
        if "implementation_defect" in classes or "governance_defect" in classes:
            return "REPAIR_REQUIRED"
        if "evidence_defect" in classes:
            return "EVIDENCE_REQUIRED"
        if "specification_defect" in classes:
            return "SPEC_CLARIFICATION_REQUIRED"
        return "REPAIR_REQUIRED"
    # P2/P3 only or no findings
    spec_issues = any(
        f.get("defect_class") == "specification_defect" for f in findings
    )
    if spec_issues:
        return "SPEC_CLARIFICATION_REQUIRED"
    return "READY_FOR_CHATGPT_RULING"


def _label_from_findings(findings: list[dict]) -> str:
    sev = max_severity(findings)
    if sev == "P0":
        return SkillLabel.REJECT_BAD_RULES.value
    if sev == "P1":
        return SkillLabel.HOLD.value
    if sev in ("P2", "P3"):
        return SkillLabel.SCOUT.value
    return SkillLabel.WATCH.value   # no findings — advisory presence, no ceiling impact


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class RedTeamReviewerAdapter(BaseSkillAdapter):
    """
    WOW Governed Red-Team Reviewer.

    Advisory, downgrade-only.  No terminal-label, production, capital,
    deployment, exception, or execution authority.

    FINAL_AUTHORITY: CHATGPT_ONLY.
    """
    SKILL_ID      = SKILL_ID
    SKILL_VERSION = SKILL_VERSION

    _AUTHORITY_STATEMENT = (
        "FINAL_AUTHORITY: CHATGPT_ONLY. "
        "This reviewer is advisory and downgrade-only. "
        "Replit must never self-approve based on this review. "
        "A READY_FOR_CHATGPT_RULING recommendation is an invitation for "
        "external review, not an approval."
    )

    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        inputs = self._inputs_or_empty(context)

        packet = context.get("review_packet")

        # ── No packet: advisory no-op ─────────────────────────────────────────
        if packet is None:
            return SkillResult.watch(
                self.SKILL_ID, self.SKILL_VERSION, inputs,
                findings=[],
                reason=(
                    "No review_packet in context. Reviewer is present but "
                    "inactive for this request. "
                    + self._AUTHORITY_STATEMENT
                ),
                run_id=run_id,
            )

        # ── Validate packet structure and hash ────────────────────────────────
        struct_result = validate_packet_structure(packet)
        if not isinstance(packet, dict):
            return SkillResult.reject(
                self.SKILL_ID, self.SKILL_VERSION, inputs,
                code="RTR_PACKET_TYPE_ERROR",
                message=f"review_packet must be a dict; got {type(packet).__name__!r}. "
                        "Failing closed.",
                label=SkillLabel.REJECT_BAD_RULES.value,
                run_id=run_id,
            )

        hash_valid, hash_detail = validate_packet_hash(packet)

        packet_valid = struct_result.is_valid and hash_valid

        # ── Formulate falsification hypotheses ────────────────────────────────
        hypotheses = {
            "pre_evaluation_hypotheses": _STANDARD_HYPOTHESES,
            "note": ("Hypotheses formulated before examining implementation "
                     "claims. Each dimension attempts to refute or confirm "
                     "the applicable hypothesis."),
        }

        # ── Run all 13 dimensions ─────────────────────────────────────────────
        all_findings: list[dict]  = []
        dimension_results: list[dict] = []

        dim_fns = [
            lambda p=packet: _dim01_spec_quality(p),
            lambda p=packet: _dim02_spec_match(p),
            lambda p=packet: _dim03_scope_integrity(p),
            lambda p=packet: _dim04_authority_integrity(p),
            lambda p=packet: _dim05_governance_integrity(p),
            lambda p=packet: _dim06_packet_integrity(
                p, hash_valid, hash_detail, struct_result.is_valid),
            lambda p=packet: _dim07_evidence_integrity(p),
            lambda p=packet: _dim08_test_quality(p),
            lambda p=packet: _dim09_reproducibility(p),
            lambda p=packet: _dim10_failure_path(p),
            lambda p=packet: _dim11_regression_risk(p),
            lambda p=packet: _dim12_resubmission_pattern(p),
            lambda p=packet: _dim13_blocker_remediation(p),
        ]

        for fn in dim_fns:
            try:
                dim_result, local_findings = fn()
                dimension_results.append(dim_result)
                all_findings.extend(local_findings)
            except Exception as exc:  # dimension error → advisory finding
                dimension_results.append({
                    "dim_id": "DIM-ERR",
                    "verdict": "FAIL",
                    "cited_evidence": [],
                    "reasoning": f"Dimension raised exception: {exc!r}",
                    "findings": [],
                })

        # ── Structural validation findings ────────────────────────────────────
        if struct_result.errors or struct_result.malformed_fields:
            all_findings.append(_f(
                "F-PKT-001", "DIM-06", "evidence_defect", "P0",
                f"Packet structure invalid: errors={struct_result.errors}; "
                f"malformed={struct_result.malformed_fields}. "
                f"Failing closed.",
                evidence=f"Structure errors: {struct_result.errors[:3]}",
                hypothesis="H7: Malformed packet cannot be trusted.",
            ))

        # ── Risk level routing (Level 1 / 2 / 3) ─────────────────────────────
        risk_level, risk_label, risk_reasons = _classify_risk_level(
            packet, all_findings
        )
        level3_required = risk_level == 3
        level3_reasons  = risk_reasons if level3_required else []

        # ── Adversarial test proposals ────────────────────────────────────────
        adversarial_proposals = _generate_adversarial_proposals(packet, all_findings)

        # ── Blockers (P0 → fatal, P1 → non-fatal) ────────────────────────────
        blockers: list[dict] = []
        for finding in all_findings:
            sev = finding.get("severity", "P3")
            if sev == "P0":
                blockers.append({
                    "code": f"RTR_{finding['finding_id'].replace('-', '_')}",
                    "message": finding["description"],
                    "fatal": True,
                    "source": SKILL_ID,
                })
            elif sev == "P1":
                blockers.append({
                    "code": f"RTR_{finding['finding_id'].replace('-', '_')}",
                    "message": finding["description"],
                    "fatal": False,
                    "source": SKILL_ID,
                })

        # ── Recommendation ────────────────────────────────────────────────────
        recommendation = _compute_recommendation(
            all_findings, level3_required, packet_valid
        )

        # ── Label ─────────────────────────────────────────────────────────────
        label = _label_from_findings(all_findings)

        # ── Summary stats ─────────────────────────────────────────────────────
        sev_counts = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
        for f in all_findings:
            sev_counts[f.get("severity", "P3")] = (
                sev_counts.get(f.get("severity", "P3"), 0) + 1
            )

        # ── Build result ──────────────────────────────────────────────────────
        return SkillResult(
            skill_id=self.SKILL_ID,
            skill_version=self.SKILL_VERSION,
            run_id=run_id or None,
            inputs_used=inputs,
            sources=[],
            data_quality="complete" if packet_valid else "partial",
            findings=all_findings,
            conflicts=[],
            blockers=blockers,
            calculations=[hypotheses] + dimension_results + [adversarial_proposals],
            label=label,
            confidence=0.0,   # advisory — no aggregate confidence score emitted
            can_execute=False,
            downstream=[{
                "recommendation":        recommendation,
                "risk_level":            risk_level,
                "risk_label":            risk_label,
                "risk_reasons":          risk_reasons,
                "level_3_required":      level3_required,
                "level_3_reasons":       level3_reasons,
                "severity_counts":       sev_counts,
                "packet_valid":          packet_valid,
                "struct_warnings":       struct_result.warnings,
                "override_log_schema":   "WOW_CHATGPT_OVERRIDE_LOG_v1",
                "overridable_recs":      ["REPAIR_REQUIRED", "BLOCKED"],
                "p0_override_rule": (
                    "P0 findings cannot be cleared by ordinary override. "
                    "Either resolve the underlying condition and resubmit, "
                    "or provide governing_spec_change in the override record."
                ),
                "can_execute":           False,
                "authority_statement":   self._AUTHORITY_STATEMENT,
            }],
        )
