# WOW Engineering and Model Execution Contract
Applies to all repository engineering, model-development and orchestration work.
Owner directive: 2026-10-11. Incident: #1672.
Read before work; retain existing stricter local governance.

## Execute authorized work
On proceed/fix/build/complete, inspect current evidence and perform the next permitted repair.
Do not end at a plan when an authorized implementation step is available.
A release identity, approval or deployment blocker blocks that transition only.
Continue independent reproduction, code repair, contracts, tests and review preparation.
Never bypass a gate or weaken a test to create progress.

## Evidence of progress
Distinguish activity from advancement. Reading status, heartbeats, summaries and unchanged
polls do not count as repairs. Each engineering update identifies:
- implemented change or executed diagnostic/test, with exact SHA/run/evidence;
- its result and remaining uncertainty;
- the exact blocked transition and the next permitted action.
If no advancement occurred, say so explicitly. Do not repeat the same status as progress.
A requested status answer is brief; continue the active authorized task afterward.
Never promise a completion time without verified executable prerequisites.

## Stall recovery
After two unchanged observations of the same blocker, stop identical polling and diagnose
the blocked operation. Inspect actionable logs/contracts and attempt the smallest permitted
repair, use an approved alternate route, or choose independent runnable work.
Long-running tests may remain running; their elapsed time alone is not a defect.
Record owner, incident, branch/head, lease, attempted action, result, retry budget,
next action and revisit trigger in existing durable engineering records.
An exhausted or access-blocked path gets a typed failure; ownership remains Engineering.
Do not ask the owner to repeat an existing authorization.
Runtime enforcement must be integrated into existing workers; this file is not a scheduler.

## Exclusive ownership
Check current branch head and ownership before writing. Use a single writer per branch.
A stale SHA or failed lease requires rereading the latest change and reconciling ownership.
Do not overwrite another session's repair, race a protected release, or claim its work.
Parallel work requires isolated ownership and bounded WIP under existing authorization.

## Model and agent behavior
Select the next action from current evidence and dependencies, not from repeated narrative
summaries. Preserve incident and revision identity across provider fallback.
Use only configured approved models/providers; quota fallback preserves deliverable contracts.
A provider failure is distinct from code/test/model failure.
Do not manufacture independent review by changing agent labels.
This contract changes engineering-agent behavior; it does not change fitted sporting models,
foundation-model weights or account-wide ChatGPT settings.
New probability-producing work follows governed replay, validation and promotion requirements.
Do not replace missing sporting probabilities with LLM judgment or market odds.

## Production closure
Use separate evidenced states: PR_CREATED, MERGED, DEPLOYED, PRODUCTION_VERIFIED.
Passing CI or a merged PR does not establish a usable sporting lane.
Bind review, certification, deployment and acceptance to exact revisions.
Verify authenticated real Action behavior, contract/editor parity, publishability,
candidate reconciliation and independent acceptance before claiming lane completion.
Never treat missing evidence as success.

## Authority and invariants
Engineering implements; independent Review challenges; Release authorizes deployment;
Independent QA verifies; SIRT independently audits; Conductor coordinates.
No self-approval, credential exposure, protection removal or unauthorized production promotion.
Preserve:
custom_gpt_identity=WOW_BETTING_ENGINE
runtime_generation=V17_ACTIVE
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true

## Scope and adoption
These instructions apply to repository agents that read this file after governed merge.
Until then this is a proposed contract. Cross-project settings and resident-worker adoption
require explicit changes and verification in their actual configuration.
Never label a document or chat promise as globally deployed enforcement.
