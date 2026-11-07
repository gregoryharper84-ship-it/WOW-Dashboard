/**
 * Side-effect-free WOW production health observation.
 * Fixed host and fixed path: no user-controlled URL, POST, credentials,
 * publication, model scoring, DB reads, settlement, or scheduler mutations.
 */
export const HEALTH_URL = "https://wow-governed-probability-engine.onrender.com/health/live";

export class ProbeError extends Error {
  constructor(code, retryable) {
    super(code);
    this.name = "ProbeError";
    this.code = code;
    this.retryable = retryable;
  }
}

export function assertPilotSafety(env = process.env) {
  if (env.WOW_TRIGGER_PILOT_ENABLED !== "true") {
    throw new ProbeError("PILOT_DISABLED", false);
  }
  if (env.WOW_TRIGGER_PILOT_ENV !== "dev") {
    throw new ProbeError("NOT_DEV_PILOT", false);
  }
  if (env.WOW_CAN_EXECUTE !== "false" || env.WOW_DRY_RUN_ONLY !== "true") {
    throw new ProbeError("EXECUTION_GUARD_NOT_PROVEN", false);
  }
}

export async function observeWowHealth({
  fetchImpl = globalThis.fetch,
  env = process.env,
  now = () => new Date().toISOString(),
} = {}) {
  assertPilotSafety(env);
  // Exactly one request per attempt. Retrying is delegated to Trigger.dev;
  // the request itself is a safe, read-only GET.
  let response;
  try {
    response = await fetchImpl(HEALTH_URL, {
      method: "GET",
      headers: { accept: "application/json" },
      redirect: "error",
      signal: AbortSignal.timeout(8000),
    });
  } catch (error) {
    if (error && typeof error === "object" && error.name === "TimeoutError") {
      throw new ProbeError("HEALTH_TIMEOUT", true);
    }
    // A redirect refusal and a network transport error are not always
    // distinguishable in fetch. Failures never turn into successful receipts.
    throw new ProbeError("HEALTH_TRANSPORT_UNAVAILABLE", true);
  }
  if (!response || typeof response.status !== "number") {
    throw new ProbeError("INVALID_HEALTH_RESPONSE", true);
  }
  if (response.status === 429 || response.status >= 500) {
    throw new ProbeError(`HEALTH_HTTP_${response.status}`, true);
  }
  if (response.status < 200 || response.status >= 300) {
    throw new ProbeError(`HEALTH_HTTP_${response.status}`, false);
  }
  return Object.freeze({
    observation: "HTTP_REACHABILITY_ONLY",
    endpoint: "/health/live",
    http_status: response.status,
    observed_at: now(),
    production_verified: false,
    model_qualified: false,
    can_execute: false,
  });
}
