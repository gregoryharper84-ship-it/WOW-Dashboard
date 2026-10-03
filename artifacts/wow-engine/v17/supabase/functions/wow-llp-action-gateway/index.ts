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

function jsonResponse(status: number, code: string, extra: Record<string, unknown> = {}) {
  return new Response(JSON.stringify({ code, can_execute: false, ...extra }), {
    status,
    headers: { "content-type": "application/json" },
  });
}

Deno.serve(async (req: Request) => {
  const url = new URL(req.url);
  let upstreamPath = url.pathname.startsWith(SLUG)
    ? url.pathname.slice(SLUG.length)
    : "";

  // Retain the original root health probe used by the diagnostic contract.
  if (!upstreamPath) upstreamPath = "/health";

  const pathMatches = ROUTES.filter((route) => route.pattern.test(upstreamPath));
  if (pathMatches.length === 0) {
    return jsonResponse(404, "LLP_GATEWAY_PATH_NOT_ALLOWED");
  }

  const route = pathMatches.find((candidate) => candidate.method === req.method);
  if (!route) {
    return jsonResponse(405, "LLP_GATEWAY_METHOD_NOT_ALLOWED", {
      allowed_methods: [...new Set(pathMatches.map((candidate) => candidate.method))],
    });
  }

  const authorization = req.headers.get("authorization");
  if (route.auth && (!authorization || !authorization.startsWith("Bearer "))) {
    return jsonResponse(401, "LLP_GATEWAY_AUTH_REQUIRED");
  }

  const headers = new Headers();
  for (const name of ["content-type", "accept"]) {
    const value = req.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (authorization) headers.set("authorization", authorization);
  headers.set("user-agent", "WOW-LLP-Supabase-Gateway/2.0");

  const upstream = new URL(TARGET + upstreamPath);
  upstream.search = url.search;
  const body = req.method === "GET" || req.method === "HEAD" ? undefined : await req.arrayBuffer();

  try {
    const response = await fetch(upstream, {
      method: req.method,
      headers,
      body,
      redirect: "manual",
    });
    const responseBody = await response.arrayBuffer();
    const outHeaders = new Headers();
    for (const name of ["content-type", "retry-after", "x-request-id"]) {
      const value = response.headers.get(name);
      if (value) outHeaders.set(name, value);
    }
    outHeaders.set("x-wow-gateway", "supabase-edge");
    outHeaders.set("x-wow-gateway-version", "2.0");
    return new Response(responseBody, {
      status: response.status,
      statusText: response.statusText,
      headers: outHeaders,
    });
  } catch (error) {
    return jsonResponse(502, "LLP_GATEWAY_UPSTREAM_TRANSPORT_FAILURE", {
      error_type: error instanceof Error ? error.name : "UnknownError",
    });
  }
});
