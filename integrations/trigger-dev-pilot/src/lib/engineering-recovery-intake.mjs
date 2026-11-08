import { createHash } from "node:crypto";

export const ENGINEERING_REPO = "gregoryharper84-ship-it/WOW-Dashboard";
const ISSUE_API_PREFIX = "https://api.github.com/repos/" + ENGINEERING_REPO + "/issues/";

export class EngineeringIntakeError extends Error {
  constructor(code, retryable = false) {
    super(code);
    this.name = "EngineeringIntakeError";
    this.code = code;
    this.retryable = retryable;
  }
}

/**
 * @param {{triggerEnvironmentType?:string, env?:NodeJS.ProcessEnv}} options
 */
export function assertEngineeringAgentSafety({ triggerEnvironmentType, env = process.env } = {}) {
  if (triggerEnvironmentType !== "DEVELOPMENT") {
    throw new EngineeringIntakeError("TRIGGER_ENVIRONMENT_NOT_DEVELOPMENT");
  }
  if (env.WOW_TRIGGER_PILOT_ENABLED !== "true" || env.WOW_TRIGGER_PILOT_ENV !== "dev") {
    throw new EngineeringIntakeError("TRIGGER_PILOT_NOT_EXPLICITLY_ENABLED");
  }
  if (env.WOW_TRIGGER_ENGINEERING_AGENT_ENABLED !== "true") {
    throw new EngineeringIntakeError("ENGINEERING_AGENT_PILOT_DISABLED");
  }
  if (env.WOW_CAN_EXECUTE !== "false" || env.WOW_DRY_RUN_ONLY !== "true") {
    throw new EngineeringIntakeError("V17_NON_EXECUTION_GUARD_NOT_PROVEN");
  }
}

export function validateIncidentNumber(value) {
  if (!Number.isSafeInteger(value) || value < 1 || value > 999999) {
    throw new EngineeringIntakeError("INVALID_INCIDENT_NUMBER");
  }
  return value;
}

export function issueApiUrl(incidentNumber) {
  const number = validateIncidentNumber(incidentNumber);
  return ISSUE_API_PREFIX + number;
}

function sha256(value) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

async function cancelResponseBody(response) {
  try {
    await response.body?.cancel();
  } catch {
    throw new EngineeringIntakeError("GITHUB_RESPONSE_BODY_CANCEL_FAILED", true);
  }
}

/**
 * Read one public GitHub issue from a fixed repository and return metadata only.
 * The issue body is hashed but never copied into task output or logs.
 *
 * @param {{incidentNumber:number, fetchImpl?:typeof fetch}} options
 */
export async function fetchIncidentSnapshot({ incidentNumber, fetchImpl = globalThis.fetch } = {}) {
  const number = validateIncidentNumber(incidentNumber);
  if (typeof fetchImpl !== "function") {
    throw new EngineeringIntakeError("GITHUB_FETCH_UNAVAILABLE");
  }

  const url = issueApiUrl(number);
  let response;
  try {
    response = await fetchImpl(url, {
      method: "GET",
      headers: {
        accept: "application/vnd.github+json",
        "user-agent": "WOW-V17-Trigger-Engineering-Recovery-Pilot",
      },
      redirect: "manual",
      signal: AbortSignal.timeout(8000),
    });
  } catch (error) {
    const name = error && typeof error === "object" ? error.name : "";
    throw new EngineeringIntakeError(
      name === "TimeoutError" || name === "AbortError"
        ? "GITHUB_READ_TIMEOUT"
        : "GITHUB_READ_TRANSPORT_UNAVAILABLE",
      true,
    );
  }

  if (!response || !Number.isInteger(response.status)) {
    throw new EngineeringIntakeError("GITHUB_RESPONSE_INVALID");
  }

  if (response.status < 200 || response.status >= 300) {
    await cancelResponseBody(response);
    const remaining = response.headers?.get?.("x-ratelimit-remaining");
    if (response.status === 429 || response.status >= 500 || (response.status === 403 && remaining === "0")) {
      throw new EngineeringIntakeError("GITHUB_READ_TRANSIENT_HTTP_" + response.status, true);
    }
    throw new EngineeringIntakeError("GITHUB_READ_PERMANENT_HTTP_" + response.status);
  }

  let issue;
  try {
    issue = await response.json();
  } catch {
    throw new EngineeringIntakeError("GITHUB_ISSUE_JSON_INVALID");
  }

  if (!issue || typeof issue !== "object" || Array.isArray(issue)) {
    throw new EngineeringIntakeError("GITHUB_ISSUE_SHAPE_INVALID");
  }
  if (issue.number !== number || issue.html_url !== "https://github.com/" + ENGINEERING_REPO + "/issues/" + number) {
    throw new EngineeringIntakeError("GITHUB_ISSUE_IDENTITY_MISMATCH");
  }
  if (Object.prototype.hasOwnProperty.call(issue, "pull_request")) {
    throw new EngineeringIntakeError("TARGET_IS_PULL_REQUEST");
  }
  if (issue.state !== "open") {
    throw new EngineeringIntakeError("INCIDENT_NOT_OPEN");
  }
  if (typeof issue.title !== "string" || issue.title.length < 1 || issue.title.length > 500) {
    throw new EngineeringIntakeError("GITHUB_ISSUE_TITLE_INVALID");
  }
  if (typeof issue.created_at !== "string" || Number.isNaN(Date.parse(issue.created_at))
      || typeof issue.updated_at !== "string" || Number.isNaN(Date.parse(issue.updated_at))) {
    throw new EngineeringIntakeError("GITHUB_ISSUE_TIMESTAMP_INVALID");
  }

  const body = typeof issue.body === "string" ? issue.body : "";
  if (body.length > 100000) {
    throw new EngineeringIntakeError("GITHUB_ISSUE_BODY_TOO_LARGE");
  }
  const labels = Array.isArray(issue.labels)
    ? issue.labels.map(label => typeof label?.name === "string" ? label.name : null).filter(Boolean).slice(0, 30)
    : [];

  return Object.freeze({
    repository: ENGINEERING_REPO,
    issue_number: number,
    title: issue.title,
    state: issue.state,
    labels,
    created_at: issue.created_at,
    updated_at: issue.updated_at,
    html_url: issue.html_url,
    body_sha256: sha256(body),
    body_characters: body.length,
    body_included: false,
    source: "GITHUB_ISSUES_API_PUBLIC_READ_ONLY",
  });
}

export function sameIncidentSnapshot(before, after) {
  return Boolean(
    before && after
    && before.repository === after.repository
    && before.issue_number === after.issue_number
    && before.updated_at === after.updated_at
    && before.body_sha256 === after.body_sha256
    && before.state === after.state,
  );
}
