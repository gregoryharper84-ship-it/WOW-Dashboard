# Viktor audit → WOW V17 existing engineering workers (P candidate)

Status: PROTECTED REVIEW CANDIDATE, NOT MERGED OR ACTIVE. Do not merge until current exact-head P-class evidence and owner authorization pass.
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
1. Use the owner-approved zero-mandatory-human-review policy in docs/WOW_STABILITY_FIRST_MINIMUM_GOVERNANCE.md (issue #1830); do not introduce a single-owner review deadlock. Preserve GitHub ruleset 24907422's three App-pinned required regressions, strict exact-head checks, stale-review dismissal, deletion/non-fast-forward protection and zero bypass. P-class release still requires negative-security tests, independently sourced exact-head QA App (5245832) and Release Authority App (5245853) evidence, substantive findings resolved and authenticated owner release authorization. Automated check PASS is not an independent semantic review or permission to merge.
2. Review the actual Viktor App installation's permissions; the App advertises broad Actions, contents, workflows and pull-request write rights. Scope it to least privilege before go-live.
3. Exact-head CI and independent QA/Release/Security sign-off for the two protected worker workflow edits (class P).
4. Verify a real admitted worker reads an authenticated Viktor pointer for an OPEN registered issue without duplicate dispatch or model release. A verified comment is not a worker run.
5. Verify any later actual repair separately: protected merge, both Render SHAs, authenticated route, Supabase readback and scoreboard delta.

No new scheduler, queue, live trades, probability calculation, self-approval, or direct main writes.

## Codex negative-review lessons (2026-11-22)

- A same-PR repair in the Claude workflow can detach to an older commit before optional intake. The helper is therefore copied **from the verified GitHub dispatch SHA** into runner temp before any repair-head checkout. The older PR must never provide executable intake code. A regression executes the pinned helper with no module in the repair checkout.
- Malformed URL ports/brackets, whitespace, encoded strings, traversal, and natural-language suffixes are rejected with no exceptions. The only allowed references are exact repository commit/blob paths, numeric issue/PR pages, and numeric workflow-run/job URLs. GitHub App provenance by itself does not make text safe.
- Both are preserved as permanent tests. Optional Viktor comments never grant dispatch, merge, qualification, or execution authority.
