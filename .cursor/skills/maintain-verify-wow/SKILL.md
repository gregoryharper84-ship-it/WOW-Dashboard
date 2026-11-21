---
name: maintain-verify-wow
description: Maintain WOW V17 verify-wow feature map and read-only doctor using source review and real user-path drives; never change product behavior or introduce release gates.
---

# Maintain verify-wow

This is maintenance of the existing skill, not another worker, scheduler, approval or deployment controller. Edit only .cursor/skills/verify-wow/ (SKILL.md, features/, scripts/, tests/). Product bugs are routed to the existing Engineering/AMRT incident system, not patched here.

1. Index hygiene: compare features/README.md entries to feature files, removing dead/duplicate items and adding only source-grounded paths.
2. Parallel source wave: use one read-only investigator per feature to identify implementation source, likely skill drift, concrete live drive recipe and prerequisites. Children never drive the shared app or edit files.
3. Reconcile all source findings, spot-check suspected drift, and examine recent changed user-facing routes for omissions.
4. Live pass: one coordinator drives each mapped user feature at least once using verify-wow's Launch, Doctor, Drive, Evidence and Cleanup steps. Doctor before first drive and after surprising failures. If live host/auth/current pregame data is unavailable, record VERIFIED_UNREACHABLE with exact prerequisite, never invented PASS.
5. Triage: fix documentation drift in skill, harness drift in skill and re-drive it, or report genuine product defects to the existing incident. Never weaken a journey to get a green result.
6. Clean only processes/scratch created by this run. Check evidence still exists after every cleanup.
7. Outcome: CLEAN (complete source/live coverage, no PR), CHANGED (one PR maximum, only proven skill corrections), BLOCKED (specific unmet prerequisites or defects, no false PASS). Re-read every changed file before proposing a PR.

This process is advisory and not an independent review or production release controller. Preserve V17_TERMINAL_REDUCER and can_execute=false; no wagers, trading or model overrides.
