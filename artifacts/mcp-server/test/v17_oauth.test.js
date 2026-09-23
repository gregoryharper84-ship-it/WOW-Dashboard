import assert from "node:assert/strict";
import { generateKeyPairSync, sign } from "node:crypto";
import test from "node:test";

import {
  V17OAuthError,
  WOW_PERMISSION_SET,
  assertOAuthPermissions,
  buildProtectedResourceMetadata,
  extractOAuthPermissions,
  oauthConfig,
  oauthSessionFingerprint,
  requiredPermissionsForMcpBody,
  verifyOAuthAccessToken,
} from "../src/v17_oauth.js";

const ISSUER = "https://project.example/auth/v1";
const AUDIENCE = "authenticated";
const CLIENT_ID = "wow-chatgpt-client";
const NOW = 1_800_000_000;

const { publicKey, privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
const publicJwk = publicKey.export({ format: "jwk" });
const jwks = {
  keys: [{ ...publicJwk, kid: "test-key", alg: "RS256", use: "sig" }],
};

function encodeJson(value) {
  return Buffer.from(JSON.stringify(value), "utf8").toString("base64url");
}

function token(overrides = {}, { signingKey = privateKey, kid = "test-key" } = {}) {
  const header = encodeJson({ alg: "RS256", typ: "JWT", kid });
  const payload = encodeJson({
    iss: ISSUER,
    aud: AUDIENCE,
    sub: "user-1",
    client_id: CLIENT_ID,
    session_id: "session-1",
    iat: NOW - 60,
    exp: NOW + 3600,
    app_metadata: {
      wow_permissions: ["wow.runtime.read", "wow.predictions.score"],
    },
    ...overrides,
  });
  const signingInput = `${header}.${payload}`;
  const signature = sign("RSA-SHA256", Buffer.from(signingInput, "ascii"), signingKey).toString("base64url");
  return `${signingInput}.${signature}`;
}

function verifyOptions(overrides = {}) {
  return {
    issuer: ISSUER,
    audience: AUDIENCE,
    jwksUrl: `${ISSUER}/.well-known/jwks.json`,
    allowedClientIds: new Set([CLIENT_ID]),
    nowSeconds: NOW,
    jwks,
    ...overrides,
  };
}

test("OAuth JWT validation verifies signature, issuer, audience, expiry, and client allowlist", async () => {
  const context = await verifyOAuthAccessToken(token(), verifyOptions());
  assert.equal(context.mode, "oauth");
  assert.equal(context.subject, "user-1");
  assert.equal(context.clientId, CLIENT_ID);
  assert.equal(context.permissions.has("wow.runtime.read"), true);
  assert.equal(context.permissions.has("wow.predictions.score"), true);
});

test("OAuth JWT validation fails closed for audience, expiry, and client mismatches", async () => {
  await assert.rejects(
    verifyOAuthAccessToken(token({ aud: "wrong" }), verifyOptions()),
    (error) => error instanceof V17OAuthError && error.code === "MCP_OAUTH_AUDIENCE_INVALID",
  );
  await assert.rejects(
    verifyOAuthAccessToken(token({ exp: NOW - 60 }), verifyOptions()),
    (error) => error instanceof V17OAuthError && error.code === "MCP_OAUTH_TOKEN_EXPIRED",
  );
  await assert.rejects(
    verifyOAuthAccessToken(token({ client_id: "other-client" }), verifyOptions()),
    (error) => error instanceof V17OAuthError && error.code === "MCP_OAUTH_CLIENT_NOT_ALLOWED",
  );
});

test("OAuth mode refuses to run without an explicit client allowlist", async () => {
  await assert.rejects(
    verifyOAuthAccessToken(token(), verifyOptions({ allowedClientIds: new Set() })),
    (error) => error instanceof V17OAuthError && error.code === "MCP_OAUTH_NOT_CONFIGURED" && error.status === 503,
  );
});

test("unknown JWT kid forces one JWKS refresh so signing-key rotation does not depend on cache expiry", async () => {
  const rotated = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const rotatedJwk = rotated.publicKey.export({ format: "jwk" });
  const staleJwks = jwks;
  const refreshedJwks = {
    keys: [{ ...rotatedJwk, kid: "rotated-key", alg: "RS256", use: "sig" }],
  };
  const calls = [];
  const fetchImpl = async (url) => {
    calls.push(url);
    const body = calls.length === 1 ? staleJwks : refreshedJwks;
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
  const rotatedToken = token({}, { signingKey: rotated.privateKey, kid: "rotated-key" });
  const context = await verifyOAuthAccessToken(rotatedToken, verifyOptions({
    jwks: null,
    jwksUrl: `${ISSUER}/rotation-test-${Date.now()}/.well-known/jwks.json`,
    fetchImpl,
  }));
  assert.equal(context.clientId, CLIENT_ID);
  assert.equal(calls.length, 2);
});

test("signed WOW permission claim and native OAuth scope claims are both recognized", () => {
  const permissions = extractOAuthPermissions({
    scope: "openid wow.runtime.read",
    scp: ["wow.predictions.read"],
    app_metadata: { wow_permissions: ["wow.predictions.score", "wow.daily.run"] },
  });
  assert.deepEqual(
    [...permissions].sort(),
    ["openid", "wow.daily.run", "wow.predictions.read", "wow.predictions.score", "wow.runtime.read"].sort(),
  );
});

test("tool calls use the pre-existing V17 operation permission map", () => {
  assert.deepEqual(
    [...requiredPermissionsForMcpBody({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "scoreWowPickRequest" } })],
    ["wow.predictions.score"],
  );
  assert.deepEqual(
    [...requiredPermissionsForMcpBody({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} })],
    ["wow.runtime.read"],
  );
  assert.throws(
    () => requiredPermissionsForMcpBody({ jsonrpc: "2.0", id: 3, method: "tools/call", params: { name: "placeBet" } }),
    (error) => error instanceof V17OAuthError && error.code === "MCP_OAUTH_TOOL_UNKNOWN" && error.status === 403,
  );
});

