/// <reference lib="dom" />
/**
 * WOW V17 runtime sentinel: read-only signals, not a fitted-model or Action probe.
 * No incident mutation, GitHub dispatch, release action, or wagering authority.
 */
export const WOW_BACKEND = "https://wow-governed-probability-engine.onrender.com";
const PROBE_TIMEOUT_MS = 7000;

type Check = "ok" | "unreachable" | "mismatch_or_unreachable" | "unknown";
type Probe = {
  endpoint: "/health" | "/health/ready";
  http_status: number | null;
  code: string;
  detail?: string;
};

type FetchLike = (url: string, options: { method: "GET"; signal: AbortSignal; redirect: "error"; headers: { accept: string } }) => Promise<Pick<Response, "status" | "ok" | "json">>;

export type WowRuntimeReceipt = {
  objective_id: "WOW-V17-TRIGGER-RECOVERY-001";
  schema_version: 1;
  observed_at: string;
  status: "WORKING_NOT_COMPLETE" | "BLOCKED";
  scope: "BACKEND_RUNTIME";
  owner: "AMRT";
  code: string;
  incident_key: string | null;
  checked_url: string;
  probes: Probe[];
  readiness: { database: Check; queue: Check; worker_registry: Check };
  model_capability: "UNVERIFIED_NOT_PROBED";
  action_receipt: "UNVERIFIED_NOT_PROBED";
  durable_incident_written: false;
  dispatch_attempted: false;
  terminal_authority: "V17_TERMINAL_REDUCER";
  can_execute: false;
};

function checkValue(value: unknown): Check {
  return value === "ok" || value === "unreachable" || value === "mismatch_or_unreachable"
    ? value : "unknown";
}

function obj(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
}

function readReadyPayload(value: unknown): Record<string, unknown> | null {
  const root = obj(value);
  if (!root) return null;
  return obj(root.detail) ?? root;
}

async function getJson(
  fetchImpl: FetchLike, endpoint: Probe["endpoint"],
): Promise<{ status: number | null; body: unknown; error?: string }> {
  try {
    const response = await fetchImpl(`${WOW_BACKEND}${endpoint}`, {
      method: "GET", signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
      redirect: "error", headers: { accept: "application/json" },
    });
    try {
      return { status: response.status, body: await response.json() };
    } catch {
      return { status: response.status, body: null, error: "INVALID_JSON" };
    }
  } catch {
    // Deliberately don't serialize exception text: it may contain transport secrets.
    return { status: null, body: null, error: "TRANSPORT_FAILURE" };
  }
}

/** Pure, injectable monitor for deterministic tests. No backend mutations. */
export async function probeWowRuntime(
  options: { fetchImpl?: FetchLike; now?: () => Date } = {},
): Promise<WowRuntimeReceipt> {
  const fetchImpl = options.fetchImpl ?? (fetch as FetchLike);
  const observedAt = (options.now ?? (() => new Date()))().toISOString();
  const probes: Probe[] = [];
  const readiness: WowRuntimeReceipt["readiness"] = {
    database: "unknown", queue: "unknown", worker_registry: "unknown",
  };
  let failure: string | null = null;
  const live = await getJson(fetchImpl, "/health");
  const liveBody = obj(live.body);
  if (live.error) failure = `BACKEND_LIVENESS_${live.error}`;
  else if (live.status !== 200) failure = `BACKEND_LIVENESS_HTTP_${live.status}`;
  else if (liveBody?.status !== "ok" || liveBody?.can_execute === true) failure = "BACKEND_LIVENESS_CONTRACT_INVALID";
  probes.push({ endpoint: "/health", http_status: live.status, code: failure ?? "OK" });

  if (!failure) {
    const ready = await getJson(fetchImpl, "/health/ready");
    const body = readReadyPayload(ready.body);
    readiness.database = checkValue(body?.database);
    readiness.queue = checkValue(body?.queue);
    readiness.worker_registry = checkValue(body?.worker_registry);
    if (ready.error) failure = `BACKEND_READINESS_${ready.error}`;
    else if (body?.can_execute === true) failure = "BACKEND_READINESS_CONTRACT_INVALID";
    else if (ready.status === 503 && body?.status === "not_ready") {
      failure = "BACKEND_NOT_READY";
    } else if (ready.status !== 200) failure = `BACKEND_READINESS_HTTP_${ready.status}`;
    else if (body?.status !== "ok" || readiness.database !== "ok" || readiness.queue !== "ok" || readiness.worker_registry !== "ok") {
      failure = "BACKEND_READINESS_CONTRACT_INVALID";
    }
    probes.push({ endpoint: "/health/ready", http_status: ready.status, code: failure ?? "OK" });
  }
  return {
    objective_id: "WOW-V17-TRIGGER-RECOVERY-001", schema_version: 1,
    observed_at: observedAt, status: failure ? "BLOCKED" : "WORKING_NOT_COMPLETE",
    scope: "BACKEND_RUNTIME", owner: "AMRT",
    code: failure ?? "BACKEND_READY_MODEL_UNVERIFIED",
    incident_key: failure
      ? `WOW_V17:BACKEND_RUNTIME:${failure}:${[
          readiness.database === "unreachable" ? "DATABASE" : "",
          readiness.queue === "unreachable" ? "QUEUE" : "",
          readiness.worker_registry === "mismatch_or_unreachable" ? "WORKER_REGISTRY" : "",
        ].filter(Boolean).join("+") || "GENERAL"}`
      : null,
    checked_url: WOW_BACKEND, probes, readiness,
    model_capability: "UNVERIFIED_NOT_PROBED", action_receipt: "UNVERIFIED_NOT_PROBED",
    durable_incident_written: false, dispatch_attempted: false,
    terminal_authority: "V17_TERMINAL_REDUCER", can_execute: false,
  };
}
