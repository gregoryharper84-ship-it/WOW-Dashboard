import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  assertCompletedRun,
  assertRemoteCliAuth,
  sanitizeWorkerOutput,
  workerDiagnostic,
  healthReceipt,
  syntheticRetryReceipt,
  recoveryReceipt,
  DEV_BRANCH,
} from "../scripts/run-remote-dev-acceptance.mjs";

test("remote worker is a named DEVELOPMENT branch", () => {
  assert.equal(DEV_BRANCH, "wow-v17-github-actions-acceptance");
});

test("health receipt requires a real fixed endpoint HTTP 200 and no-execution output", () => {
  const run = {
    id: "run_health",
    status: "COMPLETED",
    output: {
      observation: "HTTP_REACHABILITY_ONLY",
      endpoint: "/health/live",
      http_status: 200,
      observed_at: "2026-11-08T20:00:00.000Z",
      production_verified: false,
      model_qualified: false,
      can_execute: false,
    },
  };
  assert.deepEqual(healthReceipt(run), {
    run_id: "run_health",
    status: "COMPLETED",
    http_status: 200,
    observed_at: "2026-11-08T20:00:00.000Z",
    observation: "HTTP_REACHABILITY_ONLY",
  });
  assert.throws(() => healthReceipt({ ...run, output: { ...run.output, http_status: 503 } }), /HEALTH_REACHABILITY_NOT_PROVED/);
});

test("synthetic retry receipt requires exactly three platform attempts", () => {
  const run = {
    id: "run_retry",
    status: "COMPLETED",
    output: {
      observation: "SYNTHETIC_RETRY_ONLY",
      attempts: 3,
      production_verified: false,
      model_qualified: false,
      can_execute: false,
    },
  };
  assert.deepEqual(syntheticRetryReceipt(run), {
    run_id: "run_retry",
    status: "COMPLETED",
    attempts: 3,
  });
  assert.throws(() => syntheticRetryReceipt({ ...run, output: { ...run.output, attempts: 2 } }), /SYNTHETIC_RETRY_NOT_PROVED/);
});

test("recovery receipt requires resumed 65-second checkpoint, unchanged source and no dispatch", () => {
  const run = {
    id: "run_recovery",
    status: "COMPLETED",
    output: {
      status: "DURABLE_CHECKPOINT_RESUME_PROVED",
      checkpoint: {
        requested: true,
        resumed_after_wait: true,
        source_unchanged: true,
        checkpoint_resume_proved: true,
      },
      handoff: { dispatched: false },
      governance: { can_execute: false, dry_run_only: true },
    },
  };
  assert.deepEqual(recoveryReceipt(run), {
    run_id: "run_recovery",
    status: "DURABLE_CHECKPOINT_RESUME_PROVED",
    resumed_after_wait: true,
    source_unchanged: true,
    dispatched: false,
    can_execute: false,
  });
  assert.throws(() => recoveryReceipt({ ...run, output: { ...run.output, handoff: { dispatched: true } } }), /DURABLE_CHECKPOINT_RESUME_NOT_PROVED/);
});

test("failed or incomplete Trigger runs are never accepted", () => {
  assert.throws(() => assertCompletedRun("task", { status: "FAILED" }), /TRIGGER_RUN_NOT_COMPLETED/);
  assert.throws(() => assertCompletedRun("task", { status: "COMPLETED", output: null }), /TRIGGER_RUN_OUTPUT_MISSING/);
});

