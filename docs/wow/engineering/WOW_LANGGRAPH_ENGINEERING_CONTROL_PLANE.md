# WOW V17 LangGraph Engineering Control Plane

Status: proposed Class B orchestration implementation  
Probability authority: **NONE**  
Execution authority: **NONE**  
`can_execute=false`  
`DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`

## Purpose

LangGraph is the durable workflow engine for the existing WOW engineering team. It does **not** replace the V17 sporting-model architecture and does not calculate, modify, rank, or publish sporting probabilities.

The graph converts the existing engineering lifecycle into explicit state transitions so that an issue can resume from its last proven stage, run safe evidence work in parallel, retry bounded repairs, and terminate only in an allowed engineering disposition.

## Graph

```text
START
  -> REPORTER_INTAKE
  -> RESEARCH_TRIAGE
       |-> DIAGNOSTICS --|
       |-> DATA_AUDIT ---| -> ROOT_CAUSE
                              -> ENGINEERING
                              -> SANDBOX_TEST
                                   | FAIL and attempt < 3 -> ENGINEERING
                                   | FAIL at attempt 3 -> BLOCKED_WITH_EXACT_REASON
                                   ` PASS
                                     -> V17_GOVERNANCE
                                     -> INDEPENDENT_REVIEW
                                     -> GOVERNANCE_GATE
                                          | Class C -> EXPERIMENT_CREATED
                                          | no promotion authority -> PR_CREATED
                                          ` authorized
                                            -> RELEASE_OBSERVABILITY
                                            -> QA_VERIFICATION
                                            -> REPORTER_CLOSURE
```

Diagnostics and Data Audit are separate because a scorer-looking incident can actually be an identity, hydration, provider, persistence, or stale-data defect. LangGraph joins both lanes before root-cause synthesis.

## State and durability

Every canonical issue uses `issue_id` as the LangGraph `thread_id`. Tests use `InMemorySaver`. Production durable mode uses LangGraph `PostgresSaver` with `WOW_ENGINEERING_CHECKPOINT_DB_URI` supplied through the runtime secret environment. The connection string must never be printed or placed in graph state.

`LANGGRAPH_STRICT_MSGPACK=true` is enabled by the durable factory unless an operator has already explicitly configured that environment variable.

The production checkpointer creates LangGraph checkpoint tables through `PostgresSaver.setup()`. Activation therefore requires governed review of the target database/role and least-privilege permissions before first production use.

## Worker adapters

The graph owns orchestration; workers remain replaceable adapters:

- Reporter / intake
- Research / triage
- Diagnostics
- Data Audit
- Root-cause synthesis
- Engineering / patch generation
- Sandbox test runner
- Independent review
- Release / observability
- Production QA

This separation prevents the workflow engine from becoming a self-approving engineering agent.

## Bounded repair loop

The controller permits exactly **three** engineer -> sandbox-test attempts. A failing test result is returned to Engineering through graph state. Attempt three failing is terminal for that cycle:

`BLOCKED_WITH_EXACT_REASON`

The state must also include the smallest remaining action. Re-triage is required instead of continuing an unbounded patch loop.

## V17 governance gate

Before independent review, the controller verifies:

