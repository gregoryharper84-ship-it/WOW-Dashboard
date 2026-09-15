# GOVERNANCE-DECISION-2026-09-14-NFL

Decision: NFL bridge registration and NFL model certification are separate.

Current decision:
- Runtime NFL bridge: APPROVE FOR DEPLOYMENT VALIDATION.
- Add NFL to CERTIFIED_TEAM_EVENT_SPORTS: NOT YET.
- Certification state after successful deployment: CANDIDATE_REGISTERED_UNCERTIFIED.
- can_execute=false.

Reason:
The active production governance currently contains explicit prospective certification evidence for MLB but no equivalent NFL certification package. A fitted/scorable NFL chain is evidence of capability, not sufficient evidence of calibration/prospective certification.

Next action:
Run the active V17 sport-model certification process on the deployed NFL model after production smoke validation.
