import { patchTriggerCli } from "./patch-trigger-cli.mjs";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

/** Require a real DEV-only Trigger environment before the local worker can start. */
export function assertDevPreflight(env = process.env) {
  if (typeof env.TRIGGER_PROJECT_REF !== "string" || !/^proj_[A-Za-z0-9_-]+$/.test(env.TRIGGER_PROJECT_REF)) {
    throw new Error("TRIGGER_PROJECT_REF_REQUIRED");
  }
  if (typeof env.TRIGGER_SECRET_KEY !== "string" || !env.TRIGGER_SECRET_KEY.startsWith("tr_dev_")) {
    throw new Error("DEV_TRIGGER_SECRET_KEY_REQUIRED");
  }
  const required = {
    WOW_TRIGGER_PILOT_ENABLED: "true",
    WOW_TRIGGER_PILOT_ENV: "dev",
    WOW_CAN_EXECUTE: "false",
    WOW_DRY_RUN_ONLY: "true",
  };
  for (const [name, value] of Object.entries(required)) {
    if (env[name] !== value) {
      throw new Error(`DEV_PILOT_GUARD_REQUIRED:${name}`);
    }
  }
  return true;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    assertDevPreflight();
    const result = patchTriggerCli();
    console.log(`TRIGGER_DEV_PREFLIGHT_PASS_${result.status.toUpperCase()}_V${result.version}`);
  } catch (error) {
    console.error(error instanceof Error ? error.message : "TRIGGER_DEV_PREFLIGHT_FAILED");
    process.exitCode = 1;
  }
}
