import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const TARGET = "https://wow-governed-probability-engine.onrender.com";
const SLUG = "/functions/v1/wow-llp-action-gateway";
const ALLOWED = new Set([
  "/health",
  "/internal/v17/spread-forward-shadow",
]);

Deno.serve(async (req: Request) => {
  const url = new URL(req.url);
  let upstreamPath = url.pathname.startsWith(SLUG)
    ? url.pathname.slice(SLUG.length)
    : "";
  if (!upstreamPath) upstreamPath = url.searchParams.get("path") || "/health";

  if (!ALLOWED.has(upstreamPath)) {
    return new Response(JSON.stringify({ code: "LLP_GATEWAY_PATH_NOT_ALLOWED", can_execute: false }), {
      status: 404,
      headers: { "content-type": "application/json" },
    });
  }

  const authorization = req.headers.get("authorization");
  if (upstreamPath !== "/health" && (!authorization || !authorization.startsWith("Bearer "))) {
    return new Response(JSON.stringify({ code: "LLP_GATEWAY_AUTH_REQUIRED", can_execute: false }), {
      status: 401,
      headers: { "content-type": "application/json" },
    });
  }

  const headers = new Headers();
  const contentType = req.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  if (authorization) headers.set("authorization", authorization);
  headers.set("user-agent", "WOW-LLP-Supabase-Gateway/1.0");

  const body = req.method === "GET" || req.method === "HEAD" ? undefined : await req.arrayBuffer();

  try {
    const response = await fetch(TARGET + upstreamPath, {
      method: req.method,
      headers,
      body,
      redirect: "manual",
    });
    const responseBody = await response.arrayBuffer();
    const outHeaders = new Headers();
    const responseType = response.headers.get("content-type");
    if (responseType) outHeaders.set("content-type", responseType);
    outHeaders.set("x-wow-gateway", "supabase-edge");
    return new Response(responseBody, { status: response.status, headers: outHeaders });
  } catch {
    return new Response(JSON.stringify({
      code: "LLP_GATEWAY_UPSTREAM_TRANSPORT_FAILURE",
      can_execute: false,
    }), {
      status: 502,
      headers: { "content-type": "application/json" },
    });
  }
});
