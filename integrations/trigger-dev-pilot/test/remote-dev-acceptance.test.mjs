import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  assertCompletedRun,
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
  assert.match(source, /patchTriggerCli\(\)/);
  assert.match(source, /\["dev", "--branch", DEV_BRANCH\]/);
  assert.match(source, /TRIGGER_PREVIEW_BRANCH = DEV_BRANCH/);
  assert.match(source, /wow-v17-readonly-health-pilot/);
  assert.match(source, /wow-v17-synthetic-retry-pilot/);
  assert.match(source, /wow-v17-engineering-recovery-intake/);
  assert.match(source, /timeoutMs: 240000/);
  assert.doesNotMatch(source, /TRIGGER_ACCESS_TOKEN|deploy\s+--env|schedules\.task|cron\s*\(/i);
});

test("remote worker workflow is opt-in, same-repository PR only and read-only", () => {
  const workflow = readFileSync(new URL("../../.github/workflows/wow-trigger-remote-dev-acceptance.yml", import.meta.url), "utf8");
  assert.match(workflow, /pull_request:/);
  assert.match(workflow, /workflow_dispatch:/);
  assert.match(workflow, /vars\.WOW_TRIGGER_REMOTE_DEV_ENABLED == 'true'/);
  assert.match(workflow, /pull_request\.head\.repo\.full_name == github\.repository/);
  assert.match(workflow, /environment: wow-trigger-dev/);
  assert.match(workflow, /secrets\.TRIGGER_SECRET_KEY/);
  assert.match(workflow, /permissions:\s*contents: read/);
  assert.doesNotMatch(workflow, /pull_request_target:|schedule:|contents:\s*write/i);
});
