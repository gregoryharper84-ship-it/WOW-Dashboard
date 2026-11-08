import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import { tasks, runs } from "@trigger.dev/sdk";
import { assertDevPreflight } from "./start-dev-preflight.mjs";
import { patchTriggerCli } from "./patch-trigger-cli.mjs";
import { enqueueReadOnlyProbe } from "../src/lib/submit-readonly.mjs";

export const DEV_BRANCH = "wow-v17-github-actions-acceptance";

export function assertRemoteCliAuth(env = {}) {
  if (typeof env.TRIGGER_ACCESS_TOKEN !== "string" || !/^tr_pat_[A-Za-z0-9_-]+$/.test(env.TRIGGER_ACCESS_TOKEN)) {
    throw new Error("TRIGGER_CLI_PERSONAL_ACCESS_TOKEN_REQUIRED");
  }
  return true;
}

function appendTail(current, chunk, max = 5000) {
  return (current + String(chunk)).slice(-max);
}

export function sanitizeWorkerOutput(value, env = {}) {
  let output = String(value ?? "").replace(/\u001b\[[0-9;]*m/g, "");
  if (env.TRIGGER_SECRET_KEY) {
    output = output.split(env.TRIGGER_SECRET_KEY).join("[REDACTED_TRIGGER_KEY]");
  }
  if (env.TRIGGER_ACCESS_TOKEN) {
    output = output.split(env.TRIGGER_ACCESS_TOKEN).join("[REDACTED_TRIGGER_PAT]");
  }
  if (env.TRIGGER_PROJECT_REF) {
    output = output.split(env.TRIGGER_PROJECT_REF).join("[REDACTED_PROJECT_REF]");
  }
  return output
    .replace(/\btr_(?:dev|prod|stg|preview|pat|oat)_[A-Za-z0-9_-]+\b/gi, "[REDACTED_TRIGGER_CREDENTIAL]")
    .replace(/(Bearer\s+)[^\s]+/gi, "$1[REDACTED]")
    .replace(/(authorization\s*[:=]\s*)[^\r\n]+/gi, "$1[REDACTED]")
    .slice(-2500);
}

export function workerDiagnostic(worker, env = {}) {
  return {
    spawn_failed: Boolean(worker?.spawnFailed),
    spawn_error: sanitizeWorkerOutput(worker?.spawnError, env) || null,
    exit_code: worker?.exitCode ?? null,
    signal_code: worker?.signalCode ?? null,
    stdout_tail: sanitizeWorkerOutput(worker?.stdoutTail, env),
    stderr_tail: sanitizeWorkerOutput(worker?.stderrTail, env),
  };
}

const TERMINAL = new Set([
  "COMPLETED", "FAILED", "CANCELED", "CANCELLED", "CRASHED",
  "INTERRUPTED", "SYSTEM_FAILURE", "EXPIRED", "TIMED_OUT",
]);

const SAFE_FAILURES = new Set([
  "ENGINEERING_AGENT_PILOT_FLAG_REQUIRED",
  "DEV_BRANCH_MISMATCH",
  "TRIGGER_CLI_NOT_INSTALLED",
  "TRIGGER_CLI_PERSONAL_ACCESS_TOKEN_REQUIRED",
  "DEV_WORKER_SPAWN_FAILED",
  "DEV_WORKER_EXITED_EARLY",
  "TRIGGER_RUN_POLL_TIMEOUT",
  "TRIGGER_RUN_NOT_COMPLETED",
  "TRIGGER_RUN_OUTPUT_MISSING",
  "HEALTH_IDEMPOTENCY_MISMATCH",
  "HEALTH_REACHABILITY_NOT_PROVED",
  "SYNTHETIC_RETRY_NOT_PROVED",
  "DURABLE_CHECKPOINT_RESUME_NOT_PROVED",
]);

function outputOf(run) {
  let output = run?.output;
  if (typeof output === "string") {
    try { output = JSON.parse(output); } catch { output = null; }
  }
  if (!output || typeof output !== "object" || Array.isArray(output)) {
    throw new Error("TRIGGER_RUN_OUTPUT_MISSING");
  }
  return output;
}

export function assertCompletedRun(taskId, run) {
  if (!run || run.status !== "COMPLETED") {
    throw new Error("TRIGGER_RUN_NOT_COMPLETED");
  }
  return outputOf(run);
}

export function healthReceipt(run) {
  const output = assertCompletedRun("wow-v17-readonly-health-pilot", run);
  if (
    output.observation !== "HTTP_REACHABILITY_ONLY"
    || output.endpoint !== "/health/live"
    || output.http_status !== 200
    || typeof output.observed_at !== "string"
    || output.production_verified !== false
    || output.model_qualified !== false
    || output.can_execute !== false
  ) {
    throw new Error("HEALTH_REACHABILITY_NOT_PROVED");
  }
  return {
    run_id: run.id,
    status: run.status,
    http_status: output.http_status,
    observed_at: output.observed_at,
    observation: output.observation,
  };
}

export function syntheticRetryReceipt(run) {
  const output = assertCompletedRun("wow-v17-synthetic-retry-pilot", run);
  if (
    output.observation !== "SYNTHETIC_RETRY_ONLY"
    || output.attempts !== 3
    || output.production_verified !== false
    || output.model_qualified !== false
    || output.can_execute !== false
  ) {
    throw new Error("SYNTHETIC_RETRY_NOT_PROVED");
  }
  return { run_id: run.id, status: run.status, attempts: output.attempts };
}

export function recoveryReceipt(run) {
  const output = assertCompletedRun("wow-v17-engineering-recovery-intake", run);
  if (
    output.status !== "DURABLE_CHECKPOINT_RESUME_PROVED"
    || output.checkpoint?.requested !== true
    || output.checkpoint?.resumed_after_wait !== true
    || output.checkpoint?.source_unchanged !== true
    || output.checkpoint?.checkpoint_resume_proved !== true
    || output.handoff?.dispatched !== false
    || output.governance?.can_execute !== false
    || output.governance?.dry_run_only !== true
  ) {
    throw new Error("DURABLE_CHECKPOINT_RESUME_NOT_PROVED");
  }
  return {
    run_id: run.id,
    status: output.status,
    resumed_after_wait: output.checkpoint.resumed_after_wait,
    source_unchanged: output.checkpoint.source_unchanged,
    dispatched: output.handoff.dispatched,
    can_execute: output.governance.can_execute,
  };
}

export async function pollRun(runId, {
  timeoutMs,
  worker,
  retrieveRun = runs.retrieve,
  sleep = delay,
} = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (worker && (worker.spawnFailed || worker.exitCode !== null || worker.signalCode !== null)) {
      console.error("TRIGGER_DEV_WORKER_EXIT_DIAGNOSTIC", JSON.stringify(workerDiagnostic(worker, process.env)));
      throw new Error("DEV_WORKER_EXITED_EARLY");
    }
    const run = await retrieveRun(runId);
    if (TERMINAL.has(run.status)) return run;
    await sleep(1000);
  }
  throw new Error("TRIGGER_RUN_POLL_TIMEOUT");
}