test("remote runner uses only DEV worker and three explicitly bounded pilot tasks", () => {
  const source = readFileSync(new URL("../scripts/run-remote-dev-acceptance.mjs", import.meta.url), "utf8");
  assert.match(source, /assertDevPreflight\(process\.env\)/);
  assert.match(source, /assertRemoteCliAuth\(process\.env\)/);
  assert.match(source, /patchTriggerCli\(\)/);
  assert.match(source, /\["dev", "--branch", DEV_BRANCH\]/);
  assert.match(source, /TRIGGER_PREVIEW_BRANCH = DEV_BRANCH/);
  assert.match(source, /wow-v17-readonly-health-pilot/);
  assert.match(source, /wow-v17-synthetic-retry-pilot/);
  assert.match(source, /wow-v17-engineering-recovery-intake/);
  assert.match(source, /timeoutMs: 240000/);
  assert.doesNotMatch(source, /deploy\s+--env|schedules\.task|cron\s*\(/i);
});

test("remote worker workflow is opt-in, same-repository PR only and read-only", () => {
  const workflow = readFileSync(new URL("../../../.github/workflows/wow-trigger-remote-dev-acceptance.yml", import.meta.url), "utf8");
  assert.match(workflow, /pull_request:/);
  assert.match(workflow, /workflow_dispatch:/);
  assert.match(workflow, /vars\.WOW_TRIGGER_REMOTE_DEV_ENABLED == 'true'/);
  assert.match(workflow, /pull_request\.head\.repo\.full_name == github\.repository/);
  assert.match(workflow, /environment: wow-trigger-dev/);
  assert.match(workflow, /secrets\.TRIGGER_SECRET_KEY/);
  assert.match(workflow, /secrets\.TRIGGER_ACCESS_TOKEN/);
  assert.match(workflow, /permissions:\s*contents: read/);
  assert.doesNotMatch(workflow, /pull_request_target:|schedule:|contents:\s*write/i);
});


test("worker diagnostics redact Trigger keys, project references and bearer tokens", () => {
  const secret = "tr_dev_NEVER_LOG_THIS_KEY";
  const project = "proj_NEVER_LOG_THIS_REFERENCE";
  const value = `auth=${secret} project=${project} Bearer abc123 tr_prod_OTHER_SECRET`;
  const safe = sanitizeWorkerOutput(value, {
    TRIGGER_SECRET_KEY: secret,
    TRIGGER_PROJECT_REF: project,
  });
  assert.ok(!safe.includes(secret));
  assert.ok(!safe.includes(project));
  assert.ok(!safe.includes("abc123"));
  assert.ok(!safe.includes("tr_prod_OTHER_SECRET"));
  assert.match(safe, /REDACTED_TRIGGER_KEY/);
  assert.match(safe, /REDACTED_PROJECT_REF/);
});

test("worker diagnostic is bounded and reports exit metadata without environment values", () => {
  const worker = {
    spawnFailed: false,
    spawnError: null,
    exitCode: 1,
    signalCode: null,
    stdoutTail: "x".repeat(6000),
    stderrTail: "startup failed",
  };
  const diagnostic = workerDiagnostic(worker, {
    TRIGGER_SECRET_KEY: "tr_dev_hidden",
    TRIGGER_PROJECT_REF: "proj_hidden",
  });
  assert.equal(diagnostic.exit_code, 1);
  assert.equal(diagnostic.signal_code, null);
  assert.ok(diagnostic.stdout_tail.length <= 2500);
  assert.equal(diagnostic.stderr_tail, "startup failed");
  assert.equal(JSON.stringify(diagnostic).includes("tr_dev_hidden"), false);
  assert.equal(JSON.stringify(diagnostic).includes("proj_hidden"), false);
});


test("remote CLI preflight requires a Trigger.dev Personal Access Token, not an environment API key", () => {
  assert.equal(assertRemoteCliAuth({ TRIGGER_ACCESS_TOKEN: "tr_pat_test_only" }), true);
  for (const value of [
    undefined,
    "",
    "tr_dev_sk_environment_key",
    "tr_prod_sk_environment_key",
    "tr_preview_sk_environment_key",
    "tr_oat_internal_token",
  ]) {
    assert.throws(
      () => assertRemoteCliAuth({ TRIGGER_ACCESS_TOKEN: value }),
      /TRIGGER_CLI_PERSONAL_ACCESS_TOKEN_REQUIRED/,
    );
  }
});
