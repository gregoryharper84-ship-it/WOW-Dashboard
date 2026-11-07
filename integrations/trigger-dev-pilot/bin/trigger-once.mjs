import { tasks } from "@trigger.dev/sdk";
import { enqueueReadOnlyProbe } from "../src/lib/submit-readonly.mjs";

// The CLI is an intentional operator action; refuse production keys.
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
const probeId = process.argv[2];
const handle = await enqueueReadOnlyProbe(probeId, (taskId, payload, options) =>
  tasks.trigger(taskId, payload, options)
);
console.log(JSON.stringify({ probe_id: probeId, run_id: handle.id, environment: "dev" }));