function startWorker() {
  const cli = resolve(process.cwd(), "node_modules/.bin/trigger");
  if (!existsSync(cli)) throw new Error("TRIGGER_CLI_NOT_INSTALLED");

  const child = spawn(cli, ["dev", "--branch", DEV_BRANCH], {
    cwd: process.cwd(),
    env: {
      ...process.env,
      CI: "true",
      TRIGGER_DEV_BRANCH: DEV_BRANCH,
      TRIGGER_PREVIEW_BRANCH: DEV_BRANCH,
    },
    detached: process.platform !== "win32",
    stdio: ["ignore", "pipe", "pipe"],
  });

  child.spawnFailed = false;
  child.spawnError = null;
  child.stdoutTail = "";
  child.stderrTail = "";
  child.on("error", error => {
    child.spawnFailed = true;
    child.spawnError = error instanceof Error ? error.message : "unknown spawn error";
  });
  // Retain only bounded tails for redacted diagnostics if startup fails.
  child.stdout?.on("data", chunk => {
    child.stdoutTail = appendTail(child.stdoutTail, chunk);
  });
  child.stderr?.on("data", chunk => {
    child.stderrTail = appendTail(child.stderrTail, chunk);
  });
  return child;
}

async function stopWorker(worker) {
  if (!worker || worker.exitCode !== null || worker.signalCode !== null) return;
  try {
    if (process.platform !== "win32" && worker.pid) {
      process.kill(-worker.pid, "SIGTERM");
    } else {
      worker.kill("SIGTERM");
    }
  } catch {
    try { worker.kill("SIGTERM"); } catch {}
  }
  await Promise.race([
    new Promise(resolveExit => worker.once("exit", resolveExit)),
    delay(5000),
  ]);
  if (worker.exitCode === null && worker.signalCode === null) {
    try {
      if (process.platform !== "win32" && worker.pid) process.kill(-worker.pid, "SIGKILL");
      else worker.kill("SIGKILL");
    } catch {}
  }
}

