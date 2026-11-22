import { tasks } from "@trigger.dev/sdk";
import { enqueueReadOnlyProbe, idempotencyKeyFor } from "../src/lib/submit-readonly.mjs";

const SAFE_ERRORS = new Set([
  "DEV_PILOT_GUARDS_REQUIRED",
  "DEV_ENV_ONLY",
  "DEV_TRIGGER_SECRET_KEY_REQUIRED",
  "INVALID_PROBE_ID",
  "TRIGGER_RUN_ID_MISSING",
]);

/** Run one explicitly requested DEV-only health probe and print a safe receipt. */
async function main() {
  const probeId = process.argv[2];
  if (typeof probeId !== "string" || probeId.length === 0) {
    console.error("Usage: node bin/trigger-once.mjs <probe-id>");
    process.exitCode = 2;
    return;
  }

  try {
    try {
      idempotencyKeyFor(probeId);
    } catch {
      throw new Error("INVALID_PROBE_ID");
    }

    if (process.env.WOW_TRIGGER_PILOT_ENABLED !== "true" ||
        process.env.WOW_CAN_EXECUTE !== "false" ||
        process.env.WOW_DRY_RUN_ONLY !== "true") {
      throw new Error("DEV_PILOT_GUARDS_REQUIRED");
    }
    if (process.env.WOW_TRIGGER_PILOT_ENV !== "dev") {
      throw new Error("DEV_ENV_ONLY");
    }

    const secret = process.env.TRIGGER_SECRET_KEY ?? "";
    if (!secret.startsWith("tr_dev_")) {
      throw new Error("DEV_TRIGGER_SECRET_KEY_REQUIRED");
    }

    const handle = await enqueueReadOnlyProbe(probeId, (taskId, payload, options) =>
      tasks.trigger(taskId, payload, options)
    );
    if (!handle || typeof handle.id !== "string" || handle.id.length === 0) {
      throw new Error("TRIGGER_RUN_ID_MISSING");
    }
    console.log(JSON.stringify({ probe_id: probeId, run_id: handle.id, environment: "dev" }));
  } catch (error) {
    const code = error instanceof Error && SAFE_ERRORS.has(error.message)
      ? error.message
      : "TRIGGER_SUBMISSION_FAILED";
    console.error(code);
    process.exitCode = 1;
  }
}

await main();