test("missing operation permission returns a typed 403 without changing execution authority", () => {
  const authContext = { mode: "oauth", permissions: new Set(["wow.runtime.read"]) };
  assert.throws(
    () => assertOAuthPermissions(authContext, new Set(["wow.predictions.score"])),
    (error) => error instanceof V17OAuthError
      && error.code === "MCP_OAUTH_PERMISSION_DENIED"
      && error.status === 403
      && error.details.required_permissions[0] === "wow.predictions.score",
  );
});

test("MCP OAuth sessions are bound to the issuing Supabase session as well as user and client", () => {
  const base = { mode: "oauth", subject: "user-1", clientId: CLIENT_ID };
  assert.notEqual(
    oauthSessionFingerprint({ ...base, sessionId: "session-a" }),
    oauthSessionFingerprint({ ...base, sessionId: "session-b" }),
  );
  assert.equal(
    oauthSessionFingerprint({ ...base, sessionId: "session-a" }),
    `oauth:user-1:${CLIENT_ID}:session-a`,
  );
});

test("protected-resource metadata advertises the OAuth issuer without pretending Supabase supports WOW custom scopes", () => {
  const metadata = buildProtectedResourceMetadata({
    resourceUrl: "https://mcp.example/mcp",
    authorizationServers: [ISSUER],
    scopesSupported: ["openid", "email", "profile"],
  });
  assert.equal(metadata.resource, "https://mcp.example/mcp");
  assert.deepEqual(metadata.authorization_servers, [ISSUER]);
  assert.deepEqual(metadata.scopes_supported, ["openid", "email", "profile"]);
  assert.equal(metadata.wow_permissions_supported.includes("wow.predictions.score"), true);
  assert.equal(WOW_PERMISSION_SET.includes("wow.settlements.write"), true);
});

test("OAuth config derives Supabase-style JWKS and keeps client allowlist explicit", () => {
  const config = oauthConfig({
    WOW_MCP_OAUTH_ISSUER: ISSUER,
    WOW_MCP_OAUTH_ALLOWED_CLIENT_IDS: `${CLIENT_ID}, second-client`,
  });
  assert.equal(config.jwksUrl, `${ISSUER}/.well-known/jwks.json`);
  assert.equal(config.audience, "authenticated");
  assert.deepEqual(config.authorizationServers, [ISSUER]);
  assert.equal(config.allowedClientIds.has(CLIENT_ID), true);
  assert.equal(config.allowedClientIds.has("second-client"), true);
  assert.equal(config.permissionClaim, "app_metadata.wow_permissions");
});