async function main() {
  assertDevPreflight(process.env);
  assertRemoteCliAuth(process.env);
  if (process.env.WOW_TRIGGER_ENGINEERING_AGENT_ENABLED !== "true") {
    throw new Error("ENGINEERING_AGENT_PILOT_FLAG_REQUIRED");
  }
  if (
    (process.env.TRIGGER_DEV_BRANCH && process.env.TRIGGER_DEV_BRANCH !== DEV_BRANCH)
    || (process.env.TRIGGER_PREVIEW_BRANCH && process.env.TRIGGER_PREVIEW_BRANCH !== DEV_BRANCH)
  ) {
    throw new Error("DEV_BRANCH_MISMATCH");
  }

  process.env.TRIGGER_DEV_BRANCH = DEV_BRANCH;
  process.env.TRIGGER_PREVIEW_BRANCH = DEV_BRANCH;
  process.env.CI = "true";
  patchTriggerCli();

  const worker = startWorker();
  try {
    await delay(10000);
    if (worker.spawnFailed || worker.exitCode !== null || worker.signalCode !== null) {
      console.error("TRIGGER_DEV_WORKER_EXIT_DIAGNOSTIC", JSON.stringify(workerDiagnostic(worker, process.env)));
      throw new Error("DEV_WORKER_EXITED_EARLY");
    }

    const runSuffix = `${process.env.GITHUB_RUN_ID || "manual"}-${process.env.GITHUB_RUN_ATTEMPT || "1"}`;

    const probeId = `gha-${runSuffix}-health`;
    const submit = (taskId, payload, options) => tasks.trigger(taskId, payload, options);
    const firstHealth = await enqueueReadOnlyProbe(probeId, submit);
    const duplicateHealth = await enqueueReadOnlyProbe(probeId, submit);
    if (!firstHealth?.id || firstHealth.id !== duplicateHealth?.id) {
      throw new Error("HEALTH_IDEMPOTENCY_MISMATCH");
    }
    const healthRun = await pollRun(firstHealth.id, { timeoutMs: 90000, worker });
    const health = healthReceipt(healthRun);

    const retryProbeId = `gha-${runSuffix}-retry`;
    const retryHandle = await tasks.trigger(
      "wow-v17-synthetic-retry-pilot",
      { probeId: retryProbeId },
      {
        idempotencyKey: `wow-v17-synthetic-retry:${retryProbeId}`,
        idempotencyKeyTTL: "24h",
        ttl: "10m",
      },
    );
    if (!retryHandle?.id) throw new Error("SYNTHETIC_RETRY_NOT_PROVED");
    const retryRun = await pollRun(retryHandle.id, { timeoutMs: 90000, worker });
    const retry = syntheticRetryReceipt(retryRun);

    const recoveryHandle = await tasks.trigger(
      "wow-v17-engineering-recovery-intake",
      { incidentNumber: 2516, checkpointProbe: true },
      {
        idempotencyKey: `wow-v17-engineering-recovery:${runSuffix}`,
        idempotencyKeyTTL: "24h",
        ttl: "10m",
      },
    );
    if (!recoveryHandle?.id) throw new Error("DURABLE_CHECKPOINT_RESUME_NOT_PROVED");
    const recoveryRun = await pollRun(recoveryHandle.id, { timeoutMs: 240000, worker });
    const recovery = recoveryReceipt(recoveryRun);

    console.log("WOW_TRIGGER_REMOTE_DEV_ACCEPTANCE_RECEIPT");
    console.log(JSON.stringify({
      schema_version: "wow.v17.trigger.remote-dev-acceptance.v1",
      worker: {
        host: "GITHUB_ACTIONS",
        connected_task_execution_proved: true,
        development_branch: DEV_BRANCH,
      },
      environment: "DEVELOPMENT",
      tasks: {
        read_only_health: {
          ...health,
          duplicate_submission_same_run_id: true,
        },
        synthetic_retry: retry,
        engineering_recovery: recovery,
      },
      governance: {
        can_execute: false,
        dry_run_only: true,
        production_verified: false,
        model_qualified: false,
        github_writes: false,
        database_writes: false,
        coding_dispatch: false,
      },
    }, null, 2));
  } finally {
    await stopWorker(worker);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch(error => {
    const reason = error instanceof Error && SAFE_FAILURES.has(error.message)
      ? error.message
      : "TRIGGER_REMOTE_DEV_ACCEPTANCE_FAILED";
    console.error(reason);
    process.exitCode = 1;
  });
}
