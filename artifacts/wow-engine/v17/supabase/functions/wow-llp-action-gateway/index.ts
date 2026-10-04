import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const TARGET = "https://wow-governed-probability-engine.onrender.com";
const SLUG = "/functions/v1/wow-llp-action-gateway";

type Route = {
  method: "GET" | "POST";
  pattern: RegExp;
  auth: boolean;
};

const ROUTES: Route[] = [
  { method: "GET", pattern: /^\/health$/, auth: false },
  { method: "GET", pattern: /^\/governance$/, auth: false },
  { method: "GET", pattern: /^\/v17\/host-contract$/, auth: true },
  { method: "POST", pattern: /^\/v17\/daily-snapshot-run$/, auth: true },
  { method: "GET", pattern: /^\/v17\/daily-snapshot-run\/[^/]+\/rows$/, auth: true },
  { method: "POST", pattern: /^\/score-team-event$/, auth: true },
  { method: "POST", pattern: /^\/internal\/v17\/spread-forward-shadow$/, auth: true },
  { method: "POST", pattern: /^\/internal\/v17\/nfl-spread-forward-shadow$/, auth: true },
  { method: "POST", pattern: /^\/internal\/v17\/wnba-spread-forward-shadow$/, auth: true },
  { method: "POST", pattern: /^\/internal\/v17\/mlb-run-line-forward-shadow$/, auth: true },
  { method: "POST", pattern: /^\/record-recommendations$/, auth: true },
  { method: "POST", pattern: /^\/settle-recommendations$/, auth: true },
];

function correlationId(req: Request): string {
  const supplied = (
    req.headers.get("x-wow-request-id") ||
    req.headers.get("x-request-id") ||
    ""
  ).trim();
  if (/^[A-Za-z0-9._:-]{8,128}$/.test(supplied)) return supplied;
  return crypto.randomUUID();
}

function responseHeaders(requestId: string, contentType = "application/json"): Headers {
  const headers = new Headers({
    "content-type": contentType,
    "x-request-id": requestId,
    "x-wow-request-id": requestId,
    "x-wow-gateway": "supabase-edge",
    "x-wow-gateway-version": "2.1",
  });
  return headers;
}

function gatewayLog(
  stage: string,
  requestId: string,
  method: string,
  path: string,
  extra: Record<string, unknown> = {},
) {
  console.info(JSON.stringify({
    component: "wow-llp-action-gateway",
    stage,
    request_id: requestId,
    method,
    path,
    can_execute: false,
    ...extra,
  }));
}

function jsonResponse(
  status: number,
  code: string,
  requestId: string,
  extra: Record<string, unknown> = {},
) {
  return new Response(JSON.stringify({ code, can_execute: false, ...extra }), {
    status,
    headers: responseHeaders(requestId),
  });
}

Deno.serve(async (req: Request) => {
  const requestId = correlationId(req);
  const url = new URL(req.url);
  let upstreamPath = url.pathname.startsWith(SLUG)
    ? url.pathname.slice(SLUG.length)
    : "";

  // Retain the original root health probe used by the diagnostic contract.
  if (!upstreamPath) upstreamPath = "/health";

  gatewayLog("INGRESS", requestId, req.method, upstreamPath);

  const pathMatches = ROUTES.filter((route) => route.pattern.test(upstreamPath));
  if (pathMatches.length === 0) {
    gatewayLog("REJECTED_PATH", requestId, req.method, upstreamPath);
    return jsonResponse(404, "LLP_GATEWAY_PATH_NOT_ALLOWED", requestId);
  }

  const route = pathMatches.find((candidate) => candidate.method === req.method);
  if (!route) {
    gatewayLog("REJECTED_METHOD", requestId, req.method, upstreamPath);
    return jsonResponse(405, "LLP_GATEWAY_METHOD_NOT_ALLOWED", requestId, {
      allowed_methods: [...new Set(pathMatches.map((candidate) => candidate.method))],
    });
  }

  const authorization = req.headers.get("authorization");
  if (route.auth && (!authorization || !authorization.startsWith("Bearer "))) {
    gatewayLog("AUTH_REQUIRED", requestId, req.method, upstreamPath);
    return jsonResponse(401, "LLP_GATEWAY_AUTH_REQUIRED", requestId);
  }

  const headers = new Headers();
  for (const name of ["content-type", "accept"]) {
    const value = req.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (authorization) headers.set("authorization", authorization);
  headers.set("x-request-id", requestId);
  headers.set("x-wow-request-id", requestId);
  headers.set("user-agent", "WOW-LLP-Supabase-Gateway/2.1");

  const upstream = new URL(TARGET + upstreamPath);
  upstream.search = url.search;
  const body = req.method === "GET" || req.method === "HEAD" ? undefined : await req.arrayBuffer();

  try {
    gatewayLog("UPSTREAM_DISPATCH", requestId, req.method, upstreamPath);
    const response = await fetch(upstream, {
      method: req.method,
      headers,
      body,
      redirect: "manual",
    });
    const responseBody = await response.arrayBuffer();
    const outHeaders = responseHeaders(
      requestId,
      response.headers.get("content-type") || "application/json",
    );
    const retryAfter = response.headers.get("retry-after");
    if (retryAfter) outHeaders.set("retry-after", retryAfter);
    gatewayLog("UPSTREAM_RESPONSE", requestId, req.method, upstreamPath, {
      upstream_status: response.status,
    });
    return new Response(responseBody, {
      status: response.status,
      statusText: response.statusText,
      headers: outHeaders,
    });
  } catch (error) {
    const errorType = error instanceof Error ? error.name : "UnknownError";
    gatewayLog("UPSTREAM_FAILURE", requestId, req.method, upstreamPath, {
      error_type: errorType,
    });
    return jsonResponse(502, "LLP_GATEWAY_UPSTREAM_TRANSPORT_FAILURE", requestId, {
      error_type: errorType,
    });
  }
});
