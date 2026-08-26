# WOW Governed Red-Team Reviewer

**Skill ID:** `wow.governed-red-team-reviewer`
**Version:** 1.0.0
**Adapter:** `skills/adapters/red_team_reviewer.py`

---

## Authority Boundaries (unconditional, non-negotiable)

```
can_execute              = False
PRODUCTION_AUTHORITY     = False
USER_OUTPUT_AUTHORITY    = False
TERMINAL_LABEL_AUTHORITY = False
```

This reviewer is **advisory and downgrade-only**. It is never a terminal-label, production, deployment, capital, exception, or execution authority.

**FINAL_AUTHORITY: CHATGPT_ONLY.**  
Replit must never self-approve based on this review. The reviewer produces a `READY_FOR_CHATGPT_RULING` recommendation when the packet is complete and no P0/P1 findings are present — that is not an approval. ChatGPT makes the final ruling.

---

## Purpose

The governed red-team reviewer independently verifies implementation work before outputs leave the pipeline. It:

1. Formulates falsification hypotheses *before* evaluating implementation claims
2. Attempts to identify bypasses and fail-open paths
3. Generates adversarial test proposals
4. Classifies findings by defect class and severity
5. Determines whether Level-3 external review is required

---

## Invocation

The reviewer is wired into `_ROUTE_PLAYER_PROP`, `_ROUTE_SPORTS_TEAM`, and `_ROUTE_GOVERNANCE_REVIEW` in the orchestrator. It also accepts a `review_packet` key in the orchestrator context.

When no `review_packet` is present in context, the reviewer returns `WATCH` (advisory presence, no-op, does not lower the ceiling further than any upstream skill already has).

When a `review_packet` is present, the reviewer runs all 13 dimensions and returns a full review result.

---

## Review Packet

The review packet is a frozen, machine-oriented dict bound at minimum to:

| Field | Description |
|-------|-------------|
| `work_item_id` | Work item / review attempt identifier |
| `review_attempt` | 1-based attempt number |
| `spec_version` | Specification version |
| `spec_hash` | SHA-256 of spec content |
| `base_commit_sha` | Base (pre-change) commit |
| `candidate_commit_sha` | Candidate (post-change) commit |
| `diff_manifest` | List of `{file, sha256, op}` entries |
| `acceptance_criteria` | List of acceptance criteria strings |
| `test_commands` | Exact reproducible test commands |
| `test_artifacts` | List of `{artifact_id, content_hash, raw_output}` |
| `test_counts` | `{passed, failed, skipped, xfail}` |
| `runtime_governance_hash` | Governance hash at review time |
| `tested_edge_cases` | List of edge cases tested |
| `tested_negative_cases` | List of negative/failure-path cases tested |
| `prior_review_history` | Prior review results (list of dicts) |
| `prior_blockers` | Prior blockers, each with `{blocker_id, description, severity, status}` |
| `packet_creation_timestamp` | ISO-8601 UTC timestamp |
| `packet_hash` | SHA-256 of all other fields (canonical JSON) |

**Packet drift**: If `candidate_commit_sha` changes or any field is modified after freeze, the `packet_hash` will not match the recomputed hash. This triggers a P0 `PACKET_DRIFT_DETECTED` finding and a `BLOCKED` recommendation.

The packet builder must be deterministic and non-editorial: it may collect required artifacts but must not select favorable evidence or summarize correctness.

---

## 13 Review Dimensions

| ID | Dimension |
|----|-----------|
| DIM-01 | Specification quality / ambiguity |
| DIM-02 | Specification match |
| DIM-03 | Scope integrity |
| DIM-04 | Authority integrity / self-approval detection |
| DIM-05 | Governance integrity |
| DIM-06 | Packet integrity / drift |
| DIM-07 | Evidence integrity / provenance |
| DIM-08 | Test quality / test independence |
| DIM-09 | Reproducibility |
| DIM-10 | Failure-path review |
| DIM-11 | Backward compatibility / regression risk |
| DIM-12 | Resubmission-pattern detection |
| DIM-13 | Prior-blocker remediation tracking |

Every PASS/PARTIAL/FAIL verdict must be supported by cited packet evidence or explicit reasoning. No dimension may return a verdict without evidence.

---

## Defect Classification

Every finding before remediation is classified as one of:

| Class | Description |
|-------|-------------|
| `implementation_defect` | Code or logic error in the implementation |
| `evidence_defect` | Missing, incomplete, or insufficient test evidence |
| `specification_defect` | Ambiguous, incomplete, or incorrect specification |
| `governance_defect` | Authority bypass, governance constant modification, or self-approval |

---

## Severity Scale

| Level | Definition |
|-------|-----------|
| P0 | Hard blocker: governance/security/authority bypass or evidence-integrity compromise. Cannot be averaged away. |
| P1 | Material correctness risk |
| P2 | Important weakness |
| P3 | Non-blocking hardening suggestion |

P0 findings always produce a `BLOCKED` recommendation. P0 cannot be mitigated by other passing dimensions.

---

## Recommendations

| Value | Meaning |
|-------|---------|
| `READY_FOR_CHATGPT_RULING` | Packet complete, no P0/P1 findings. External ChatGPT review may proceed. |
| `REPAIR_REQUIRED` | P1 implementation or governance defect found. Resubmit after repair. |
| `EVIDENCE_REQUIRED` | P1 evidence defect: missing test artifacts, empty test commands, or unverifiable claims. |
| `SPEC_CLARIFICATION_REQUIRED` | P1 specification defect: ambiguous or incomplete spec blocks evaluation. |
| `BLOCKED` | P0 finding, packet drift, or Level-3 trigger combined with P1. No further work without external review. |

---

## Level-3 Review Triggers

Level-3 external review is **mandatory** (not optional) for:

- Governance or authority-boundary changes
- Authentication / security changes
- Irreversible migrations
- Production / live execution paths
- Major probability or calibration methodology changes
- Capital-authority changes
- Unresolved P0/P1 disagreements between reviewer versions
- Any material change to this reviewer, packet builder, severity taxonomy, or authority boundaries
- Every 10th `APPROVED_CLOSED` patch (configurable cadence)

When Level-3 is required, the reviewer records `level_3_required: true` and `level_3_reasons` in `downstream`. It does not claim external review has occurred when it has not.

---

## Resubmission

On resubmission (`review_attempt > 1`), every prior blocker must be individually classified:

| Status | Meaning |
|--------|---------|
| `RESOLVED` | Evidence in diff_manifest or test_artifacts shows the blocker is addressed |
| `STILL_PRESENT` | Blocker condition still observable in current findings |
| `REGRESSED` | Was previously resolved; now failing again |
| `NOT_EVIDENCED` | Claimed resolved but no packet evidence supports the claim |

`NOT_EVIDENCED` → P1 per unresolved blocker.  
`STILL_PRESENT` on a P0/P1 blocker → P0 in current review.  
`REGRESSED` → P0 always.
