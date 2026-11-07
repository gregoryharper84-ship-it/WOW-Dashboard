import { task, AbortTaskRunError } from "@trigger.dev/sdk";
import { assertPilotSafety } from "../lib/read-only-health.mjs";
import { idempotencyKeyFor } from "../lib/submit-readonly.mjs";

/**
 * Purely synthetic and DEV-only retry exercise. No fetch, DB, tokens, scoring,
 * scheduling, or production side effects. Attempt #1/#2 fail; #3 succeeds.
 * The Trigger.dev platform, not local loop logic, owns the retries.
 */
export const wowV17SyntheticRetryPilot = task({
  id: "wow-v17-synthetic-retry-pilot",
  concurrency: { total: 1 },
  maxDuration: 30,
  retry: {
    maxAttempts: 3,
    minTimeoutInMs: 1000,
    maxTimeoutInMs: 5000,
    factor: 2,
    randomize: false,
  },
  run: async (payload: { probeId: string }, { ctx }) => {
    try {
      assertPilotSafety();
      idempotencyKeyFor(payload?.probeId);
    } catch {
      throw new AbortTaskRunError("SYNTHETIC_RETRY_DEV_GUARD_FAILED");
    }
    const attempt = ctx.attempt.number;
    if (attempt === 1 || attempt === 2) {
      throw new Error("SYNTHETIC_TRANSIENT_FOR_RETRY_TEST");
    }
    if (attempt !== 3) {
      throw new AbortTaskRunError("UNEXPECTED_SYNTHETIC_ATTEMPT");
    }
    return {
      observation: "SYNTHETIC_RETRY_ONLY",
      attempts: attempt,
      production_verified: false,
      model_qualified: false,
      can_execute: false,
    };
  },
});
