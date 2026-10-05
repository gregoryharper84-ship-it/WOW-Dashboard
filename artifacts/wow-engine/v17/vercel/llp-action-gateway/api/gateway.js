import { randomUUID } from "node:crypto";

const TARGET = "https://wow-governed-probability-engine.onrender.com";

const ROUTES = new Map([
  ["/health", { method: "GET", auth: false }],
  ["/governance", { method: "GET", auth: false }],
  ["/v17/host-contract", { method: "GET", auth: true }],
  ["/v17/daily-snapshot-run", { method: "POST", auth: true }],
  ["/v17/daily-snapshot-run/__ROWS__", { method: "GET", auth: true }],
  ["/score-team-event", { method: "POST", auth: true }],
  ["/internal/v17/spread-forward-shadow", { method: "POST", auth: true }],
  ["/internal/v17/nfl-spread-forward-shadow", { method: "POST", auth: true }],
  ["/internal/v17/wnba-spread-forward-shadow", { method: "POST", auth: true }],
  ["/internal/v17/mlb-run-line-forward-shadow", { method: "POST", auth: true }],
  ["/record-recommendations", { method: "POST", auth: true }],
  ["/settle-recommendations", { method: "POST", auth: true }],
]);

function safeRequestId(req) {
  const supplied = String(
    req.headers["x-wow-request-id"] || req.headers["x-request-id"] || "",
  ).trim();
  return /^[A-Za-z0-9._:-]{8,128}$/.test(supplied) ? supplied : randomUUID();
}

function gatewayHeaders(requestId, contentType = "application/json") {
  return {
    "content-type": contentType,
    "x-request-id": requestId,
    "x-wow-request-id": requestId,
    "x-wow-gateway": "vercel-action-ingress",
    "x-wow-gateway-version": "0.1-challenger",
  };
}

function fail(res, status, code, requestId, extra = {}) {
  res.status(status);
  for (const [name, value] of Object.entries(gatewayHeaders(requestId))) {
    res.setHeader(name, value);
  }
  res.send(JSON.stringify({ code, can_execute: false, ...extra }));
}

function normalizedRoute(query) {
  const route = String(query.__route || "");
  if (route === "/v17/daily-snapshot-run/__ROWS__") {
    const runId = String(query.run_id || "").trim();
    if (!/^[A-Za-z0-9._:-]{1,128}$/.test(runId)) return null;
    return `/v17/daily-snapshot-run/${encodeURIComponent(runId)}/rows`;
  }
  return route;
}

function routeKey(query) {
  return String(query.__route || "");
}

function outboundBody(req) {
  if (req.method === "GET" || req.method === "HEAD") return undefined;
  if (req.body == null) return undefined;
  if (Buffer.isBuffer(req.body) || typeof req.body === "string") return req.body;
  return JSON.stringify(req.body);
}

export default async function handler(req, res) {
  const requestId = safeRequestId(req);
  const key = routeKey(req.query || {});
  const route = ROUTES.get(key);

  if (!route) {
    return fail(res, 404, "LLP_VERCEL_GATEWAY_PATH_NOT_ALLOWED", requestId);
  }
  if (req.method !== route.method) {
    return fail(res, 405, "LLP_VERCEL_GATEWAY_METHOD_NOT_ALLOWED", requestId, {
      allowed_method: route.method,
    });
  }

  const authorization = req.headers.authorization;
  if (route.auth && (!authorization || !authorization.startsWith("Bearer "))) {
    return fail(res, 401, "LLP_VERCEL_GATEWAY_AUTH_REQUIRED", requestId);
  }

  const path = normalizedRoute(req.query || {});
  if (!path) {
    return fail(res, 422, "LLP_VERCEL_GATEWAY_ROUTE_INVALID", requestId);
  }

  const upstream = new URL(TARGET + path);
  for (const [name, value] of Object.entries(req.query || {})) {
    if (name === "__route" || name === "run_id") continue;
    if (Array.isArray(value)) {
      for (const item of value) upstream.searchParams.append(name, String(item));
    } else if (value != null) {
      upstream.searchParams.set(name, String(value));
    }
  }

  const headers = {
    "x-request-id": requestId,
    "x-wow-request-id": requestId,
    "user-agent": "WOW-LLP-Vercel-Gateway/0.1",
  };
  for (const name of ["content-type", "accept"]) {
    if (req.headers[name]) headers[name] = req.headers[name];
  }
  if (authorization) headers.authorization = authorization;

  try {
    const response = await fetch(upstream, {
      method: req.method,
      headers,
      body: outboundBody(req),
      redirect: "manual",
    });
    const body = Buffer.from(await response.arrayBuffer());

    res.status(response.status);
    const out = gatewayHeaders(
      requestId,
      response.headers.get("content-type") || "application/json",
    );
    for (const [name, value] of Object.entries(out)) res.setHeader(name, value);
    const retryAfter = response.headers.get("retry-after");
    if (retryAfter) res.setHeader("retry-after", retryAfter);
    return res.send(body);
  } catch (error) {
    return fail(res, 502, "LLP_VERCEL_GATEWAY_UPSTREAM_TRANSPORT_FAILURE", requestId, {
      error_type: error instanceof Error ? error.name : "UnknownError",
    });
  }
}
