import { task } from "@trigger.dev/sdk";
import { probeWowRuntime } from "./wow-v17-recovery-probe";

/**
 * P0 first-stage read-only recovery sentinel.
 * Manual triggers only until independent acceptance authorizes a schedule.
 * It cannot dispatch writers, create incidents, merge, deploy, score, or wager.
 */
export const wowV17RecoverySentinel = task({
  id: "wow-v17-recovery-sentinel",
  maxDuration: 30,
  retry: { maxAttempts: 1 },
  queue: { concurrencyLimit: 1 },
  run: async () => probeWowRuntime(),
});
