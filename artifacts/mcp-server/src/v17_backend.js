import {
  BACKEND_DEFAULT,
  CAN_EXECUTE,
  TERMINAL_AUTHORITY,
  V17_OPERATIONS,
} from "./v17_contract.js";

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

async function readResponseBody(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { raw_text: text };
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
  if (!key) {
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
    Authorization: `Bearer ${key}`,
    "User-Agent": "wow-v17-mcp-gateway/1.0",
  };

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

  const payload = await readResponseBody(response);
  if (!response.ok) {
    throw new V17GatewayError(
      "UPSTREAM_TYPED_FAILURE",
      `WOW V17 backend returned HTTP ${response.status}. Preserve the backend failure payload; do not rewrite it as MODEL_UNAVAILABLE.`,
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
      mcp_gateway: gatewayMeta(operationName),
    };
  }
  return {
    ok: false,
    gateway_status: "MCP_GATEWAY_INTERNAL_ERROR",
    message: error?.message || String(error),
    mcp_gateway: gatewayMeta(operationName),
  };
}
