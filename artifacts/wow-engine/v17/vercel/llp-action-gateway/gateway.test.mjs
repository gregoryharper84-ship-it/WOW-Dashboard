import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import handler from "./api/gateway.js";

const config = JSON.parse(readFileSync(new URL("./vercel.json", import.meta.url), "utf8"));

function vercelSourceMatches(source, requestPath) {
  const pieces = source.split("/").map((segment) => {
    if (segment.startsWith(":")) {
      return segment.endsWith("*") ? ".*" : "[^/]+";
    }
    return segment.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  });
  return new RegExp("^" + pieces.join("/") + "$").test(requestPath);
}

test("Vercel public rewrites do not send WOW prop paths to the handler", () => {
  const propPaths = [
    "/functions/v1/wow-llp-action-gateway/score-pick-request",
    "/functions/v1/wow-llp-action-gateway/score-prop",
  ];
  for (const path of propPaths) {
    assert.equal(
      config.rewrites.some((rewrite) => vercelSourceMatches(rewrite.source, path)),
      false,
      `unexpected public rewrite for ${path}`,
    );
  }
  assert.equal(
    config.rewrites.some((rewrite) => rewrite.source.endsWith("/score-team-event")),
    true,
  );
});

test("Vercel handler rejects prop __route values before upstream dispatch", async () => {
  const originalFetch = globalThis.fetch;
  let upstreamCalls = 0;
  globalThis.fetch = async () => {
    upstreamCalls += 1;
    throw new Error("unexpected upstream call");
  };
  try {
    for (const route of ["/score-pick-request", "/score-prop"]) {
      let statusCode;
      let body;
      const headers = {};
      const response = {
        status(code) {
          statusCode = code;
          return this;
        },
        setHeader(name, value) {
          headers[name] = value;
          return this;
        },
        send(value) {
          body = value;
          return this;
        },
      };
      await handler(
        {
          method: "POST",
          query: { __route: route },
          headers: { authorization: "Bearer test-only-not-a-secret" },
          body: { probe: true },
        },
        response,
      );
      assert.equal(statusCode, 404);
      assert.equal(JSON.parse(body).code, "LLP_VERCEL_GATEWAY_PATH_NOT_ALLOWED");
      assert.equal(headers["x-wow-gateway"], "vercel-action-ingress");
      assert.equal(typeof headers["x-wow-request-id"], "string");
      assert.equal(upstreamCalls, 0);
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});
