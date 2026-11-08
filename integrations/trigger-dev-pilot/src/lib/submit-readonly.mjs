// This is a DEVELOPMENT-only invocation contract; it does not call production.
export const TASK_ID = "wow-v17-readonly-health-pilot";

/** Validate a probe ID and derive its stable Trigger.dev idempotency key. */
export function idempotencyKeyFor(probeId) {
  if (typeof probeId !== "string" || !/^[A-Za-z0-9._-]{1,80}$/.test(probeId)) {
    throw new TypeError("probeId must be 1-80 safe ASCII characters");
  }
  return `wow-v17-readonly-health:${probeId}`;
}

/** Submit a single DEV probe with a 24-hour deduplication key and bounded queue TTL. */
export async function enqueueReadOnlyProbe(probeId, triggerFn) {
  if (typeof triggerFn !== "function") throw new TypeError("triggerFn is required");
  const idempotencyKey = idempotencyKeyFor(probeId);
  return triggerFn(TASK_ID, { probeId }, {
    idempotencyKey,
    idempotencyKeyTTL: "24h",
    ttl: "10m",
  });
}
