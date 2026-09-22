import {
  BACKEND_DEFAULT,
  CAN_EXECUTE,
  TERMINAL_AUTHORITY,
  V17_OPERATIONS,
} from "./v17_contract.js";

const PUBLIC_UNAUTHENTICATED_OPERATIONS = new Set([
  "getWowV17BackendHealth",
  "getWowV17Governance",
]);

export class V17GatewayError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = "V17GatewayError";
    this.code = code;
    this.details = details;
  }
}

function baseUrl() {
  return (process.env.WOW_V17_BACKEND_URL || BACKEND_DEFAULT).replace(/\/$/, "");
}

function backendKey() {
  return process.env.WOW_ACTION_API_KEY || "";
}

function timeoutFor(operation) {
  const envValue = Number(process.env.WOW_MCP_UPSTREAM_TIMEOUT_MS || "");
  if (Number.isFinite(envValue) && envValue > 0) return envValue;
  return operation.timeoutMs || 30000;
}

function interpolatePath(path, args, pathParams = []) {
  let resolved = path;
  for (const name of pathParams) {
    const value = args?.[name];
    if (value == null || value === "") {
      throw new V17GatewayError("MCP_INPUT_INVALID", `Missing required path parameter: ${name}`, { name });
    }
    resolved = resolved.replace(`{${name}}`, encodeURIComponent(String(value)));
  }
  return resolved;
}

function buildQuery(args, names = []) {
  const params = new URLSearchParams();
  for (const name of names) {
    const value = args?.[name];
    if (value == null) continue;
    params.set(name, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

async function readResponseBody(response) {
  const text = await response.text();
  if (!text) return { payload: {}, wasJson: true };
  try {
    return { payload: JSON.parse(text), wasJson: true };
  } catch {
    return { payload: { raw_text: text }, wasJson: false };
  }
}

function hasGovernedMarkers(payload) {
  if (!isPlainObject(payload)) return false;
  return [
    "status",
    "code",
    "blocker",
    "blockers",
    "rank_eligible",
    "probability_publishable",
    "scoring_attempted",
    "prediction_id",
    "can_execute",
  ].some((key) => Object.prototype.hasOwnProperty.call(payload, key));
}

export function assertNoExecutionAuthority(payload) {
  const queue = [payload];
  while (queue.length) {
    const current = queue.shift();
    if (isPlainObject(current)) {
      if (current.can_execute === true) {
        throw new V17GatewayError(
          "MCP_GOVERNANCE_VIOLATION",
          "Upstream attempted to grant execution authority; the MCP gateway failed closed.",
        );
      }
      for (const value of Object.values(current)) {
        if (isPlainObject(value) || Array.isArray(value)) queue.push(value);
      }
    } else if (Array.isArray(current)) {
      for (const value of current) {
        if (isPlainObject(value) || Array.isArray(value)) queue.push(value);
      }
    }
  }
}

function gatewayMeta(operationName) {
  return {
    adapter: "WOW_V17_MCP_GATEWAY",
    operation_id: operationName,
    terminal_authority: TERMINAL_AUTHORITY,
    can_execute: CAN_EXECUTE,
    dry_run_only: true,
    wager_execution_supported: false,
  };
}

export function decorateGatewayResult(operationName, payload) {
  assertNoExecutionAuthority(payload);
  if (payload && typeof payload === "object" && !Array.isArray(payload)) {
    return { ...payload, mcp_gateway: gatewayMeta(operationName) };
  }
  return { upstream_result: payload, mcp_gateway: gatewayMeta(operationName) };
}

export async function invokeV17Operation(operationName, args = {}, options = {}) {
  const operation = V17_OPERATIONS[operationName];
  if (!operation) {
    throw new V17GatewayError("MCP_TOOL_NOT_REGISTERED", `Unknown WOW V17 MCP operation: ${operationName}`);
  }

  const key = options.backendKey ?? backendKey();
  const authRequired = !PUBLIC_UNAUTHENTICATED_OPERATIONS.has(operationName);
  if (!key && authRequired) {
    throw new V17GatewayError(
      "BACKEND_AUTH_NOT_CONFIGURED",
      "WOW_ACTION_API_KEY is not configured on the MCP gateway. The backend credential must remain server-side.",
    );
  }

  const path = interpolatePath(operation.path, args, operation.pathParams || []);
  const query = buildQuery(args, operation.query || []);
  const url = `${options.baseUrl || baseUrl()}${path}${query}`;
  const headers = {
    Accept: "application/json",
    "User-Agent": "wow-v17-mcp-gateway/1.0",
  };
  if (key) headers.Authorization = `Bearer ${key}`;

  const request = {
    method: operation.method,
    headers,
    signal: AbortSignal.timeout(options.timeoutMs || timeoutFor(operation)),
  };

  if (operation.method !== "GET") {
    headers["Content-Type"] = "application/json";
    request.body = JSON.stringify(args ?? {});
  }

  const fetchImpl = options.fetchImpl || globalThis.fetch;
  if (typeof fetchImpl !== "function") {
    throw new V17GatewayError("MCP_RUNTIME_FETCH_UNAVAILABLE", "Global fetch is unavailable in this Node runtime.");
  }

  let response;
  try {
    response = await fetchImpl(url, request);
  } catch (error) {
    const timeout = error?.name === "TimeoutError" || error?.name === "AbortError";
    throw new V17GatewayError(
      timeout ? "ACTION_TRANSPORT_TIMEOUT" : "ACTION_TRANSPORT_FAILURE",
      `WOW V17 backend request failed before a terminal response: ${error?.message || String(error)}`,
      {
        operation_id: operationName,
        recovery_operation_id:
          operationName === "scoreWowPickRequest" ? "lookupWowV17PredictionReceipts" : null,
      },
    );
  }

  const { payload, wasJson } = await readResponseBody(response);

  if (response.status === 401 || response.status === 403) {
    throw new V17GatewayError(
      "BACKEND_AUTH_FAILED",
      `WOW V17 backend rejected the server-held credential with HTTP ${response.status}.`,
      { operation_id: operationName, upstream_status: response.status },
    );
  }

  if (!response.ok) {
    if (wasJson && hasGovernedMarkers(payload)) {
      return decorateGatewayResult(operationName, payload);
    }
    throw new V17GatewayError(
      "UPSTREAM_HTTP_FAILURE",
      `WOW V17 backend returned non-governed HTTP ${response.status}.`,
      {
        operation_id: operationName,
        upstream_status: response.status,
        upstream_payload: payload,
      },
    );
  }

  return decorateGatewayResult(operationName, payload);
}

export function errorPayload(error, operationName) {
  if (error instanceof V17GatewayError) {
    return {
      ok: false,
      gateway_status: error.code,
      message: error.message,
      details: error.details,
      can_execute: false,
      mcp_gateway: gatewayMeta(operationName),
    };
  }
  return {
    ok: false,
    gateway_status: "MCP_GATEWAY_INTERNAL_ERROR",
    message: error?.message || String(error),
    can_execute: false,
    mcp_gateway: gatewayMeta(operationName),
  };
}
