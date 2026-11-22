# Viktor audit → WOW V17 existing engineering workers (P candidate)

Status: PROTECTED DRAFT, NOT FOR MERGE OR ACTIVATION.
Identity: WOW_BETTING_ENGINE / V17_ACTIVE / V17_TERMINAL_REDUCER; can_execute=false; DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true.

The existing hourly dispatcher, approved manifest, GitHub issue state, single-writer lease and protected approvals remain authoritative. Issue comments do NOT trigger workers.
The full Viktor daily audit must stay private. Viktor may publish only a changed, sanitized, data-only evidence pointer to an existing OPEN registered incident.

## Required GitHub comment format
<!-- WOW_VIKTOR_AUDIT_V1 -->
{"schema":"WOW_VIKTOR_AUDIT_V1","incident_id":"1950","source_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","evidence_url":"https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/44959860492","fingerprint":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","failure_code":"MODEL_INPUTS_INSUFFICIENT"}

Hashes above are placeholders, not evidence. Use exact observed 40-char current main SHA, real GitHub source URL, canonical typed failure code, unique stable fingerprint, and the current admitted issue ID. Do not paste secrets, private audit text or probabilities.

## Admission and trust
- GitHub API must attest performed_via_github_app id=2603499 and slug=viktor-com, plus author user.id=285088163; username alone fails.
- Exact selected existing incident and exact worker SHA; <=48-hour GitHub-created freshness.
- Strict allowlist JSON and same-repository HTTPS evidence URL only. No extra instructions are passed to the agent.
- A full 100-comment API page, stale/invalid data, duplicates or missing scope yields no advisory.
- Existing worker context, model authority, certification, approval, leases and release remain unchanged. Every evidence link is independently reverified.

## Release prerequisites
1. Independently enforce review on main first. Read on 2026-11-22: ruleset 24907422 requires zero approving PR reviews and no code-owner review. Preserve all three existing required checks, no bypass actors and stale-review dismissal; require at least one non-author approval and code-owner review as supported.
2. Review the actual Viktor App installation's permissions; the App advertises broad Actions, contents, workflows and pull-request write rights. Scope it to least privilege before go-live.
3. Exact-head CI and independent QA/Release/Security sign-off for the two protected worker workflow edits (class P).
4. Verify a real admitted worker reads an authenticated Viktor pointer for an OPEN registered issue without duplicate dispatch or model release. A verified comment is not a worker run.
5. Verify any later actual repair separately: protected merge, both Render SHAs, authenticated route, Supabase readback and scoreboard delta.

No new scheduler, queue, live trades, probability calculation, self-approval, or direct main writes.
