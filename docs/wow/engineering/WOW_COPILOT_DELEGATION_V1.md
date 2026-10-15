# WOW Engineering — Copilot bounded delegation v1

**State: STAGED / NOT PRODUCTION-AUTHORIZED.** This file is a design and pilot protocol, not a new control-plane leader. See pilot issue #1763. Current repository is public; GitHub's native Copilot Automations currently require private/internal repository visibility. Do **not** change the repository visibility merely to activate automations.

## Ownership and actual authority

The existing WOW Engineering incident queue and single-writer lease remain authoritative. Copilot performs bounded implementation, regression, and advisory review tasks **only after** an independently verified assignment. No second scheduler, queue, release authority, or incident owner is created here. Do not let a GitHub label, issue comment, bot acknowledgment, or self-declared lease grant production authority.

- Triage/controller: deduplicate event, establish severity/owner, resolve active writer conflicts, verify lease and exact current-main SHA, and record a durable packet.
- Copilot repair: reproduce, patch only enumerated paths, run tests, write a Draft PR and return source/head SHA and evidence.
- Copilot regression: challenge negative/adjacent failure modes, keep author/test evidence separate.
- Copilot review: advisory findings only; **not** the independent CODEOWNER, security, QA or Release Authority.
- Existing protected workflows and authorized Release Authority: independently review, certify exact head, merge/deploy only after approvals, verify live SHA and behavior, and persist acceptance receipts.

## Packet contract

Run the offline non-authorizing validator:

```bash
python artifacts/wow-engine/v17/copilot_handoff_contract.py handoff.json \
  --repository gregoryharper84-ship-it/WOW-Dashboard \
  --current-main-sha '<exact 40-character lower-case main SHA>'
```

Example (replace SHA with the actual protected-main SHA before using):

```json
{
  "schema": "WOW_COPILOT_HANDOFF_V1",
  "repository": "gregoryharper84-ship-it/WOW-Dashboard",
  "incident_id": 1763,
  "mode": "PROPOSE_ONLY",
  "can_execute": false,
  "source_main_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "severity": "P1",
  "change_class": "A",
  "owner": "WOW_ENGINEERING",
  "lease_group": "COPILOT_PILOT",
  "allowed_paths": ["artifacts/wow-engine/test_v17_copilot_issue_pilot.py"]
}
```

`PACKET_VALID_NON_AUTHORIZING` only proves local shape checks. It **does not** verify incident authority, user/bot identity, lease ownership, active competing writes, source provenance, GitHub session creation, PR legitimacy, CI, or QA. Those checks must be performed using trusted authenticated evidence; otherwise `BLOCKED_WITH_EXACT_REASON`.

## Phase 1 — public-repo proof

1. Issue #1763 is a bounded test-only task assigned to Copilot. Observe the actual GitHub Copilot session; assignment alone is not execution.
2. Prove code-changing Draft PR from the Copilot actor, exact linked incident, correct base/head SHA, and only the allowed test file changed.
3. Run focused and negative regressions, exact SHA CI, adversarial advisory review, and independent CODEOWNER/security/QA when applicable.
4. Record issue/owner/lease, assignment actor and time, session URL, PR URL, exact SHAs, test commands and outcomes, blockers, next action and re-entry. Do not close the production incident on PR creation.
5. If Copilot cannot start, issue assignment produces no PR, or a competing writer exists, persist a typed HOLD; no retry storms or fake closure.

The handoff validator can check an independently fetched PR JSON snapshot and changed-file list using `verify_draft_pr()`; the result is at most `PR_CREATED_UNVERIFIED`, never production green.

## Phase 2 — controlled automation (not implemented or activated)

Only after the pilot passes and the current control plane has independently attested **atomic single-writer leases, actor provenance, branch/PR conflict inventory and durable reconciliation**, may a CODEOWNER-approved Class B extension add automated assignment and receipt processing. Required coverage: replay/deduplication, wrong repository, stale main/PR head, missing/copied lease, concurrent writers, unauthorized issue author, timeout, quota, missing session, malicious issue content, missing/failed CI, bot-authored review not counted as independent approval, and release drift.

Do not use native Copilot Automations on a public repository; that feature's current availability excludes it. Do not change repository visibility, permissions, workflow trust roots, or release policy to work around this gate. If native automations become eligible, they must still funnel all work through the **existing** incident controller and its leases.

## Non-negotiable invariants

```text
custom_gpt_identity=WOW_BETTING_ENGINE
runtime_generation=V17_ACTIVE
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```

Class A is the only class eligible for a bounded implementation pilot. Class B requires governed review before adoption. Class C is experiment-only; fitted sporting-probability specialist authority, calibration, qualification and terminal reducer remain unchanged. No live bets/orders. No self-approval or deployment. **PR_CREATED ≠ MERGED ≠ DEPLOYED ≠ FIXED_AND_VERIFIED.**
