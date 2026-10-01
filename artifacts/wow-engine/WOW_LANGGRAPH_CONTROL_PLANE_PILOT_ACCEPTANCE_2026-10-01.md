# WOW LangGraph Engineering Control Plane — Pilot Acceptance Receipt

Date: 2026-10-01  
Change class: **B — engineering/orchestration**  
PR: **#1154**  
Tested code head before this receipt-only commit: `0902ca0806a924f01e1c49e968a0b052aaba0c44`  
Dedicated validation run: `36931386492`  
Current lifecycle disposition: **PR_CREATED**

## Scope accepted by the pilot

The pilot verifies the repository implementation of the WOW V17 LangGraph engineering control plane. It does not authorize production promotion and does not alter sporting probability behavior.

Verified behavior:

- Reporter/Triage enters a deterministic LangGraph workflow.
- Diagnostics and Data Audit fan out as independent evidence lanes and rejoin before root-cause synthesis.
- Engineering -> Sandbox Test is bounded to exactly three repair attempts.
- Exhausted repair attempts terminate `BLOCKED_WITH_EXACT_REASON` with a smallest remaining action.
- A checkpointed node failure can resume on the same `issue_id` without replaying completed upstream work.
- Class B defaults to `PR_CREATED` unless governed promotion authority is explicitly supplied.
- Class C terminates `EXPERIMENT_CREATED` and cannot route directly to release.
- Production closure requires Release plus QA plus deployed-artifact verification.
- New-issue payloads cannot pre-seed terminal/review/deployment state.
- Worker adapters cannot overwrite control-plane authority fields.
- Probability/wager/order fields are recursively rejected from control-plane state.
- LangGraph has `probability_authority=NONE` and `can_execute=false`.
- PostgresSaver durable mode uses a secret-supplied `WOW_ENGINEERING_CHECKPOINT_DB_URI` and strict msgpack mode.
- OpenTelemetry-compatible structured engineering traces are available through the trace sink.
- Synthetic regression fixtures cover event identity conflict, provider timeout, malformed market data, future-information leakage, and missing calibration artifacts.

## Validation evidence

GitHub Actions workflow `WOW Engineering Control Plane`, run `36931386492`, completed successfully against tested code head `0902ca0806a924f01e1c49e968a0b052aaba0c44`.

The successful job installed the pinned LangGraph/Postgres checkpoint/OpenTelemetry test dependencies and executed:

- `test_wow_engineering_control_plane.py`
- `test_wow_engineering_telemetry.py`
- existing `test_v17_dots_engineering_orchestrator_contract.py`

The final PR head is required to rerun the same checks; this receipt is not a substitute for current-head CI.

## V17 invariants preserved

- `custom_gpt_identity=WOW_BETTING_ENGINE`
- `runtime_generation=V17_ACTIVE`
- `terminal_authority=V17_TERMINAL_REDUCER`
- `can_execute=false`
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`
- exactly one controlling fitted sporting specialist remains the probability owner
- no sportsbook, Scout, Research, external projection, recent-result, or generic-LLM probability substitution
- typed V17 failures remain distinct
- no engineering worker self-approves its own implementation

## Remaining governed activation gates

This pilot is repository-accepted only after the PR's current-head checks are green and the Class B review/merge process is completed.

Production activation additionally requires:

1. a least-privilege PostgreSQL checkpoint role/database selected and `WOW_ENGINEERING_CHECKPOINT_DB_URI` configured through secret management;
2. the existing autonomous engineering host to instantiate the LangGraph control plane with real Reporter/Triage/Diagnostics/Data-Audit/Engineering/Review/Release/QA adapters;
3. production observability exporter/provider configuration if trace export is desired;
4. exact deployed SHA verification after governed deployment;
5. a production pilot issue replay proving resume, bounded repair, governance, and closure behavior without probability or execution authority drift.

Until those gates are satisfied, this change remains **PR_CREATED**, not `FIXED_AND_VERIFIED`.
