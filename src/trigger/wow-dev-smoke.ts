import { task } from "@trigger.dev/sdk";

/**
 * Development-only integration smoke test.
 *
 * This task performs no betting, trading, modeling, deployment or release
 * operations. Register and run it before adopting Trigger.dev for WOW jobs.
 */
export const wowTriggerDevSmoke = task({
  id: "wow-trigger-dev-smoke",
  maxDuration: 60,
  run: async (payload: { message?: string }) => {
    const message = payload.message?.trim() || "WOW V17 Trigger.dev is responding";
    return {
      status: "ok" as const,
      message,
      generation: "V17" as const,
      can_execute: false as const,
      checked_at: new Date().toISOString(),
    };
  },
});
