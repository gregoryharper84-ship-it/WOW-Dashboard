# Gemini concurrent read-only pilot — implementation packet v0.1

Status: **draft engineering change, disabled by default**. No production
registration, secrets, weather model promotion, trading, automatic GitHub
writes, Supabase mutations, or Render deployment are included.

## Why this is a separate package

WOW already has 'artifacts/wow-engine/agent_runtime/orchestrator.py',
'agent_runtime/schemas.py', tables 'wow_agent_jobs' and
'wow_engineering_incident_writer_leases', and lease RPCs. Do not introduce
a second authority. Integrate through existing lifecycle only after #1739
is independently green, with fenced writer identities and shared
conflict-group admission. **Research can be parallel read-only.**

## Pilot lanes

* A — KALSHI_CONTRACT_AUDIT: 20 individual real rule snapshots, 20 report
  receipts, station/source/window/rounding/YES condition, conflicting sources,
  forecast issue/availability time, no inferred P(YES).
* B — WOW_DEFECT_DIAGNOSIS: bounded defects, PR head SHAs, failed check
  evidence, reproducible failure and permanent repair hypothesis.
* In both lanes, return **unverified findings**, not an independent release
  pass. SIRT / governed release remains independent.

## Actual Google Drive source documents created (Oct 14, 2026)

- Gemini Handoff: https://docs.google.com/document/d/1-9S8h5U98rDqurPZt-R8aEJFZRgaWfaALeZyT2U-BAI/edit
- Notebook Source & Verification Index: https://docs.google.com/document/d/121j7EI5lEwvvvGesAEEDMURGCyj7eEnrwrYn3LJZAZM/edit
- Activation tracking: https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1761
- **Gemini Notebook (user-confirmed created):** https://gemini.google.com/notebook/1c561cad-82ee-416b-a43c-368e58daf11d
  - Notebook identity registered; source imports, private sharing and citation-grounding are not yet independently verified.

Google Gemini Apps can manage a Gemini Notebook through the
`@Gemini Notebook` connected app where available. This PR cannot activate
that separate Google account integration. Notebook URL and import/citation
verification remain pending until the authorized account completes them.

## Setup (not provisioned by this PR)

1. Provision Gemini API access using Google AI Studio or approved Google Cloud
   account. Store GEMINI_API_KEY in a secrets manager, never Git or NotebookLM.
2. Approve a current model identifier and set GEMINI_MODEL; do not silently
   substitute a model. Pin dependencies at integration and record model ID,
   SDK version, token counts, run ID, source hashes and receipt schema version.
3. Build a deterministic dispatcher on existing WOW control plane. Read-only
   workers receive scoped snapshots and no GitHub/Render/Supabase write token.
4. Add a separate append-only pilot evidence store after schema/RLS review;
   don't alter the canonical P(YES) ledger or writer-lease table ad hoc.
5. Create the dedicated NotebookLM notebook with the supplied source pack
   'NOTEBOOKLM_SOURCE.md' and 'GEMINI_HANDOFF.md'; use only sanitized,
   non-secret material. A notebook is a retrieval workspace, not a runtime
   orchestration or settlement authority.
6. Budget gate: default disabled; cap daily calls and tokens before enabling.
   Actual costs, including caching, must be measured from provider metadata.
7. Run local schema tests, mock-provider tests, live adapter canary, negative
   prompt-injection tests and SIRT review. Then request independent approval
   for a read-only pilot deployment. Do not auto-merge.

## Safety and release acceptance

* Immutable as-of snapshots; fail if source available_at > decision_time.
* No unregistered evidence IDs; cited IDs still require external verification.
* Gemini output is untrusted, even when Pydantic validation passes.
* No Kalshi trades or sporting wagers. can_execute=false.
* Zero autonomous deployments/branch writes/approval by Gemini.
* Reject tool access / shell / filesystem / networking in model call.
* No secrets in evidence, NotebookLM, prompts or output receipts.
* Read-only pilot and future code-writing worker must use different identities.
* No deployment until #1739 group-fencing and existing worker admission are
  verified with production-safe negative tests.
* Do not mark implementation, live connection or notebook creation complete
  until independently confirmed.

## Verification

From this directory with Pydantic v2 available:
  python -m unittest discover -s . -p 'test_*.py' -v

Google SDK only required for a live call:
  pip install google-genai

Run `routing.plan_readonly(...)` to validate admission without side effects;
it defaults to PILOT_DISABLED and enforces bounded task/context budgets.

Worker analyze(...) is deliberately not wired to production. Integration
requires an approved data-flow threat model and a separate PR.