- `custom_gpt_identity=WOW_BETTING_ENGINE`
- `runtime_generation=V17_ACTIVE`
- `terminal_authority=V17_TERMINAL_REDUCER`
- `can_execute=false`
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`
- engineering `probability_authority=NONE`
- repair-attempt bound remains three
- no top-level probability/wager/order output has entered the engineering control-plane state

The graph does not replace the sporting terminal reducer. Its terminal labels are engineering-lifecycle dispositions only.

## Change-class handling

- **Class A:** may proceed through release only when promotion authority is explicitly supplied by the governed caller.
- **Class B:** defaults to `PR_CREATED`; production promotion is never inferred.
- **Class C:** always exits the control plane as `EXPERIMENT_CREATED`. The graph never routes Class C directly to production release.

## Ticket parking and TTL wake-ups

Engineering-ticket parking is a Class B outer-orchestration concern. It uses the existing `public.wow_engineering_backlog` table but keeps its execution state in a separate `queue_status` column so the established lifecycle values in `status` (`OPEN`, `IN_PROGRESS`, `BLOCKED`, `CLOSED`) remain compatible with existing auditor/reporting code.

The execution states are:

- `ACTIONABLE`
- `IN_PROGRESS`
- `PARKED`
- `COMPLETED`
- `FAILED`

A parked row carries `parked_reason`, `parked_context`, `parked_until`, and `wake_count`. The current TTL policy is:

| parked reason | base TTL |
|---|---:|
| `api_rate_limit` | 15 minutes |
| `upstream_dependency` | 1 hour |
| `awaiting_pr_review` | 4 hours |
| unknown reason | 1 hour |

The multiplier is `min(wake_count + 1, 6)`. `awaiting_pr_review`, for example, sleeps 4h, 8h, 12h, 16h, 20h, then 24h. At six wakes the row fails closed as `BLOCKED_WITH_EXACT_REASON` and requires V17 governance/human intervention rather than cycling forever.

Wake-up does **not** require cron and there is no process that flips `PARKED` back to `ACTIONABLE`. The orchestrator's normal polling operation calls `wow_claim_engineering_tickets`, which selects either an actionable row or a parked row whose `parked_until <= CURRENT_TIMESTAMP` and atomically claims it with `FOR UPDATE SKIP LOCKED`.

The claim RPC also writes `queue_status=IN_PROGRESS`, `claim_owner`, and `claimed_at` before its transaction releases row locks. This is stricter than returning a locked row from a short RPC transaction: a returned database row lock disappears at transaction end, while persisted claim ownership prevents a second orchestrator from acquiring the same ticket during the external API checks that follow.

Terminal engineering tickets are excluded even if legacy lifecycle data is inconsistent. Legacy `BLOCKED` rows without a typed parking reason are also not automatically woken; they backfill to queue `FAILED` rather than manufacturing a retry policy.

When a claimed row was previously parked, blocker re-evaluation is mandatory before graph execution:

1. `BLOCKED`: park it again immediately and increment `wake_count`; the next TTL uses the larger multiplier.
2. `CLEARED`: clear parking metadata and reset `wake_count` to zero.
3. `FAILED`: close the queue item as `BLOCKED_WITH_EXACT_REASON` with escalation evidence.
4. Provider/transport failure during the check: re-park the ticket with the original typed blocker and record the check error type in parking context; do not strand the claim or translate the failure into a sporting `MODEL_UNAVAILABLE` state.

A cleared ticket deliberately remains `IN_PROGRESS` under the existing atomic claim while it is handed to LangGraph. Setting it to `ACTIONABLE` and then executing would reopen the exact multi-worker claim race that the queue is designed to prevent.

For `awaiting_pr_review`, the queue adapter stores the provider reference in `parked_context.pr_id`. `APPROVED` by itself remains blocked; `MERGED` clears the blocker. `CHANGES_REQUESTED` also clears the waiting state so the engineering graph can resume remediation. The Git provider adapter is injected and credentials/API payloads never enter queue state.

The intended orchestrator cadence is hourly. That cadence is owned by the orchestrator runtime, not by a database cron, Celery beat schedule, or separate wake-up worker.

## Tracing

Every node emits a structured trace event containing:

- issue ID
- stage
- status
- UTC timestamp
- repair attempt count
- compact detail

The state retains these events and the adapter layer exposes a `trace_sink` hook. That hook can be connected to OpenTelemetry without coupling V17 probability code to an observability vendor.

## Synthetic regression fixtures

The initial control-plane regression suite covers:

- canonical event-identity mismatch
- provider timeout / transport-style failure
- malformed odds payload (`+1500` vs `+150` class)
- future-information leakage marker
- missing calibration artifact

These are capture/test fixtures only. They are never sporting recommendations or probability sources.

## Activation gate

This implementation is Class B. Merge/deploy is governed. Repository acceptance requires:

1. LangGraph and Postgres-checkpoint dependencies installed.
2. Control-plane and TTL queue tests green.
3. Existing DOTS/V17 orchestrator contract tests remain green.
4. The queue migration is transactionally validated against the governed Supabase schema before production apply.
5. Independent review confirms no probability behavior changed.
6. Production checkpoint database role/URI configured through secret management.
7. Exact deployed SHA verified after any approved deployment.

Until those gates are satisfied, terminal status for this implementation is `PR_CREATED`, not `FIXED_AND_VERIFIED`.
