import { task, AbortTaskRunError } from "@trigger.dev/sdk";
import { observeWowHealth, ProbeError } from "../lib/read-only-health.mjs";
import { idempotencyKeyFor } from "../lib/submit-readonly.mjs";

/**
 * Manually triggered DEVELOPMENT-only pilot. Never scheduled; no API auth,
 * Supabase access, score/settlement calls, or production worker replacement.
 */
export const wowV17ReadonlyHealthPilot = task({
  id: "wow-v17-readonly-health-pilot",
  concurrency: { total: 1 },
  maxDuration: 30,
  retry: {
    maxAttempts: 3,
    minTimeoutInMs: 1000,
    maxTimeoutInMs: 5000,
    factor: 2,
    randomize: false,
  },
  run: async (payload: { probeId: string }) => {
    try {
      // Validates the same stable key contract used at enqueue time.
      idempotencyKeyFor(payload?.probeId);
      return await observeWowHealth();
    } catch (error) {
      // Non-transient failures must never retry or be mistaken for recovery.
      if (error instanceof ProbeError && !error.retryable) {
        throw new AbortTaskRunError(error.code);
      }
      if (error instanceof TypeError) {
        throw new AbortTaskRunError("INVALID_PROBE_ID");
      }
      throw error;
    }
  },
});
