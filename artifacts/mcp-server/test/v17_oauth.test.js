import assert from "node:assert/strict";
import { generateKeyPairSync, sign } from "node:crypto";
import test from "node:test";

import { V17_TOOL_NAMES } from "../src/v17_contract.js";
import {
  buildOAuthChallenge,
  createOAuthConfig,
  hasOperationPermission,
  protectedResourceMetadata,
  requiredPermission,
  verifyOAuthAccessToken,
} from "../src/v17_oauth.js";
import { getV17ToolDescriptors } from "../src/v17_server.js";

const RESOURCE = "https://wow-v17-mcp.example/mcp";
const ISSUER = "https://issuer.example/auth/v1";
const SUBJECT = "11111111-2222-3333-4444-555555555555";
const CLIENT_ID = "chatgpt-oauth-client";

const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
const publicJwk = publicKey.export({ format: "jwk" });
publicJwk.kid = "oauth-test-key";
publicJwk.alg = "RS256";
publicJwk.use = "sig";
const TEST_JWKS = { keys: [publicJwk] };

function config(overrides = {}) {
  return createOAuthConfig({
    WOW_MCP_PUBLIC_URL: "https://wow-v17-mcp.example",
    WOW_MCP_OAUTH_ISSUER: ISSUER,
    WOW_MCP_OAUTH_RESOURCE: RESOURCE,
    WOW_MCP_OAUTH_JWKS_URL: `${ISSUER}/.well-known/jwks.json`,
    WOW_MCP_OAUTH_SCOPES: "openid email",
    WOW_MCP_OAUTH_ALLOWED_SUBJECTS: SUBJECT,
    WOW_MCP_OAUTH_PERMISSION_CLAIM: "wow_mcp_permissions",
    ...overrides,
  });
}

function makeJwt(payloadOverrides = {}, headerOverrides = {}) {
  const now = 1_800_000_000;
  const header = { alg: "RS256", typ: "JWT", kid: "oauth-test-key", ...headerOverrides };
  const payload = {
    iss: ISSUER,
    aud: RESOURCE,
    sub: SUBJECT,
    client_id: CLIENT_ID,
    iat: now - 30,
    exp: now + 3600,
    wow_mcp_permissions: [
      "wow.runtime.read",
      "wow.governance.read",
      "wow.evidence.read",
      "wow.predictions.read",
      "wow.predictions.score",
      "wow.daily.run",
      "wow.recommendations.write",
      "wow.settlements.write",
    ],
    ...payloadOverrides,
  };
  const encodedHeader = Buffer.from(JSON.stringify(header)).toString("base64url");
  const encodedPayload = Buffer.from(JSON.stringify(payload)).toString("base64url");
  const signingInput = `${encodedHeader}.${encodedPayload}`;
  const signature = sign("RSA-SHA256", Buffer.from(signingInput), privateKey).toString("base64url");
  return `${signingInput}.${signature}`;
}

test("protected-resource metadata advertises the exact MCP resource and only real Supabase identity scopes", () => {
  const metadata = protectedResourceMetadata(config());
  assert.equal(metadata.resource, RESOURCE);
  assert.deepEqual(metadata.authorization_servers, [ISSUER]);
  assert.deepEqual(metadata.scopes_supported, ["openid", "email"]);
  assert.equal(metadata.scopes_supported.some((scope) => scope.startsWith("wow.")), false);
});

test("OAuth challenge points clients to path-aware protected-resource metadata", () => {
  const challenge = buildOAuthChallenge(config());
  assert.match(challenge, /resource_metadata="https:\/\/wow-v17-mcp\.example\/\.well-known\/oauth-protected-resource\/mcp"/);
  assert.match(challenge, /error="invalid_token"/);
});

test("valid signed OAuth token is bound to issuer, audience, subject, client and V17 permissions", async () => {
  const auth = await verifyOAuthAccessToken(makeJwt(), config(), {
    jwks: TEST_JWKS,
    nowMs: 1_800_000_000 * 1000,
  });
  assert.equal(auth.mode, "oauth");
  assert.equal(auth.subject, SUBJECT);
  assert.equal(auth.clientId, CLIENT_ID);
  assert.equal(auth.principalKey, `${ISSUER}|${SUBJECT}|${CLIENT_ID}`);
  assert.equal(auth.permissions.has("wow.predictions.score"), true);
  assert.equal(hasOperationPermission(auth, "scoreWowPickRequest"), true);
  assert.equal(requiredPermission("settleWowV17Recommendations"), "wow.settlements.write");
});

test("OAuth token with wrong audience fails closed before tool execution", async () => {
  await assert.rejects(
    verifyOAuthAccessToken(makeJwt({ aud: "https://other-resource.example/mcp" }), config(), {
      jwks: TEST_JWKS,
      nowMs: 1_800_000_000 * 1000,
    }),
    (error) => error.code === "MCP_OAUTH_AUDIENCE_MISMATCH",
  );
});

test("OAuth token with wrong issuer, expired lifetime, or unapproved subject fails closed", async () => {
  const nowMs = 1_800_000_000 * 1000;
  await assert.rejects(
    verifyOAuthAccessToken(makeJwt({ iss: "https://attacker.example" }), config(), { jwks: TEST_JWKS, nowMs }),
    (error) => error.code === "MCP_OAUTH_ISSUER_MISMATCH",
  );
  await assert.rejects(
    verifyOAuthAccessToken(makeJwt({ exp: 1_799_999_999 }), config(), { jwks: TEST_JWKS, nowMs }),
    (error) => error.code === "MCP_OAUTH_TOKEN_EXPIRED",
  );
  await assert.rejects(
    verifyOAuthAccessToken(makeJwt({ sub: "unapproved-user" }), config(), { jwks: TEST_JWKS, nowMs }),
    (error) => error.code === "MCP_OAUTH_SUBJECT_NOT_ALLOWED",
  );
});

test("symmetric or unsigned JWT algorithms are rejected even with structurally valid claims", async () => {
  await assert.rejects(
    verifyOAuthAccessToken(makeJwt({}, { alg: "HS256" }), config(), {
      jwks: TEST_JWKS,
      nowMs: 1_800_000_000 * 1000,
    }),
    (error) => error.code === "MCP_OAUTH_ALGORITHM_REJECTED",
  );
});

test("OAuth tool descriptors request standard identity scopes while V17 authorization stays claim-based", () => {
  const tools = getV17ToolDescriptors("oauth", ["openid", "email"]);
  assert.deepEqual(tools.map((entry) => entry.name), V17_TOOL_NAMES);
  assert.equal(tools.length, 16);
  for (const tool of tools) {
    assert.deepEqual(tool.securitySchemes, [{ type: "oauth2", scopes: ["openid", "email"] }], tool.name);
    assert.equal(tool.securitySchemes[0].scopes.some((scope) => scope.startsWith("wow.")), false);
  }
});

test("missing V17 permission is denied even for an otherwise authenticated OAuth identity", () => {
  const auth = {
    mode: "oauth",
    permissions: new Set(["wow.runtime.read"]),
  };
  assert.equal(hasOperationPermission(auth, "getWowV17BackendHealth"), true);
  assert.equal(hasOperationPermission(auth, "scoreWowPickRequest"), false);
  assert.equal(hasOperationPermission(auth, "recordWowV17Recommendations"), false);
});
