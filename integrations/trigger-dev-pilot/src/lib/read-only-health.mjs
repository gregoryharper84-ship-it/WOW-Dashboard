/**
 * Fixed, unauthenticated liveness endpoint used only for a bounded read-only observation.
 * No caller-supplied URL, authorization header, database, scoring, settlement, or write path.
 */
export const HEALTH_URL = "https://wow-governed-probability-engine.onrender.com/health/live";

/** Typed failure carrying whether Trigger.dev may retry the probe. */
export class ProbeError extends Error {
  constructor(code, retryable, cause) {
    super(code, cause === undefined ? undefined : { cause });
    this.name = "ProbeError";
    this.code = code;
    this.retryable = retryable;
  }
}

/** Enforce explicit pilot, DEV, and no-execution flags before any network request. */
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

/**
 * Perform exactly one fixed GET and return reachability-only evidence.
 * Trigger.dev owns retries; redirects and permanent HTTP failures are never retried.
 */
export async function observeWowHealth({
  fetchImpl = globalThis.fetch,
  env = process.env,
  now = () => new Date().toISOString(),
} = {}) {
  assertPilotSafety(env);
  if (typeof fetchImpl !== "function") {
    throw new ProbeError("FETCH_UNAVAILABLE", false);
  }

  // Exactly one request per attempt. Redirects are surfaced, never followed.
  let response;
  try {
    response = await fetchImpl(HEALTH_URL, {
      method: "GET",
      headers: { accept: "application/json" },
      redirect: "manual",
      signal: AbortSignal.timeout(8000),
    });
  } catch (error) {
    if (error && typeof error === "object" && error.name === "TimeoutError") {
      throw new ProbeError("HEALTH_TIMEOUT", true, error);
    }
    throw new ProbeError("HEALTH_TRANSPORT_UNAVAILABLE", true, error);
  }

  if (!response || !Number.isInteger(response.status) || response.status < 100 || response.status > 599) {
    throw new ProbeError("INVALID_HEALTH_RESPONSE", false);
  }

  // Do not leave an unread response stream/connection attached to the worker.
  try {
    await response.body?.cancel();
  } catch (error) {
    throw new ProbeError("HEALTH_BODY_CANCEL_FAILED", true, error);
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
