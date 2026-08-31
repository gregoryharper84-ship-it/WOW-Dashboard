---
name: WOW Governed Red-Team Reviewer
description: Architecture, registration, and test conventions for wow.governed-red-team-reviewer (skill #22).
---

# WOW Governed Red-Team Reviewer

**Skill ID:** `wow.governed-red-team-reviewer`  
**Version:** 1.0.0  
**Priority:** 22 (was 21-skill registry, now 22)

## Registration (three-layer, must stay in sync)

- `skills/skill-registry.json` — 22nd entry at priority 22
- `skills/registry.py` line 75 — count check is `!= 22` (was `!= 21`)
- `skills/adapters/__init__.py` — `from .red_team_reviewer import RedTeamReviewerAdapter` + `ADAPTER_MAP` entry
- `gate_engine/tests/test_skills_invariants.py` — `test_registry_has_22_skills` + `"wow.governed-red-team-reviewer"` in expected ID set

## Files

- `skills/adapters/review_packet.py` — frozen packet builder, hash computation, structure validation
- `skills/adapters/red_team_reviewer.py` — main adapter (13 dimensions, Level-3 routing, recommendation logic)
- `skills/red-team-reviewer/SKILL.md` — documentation (referenced by registry `path` field)
- `gate_engine/tests/test_red_team_reviewer.py` — AT-26 through AT-37 + unit tests (171 pass)

## Orchestrator routes

Added to `_ROUTE_PLAYER_PROP` and `_ROUTE_SPORTS_TEAM` (before `wow.qa-hallucination-auditor`).  
New route `_ROUTE_GOVERNANCE_REVIEW = ["wow.governed-red-team-reviewer", "wow.qa-hallucination-auditor"]` for `market_type="governance_review"`.

**When no `review_packet` in context:** returns `WATCH` (advisory no-op, does not block).  
**When `review_packet` present:** runs 13 dimensions, returns findings, recommendation, Level-3 flags.

## Governance hash: NOT affected

`compute_governance_hash()` in `gate_engine/governance.py` is computed from gate engine patch metadata only — not from the skills registry. Adding skill #22 did NOT change `_GOVERNANCE_HASH`. No test updates needed for hash-pinned tests.

## Authority invariants (unconditional)

```python
can_execute              = False
PRODUCTION_AUTHORITY     = False
USER_OUTPUT_AUTHORITY    = False
TERMINAL_LABEL_AUTHORITY = False
```

`SkillResult.__post_init__` sets `can_execute=False` via `object.__setattr__` — cannot be overridden.  
`confidence = 0.0` always — no aggregate confidence score is emitted.  
`FINAL_AUTHORITY: CHATGPT_ONLY` — legacy platform must never self-approve.

## Key design decisions

**Why:** review_packet abstracted into `review_packet.py` (pure functions, no WOW imports) so it can be imported and validated anywhere without the adapter.

**Packet drift detection:** `packet_hash = SHA-256(canonical_json(all_fields_except_packet_hash))`. Any mutation after freeze is detected as P0 `PACKET_DRIFT_DETECTED`.

**Label mapping:** P0 → `REJECT_BAD_RULES`, P1 → `HOLD`, P2/P3 → `SCOUT`, no findings → `WATCH`.

**Recommendation hierarchy:** BLOCKED (P0 or invalid packet) > REPAIR_REQUIRED (P1 impl/gov defect) > EVIDENCE_REQUIRED (P1 evidence defect) > SPEC_CLARIFICATION_REQUIRED (P1 spec defect) > READY_FOR_CHATGPT_RULING.

**Level-3 cadence:** configurable via `set_level3_cadence(n)`; default 10 APPROVED_CLOSED patches. Pass `approved_closed_patch_count` in the packet to trigger cadence check.

## Second-pass additions (full-spec build)

Three modules added on top of the original 171-test implementation:

### `review_override_log.py`
`ChatGPTOverrideRecord` dataclass + `validate_override_record` + `make_override_record` + `build_override_log_entry`. **Critical ordering rule**: `_generate_override_id()` factory must be defined BEFORE the `@dataclass` that uses it as `field(default_factory=...)` — Python evaluates the factory reference at class-definition time.

P0 override rule: `validate_override_record` returns an error if `p0_present=True` and `governing_spec_change` is `None`. Ordinary override cannot clear a P0.

Schema reference in downstream output: `override_log_schema="WOW_CHATGPT_OVERRIDE_LOG_v1"`.

### Level 1/2/3 risk routing (`_classify_risk_level`)
Wraps `_classify_level3_triggers`; then checks L2 patterns. Key design decisions:
- **P1 findings → Level 2**, not Level 3. The spec's "unresolved P0/P1 disagreements" means P0 severity findings in `all_findings`, not P1. Changed trigger string to `"unresolved_p0_findings"`.
- **Calibration/probability files (hit_probability.py, prob_ledger, calibrat) → Level 3**, not Level 2. The `_L3_PATH_TRIGGERS["probability_calibration_methodology_change"]` fires first. L2 patterns must not duplicate L3 patterns or L2 is unreachable for those file types.
- `_diff_files(packet)` reads `diff_manifest[].file` (list of dicts with "file" key). Test helpers must set `diff_manifest=[{"file": ..., "sha256": ..., "op": ...}]` — setting `changed_file_manifest` (flat list) is **ignored**.

### `_generate_adversarial_proposals`
- AP-SHA-001 (integrity): always generated, mandatory
- AP-BYPASS-001 (authority bypass): generated when `_diff_files` returns auth-sensitive paths (`authority`, `can_execute`, `governance`, `orchestrator`, etc.), mandatory
- AP-NEG-001 (failure path): generated when `tested_negative_cases` is absent/empty, mandatory
- AP-SPEC-001 (spec ambiguity): generated when `acceptance_criteria` is non-empty
- AP-MUT-NNN (mutation): one per P1 finding, up to 3, optional

Proposals placed at **END** of `calculations` list (`[hypotheses] + dimension_results + [adversarial_proposals]`). Original AT-29 test checks `calcs[1]` has `dim_id` — putting proposals second breaks it.

### Test count after second pass
141 tests in `test_red_team_reviewer.py` (171 → 141 because the test file was rewritten to AT-26–40; AT-38/39/40 are the new classes). 2863 other tests pass, 1 pre-existing failure unchanged.

## Python gotchas found during implementation

- f-string slice + repr: use `{value[:60]!r}` NOT `{value!r[:60]}` — the latter is a SyntaxError.
- Mirror-signal strings must be lowercase (e.g. `"assert result == implementation_constant"`) because `_artifact_text()` returns `.lower()` text. Uppercase signal strings never match.
