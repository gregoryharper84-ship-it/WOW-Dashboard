# WOW AMRT — Seven Specialist Team Registry

**Status: SOURCE CANDIDATE / NOT MERGED OR DEPLOYED.** This source package extends [the existing AMRT recovery implementation](WOW_AMRT_RECOVERY_TEAM.md) on PR #1843; it does not start seven autonomous daemons or create a separate ChatGPT GPT team.

## Deliverables

The authoritative source roster is `artifacts/wow-engine/v17/amrt_team_registry.json`. Seven `.agents/skills/wow-amrt-*/SKILL.md` agent definitions bind directly to the callable functions already implemented in `v17.amrt_operational_lanes`, and `test_v17_amrt_team_registry.py` verifies the correspondence and safety invariants. The existing resident Engineering worker and Conductor are the only dispatch and incident owners. The source candidate from PR #1861 has five deeper model-restoration sub-specialists; those may be adopted under the same seven-role lifecycle later, with no competing writer or control plane.

## Start-to-finish incident lifecycle

`BLOCKER_SENTINEL` → `ROOT_CAUSE_INVESTIGATOR` → `MODEL_RECOVERY_ENGINEER` or `DATA_INTEGRATION_ENGINEER` → `CODE_CI_ENGINEER` → independently signed `INDEPENDENT_QA` → `RELEASE_VERIFICATION` → external `INDEPENDENT_VERIFICATION` for closure.

Every blocker retains one accountable owner, exact source and root-cause refs, scoped lease, change class, PR/CI SHA, approval provenance, matching live Render SHA, verified user-facing Action receipt, persistence readback and reconciliation. A stage's next owner does not create a new accountable incident owner. No implementation worker may forge QA or Release attestation.

**Priorities:** P0 model correctness/publication safety/availability; P1 recovery and autonomy; P2 recurring/data defects; P3 optimization/technical debt.

**Terminal:** `V17_TERMINAL_REDUCER` only; `can_execute=false`. A missing fitted specialist remains truly unavailable, but an invoked scorer failure, insufficient row inputs, missing stage proof, missing publication receipt, absent exact-line market data and an Action invocation blocker remain their separate typed states.

## Validation

From `artifacts/wow-engine` run:

```bash
python -m pytest -q test_v17_amrt_closure_gate.py test_v17_amrt_operational_lanes.py test_v17_amrt_receipt_bridge.py test_v17_amrt_team_registry.py
```

Independent CI results must be verified against the *new exact PR head*, not assumed from earlier tests.

## Activation remains separate

1. Authenticated Engineering App adopts approved source through existing Conductor and writer lease.
2. CI, protected checks, independent QA and Release approve the exact candidate SHA, including negative regression cases.
3. Authorized merge and Render deployment are separately verified. Confirm Engineering resident heartbeat and persisted handoff.
4. Only close a repaired model incident after independently verified Action → model → terminal → persistence evidence at live SHA.

No model weights, probability calibration, qualification thresholds, security permissions, deployment workflow or trading operations are changed by this roster addition.
