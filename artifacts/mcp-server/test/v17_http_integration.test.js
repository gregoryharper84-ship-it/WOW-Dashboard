import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { generateKeyPairSync, sign } from "node:crypto";
import http from "node:http";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

import { V17_TOOL_NAMES } from "../src/v17_contract.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const packageRoot = path.resolve(here, "..");

async function waitForHealth(url, child) {
  let lastError;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    if (child.exitCode !== null) {
      throw new Error(`V17 MCP HTTP server exited before health check; code=${child.exitCode}`);
    }
    try {
      const response = await fetch(url);
      if (response.ok) return response.json();
      lastError = new Error(`health HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw lastError || new Error("V17 MCP HTTP server did not become healthy");
}

async function closeChild(child) {
  if (child.exitCode === null) child.kill("SIGTERM");
  await new Promise((resolve) => {
    if (child.exitCode !== null) return resolve();
    child.once("exit", resolve);
    setTimeout(resolve, 1000).unref();
  });
}

async function listen(server) {
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("test server did not bind a TCP port");
  return address.port;
}

function makeRs256Jwt(privateKey, payload, kid = "oauth-http-test-key") {
  const header = { alg: "RS256", typ: "JWT", kid };
  const encodedHeader = Buffer.from(JSON.stringify(header)).toString("base64url");
  const encodedPayload = Buffer.from(JSON.stringify(payload)).toString("base64url");
  const signingInput = `${encodedHeader}.${encodedPayload}`;
  const signature = sign("RSA-SHA256", Buffer.from(signingInput), privateKey).toString("base64url");
  return `${signingInput}.${signature}`;
}

test("Streamable HTTP performs MCP initialize and exposes exactly the governed V17 tools", async (t) => {
  const port = 32000 + (process.pid % 10000);
  const child = spawn(process.execPath, ["src/v17_http.js"], {
    cwd: packageRoot,
    env: {
      ...process.env,
      PORT: String(port),
      WOW_MCP_AUTH_MODE: "disabled",
      WOW_ACTION_API_KEY: "integration-placeholder-never-sent",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });

  let stdout = "";
  let stderr = "";
  child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
  child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });

  t.after(async () => closeChild(child));

  const health = await waitForHealth(`http://127.0.0.1:${port}/healthz`, child);
  assert.equal(health.ok, true, `${stdout}\n${stderr}`);
  assert.equal(health.can_execute, false);
  assert.equal(health.terminal_authority, "V17_TERMINAL_REDUCER");

  const client = new Client({ name: "wow-v17-mcp-ci", version: "1.0.0" }, { capabilities: {} });
  const transport = new StreamableHTTPClientTransport(new URL(`http://127.0.0.1:${port}/mcp`));
  t.after(async () => {
    try { await client.close(); } catch {}
  });

  await client.connect(transport);
  const listed = await client.listTools();
  const names = listed.tools.map((tool) => tool.name);

  assert.deepEqual(names, V17_TOOL_NAMES);
  assert.equal(names.length, 16);
  assert.equal(names.some((name) => /bet|wager|order/i.test(name) && /place|execute|submit|cancel|approve/i.test(name)), false);
});

test("OAuth HTTP mode publishes protected-resource metadata, rejects anonymous callers, authenticates MCP, and enforces V17 permissions", async (t) => {
  const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const publicJwk = publicKey.export({ format: "jwk" });
  publicJwk.kid = "oauth-http-test-key";
  publicJwk.alg = "RS256";
  publicJwk.use = "sig";

  const issuerServer = http.createServer((req, res) => {
    if (req.url === "/jwks.json") {
      const body = JSON.stringify({ keys: [publicJwk] });
      res.writeHead(200, { "content-type": "application/json", "content-length": Buffer.byteLength(body) });
      return res.end(body);
    }
    res.writeHead(404);
    res.end();
  });
  const issuerPort = await listen(issuerServer);
  t.after(async () => new Promise((resolve) => issuerServer.close(resolve)));

  const gatewayPort = 34000 + (process.pid % 10000);
  const publicUrl = `http://127.0.0.1:${gatewayPort}`;
  const resource = `${publicUrl}/mcp`;
  const issuer = `http://127.0.0.1:${issuerPort}/auth/v1`;
  const subject = "11111111-2222-3333-4444-555555555555";
  const clientId = "chatgpt-oauth-http-test";
  const now = Math.floor(Date.now() / 1000);
  const token = makeRs256Jwt(privateKey, {
    iss: issuer,
    aud: resource,
    sub: subject,
    client_id: clientId,
    iat: now - 5,
    exp: now + 600,
    wow_mcp_permissions: ["wow.runtime.read"],
  });

  const child = spawn(process.execPath, ["src/v17_http.js"], {
    cwd: packageRoot,
    env: {
      ...process.env,
      PORT: String(gatewayPort),
      WOW_MCP_AUTH_MODE: "oauth",
      WOW_MCP_PUBLIC_URL: publicUrl,
      WOW_MCP_OAUTH_ISSUER: issuer,
      WOW_MCP_OAUTH_RESOURCE: resource,
      WOW_MCP_OAUTH_JWKS_URL: `http://127.0.0.1:${issuerPort}/jwks.json`,
      WOW_MCP_OAUTH_SCOPES: "openid email",
      WOW_MCP_OAUTH_ALLOWED_SUBJECTS: subject,
      WOW_MCP_OAUTH_PERMISSION_CLAIM: "wow_mcp_permissions",
      WOW_ACTION_API_KEY: "integration-placeholder-never-sent",
      WOW_V17_BACKEND_URL: "http://127.0.0.1:1",
      WOW_MCP_UPSTREAM_TIMEOUT_MS: "100",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stdout = "";
  let stderr = "";
  child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
  child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
  t.after(async () => closeChild(child));

  const health = await waitForHealth(`${publicUrl}/healthz`, child);
  assert.equal(health.ok, true, `${stdout}\n${stderr}`);
  assert.equal(health.auth_mode, "oauth");
  assert.equal(health.oauth_metadata_ready, true);
  assert.equal(health.can_execute, false);

  const metadataResponse = await fetch(`${publicUrl}/.well-known/oauth-protected-resource/mcp`);
  assert.equal(metadataResponse.status, 200);
  const metadata = await metadataResponse.json();
  assert.equal(metadata.resource, resource);
  assert.deepEqual(metadata.authorization_servers, [issuer]);
  assert.deepEqual(metadata.scopes_supported, ["openid", "email"]);

  const anonymous = await fetch(`${publicUrl}/mcp`, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json, text/event-stream" },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: { protocolVersion: "2025-03-26", capabilities: {}, clientInfo: { name: "anonymous", version: "1.0.0" } },
    }),
  });
  assert.equal(anonymous.status, 401);
  assert.match(anonymous.headers.get("www-authenticate") || "", /resource_metadata=/);
  assert.match(anonymous.headers.get("www-authenticate") || "", /oauth-protected-resource\/mcp/);

  const client = new Client({ name: "wow-v17-mcp-oauth-ci", version: "1.0.0" }, { capabilities: {} });
  const transport = new StreamableHTTPClientTransport(new URL(resource), {
    requestInit: { headers: { Authorization: `Bearer ${token}` } },
  });
  t.after(async () => {
    try { await client.close(); } catch {}
  });

  await client.connect(transport);
  const listed = await client.listTools();
  assert.deepEqual(listed.tools.map((tool) => tool.name), V17_TOOL_NAMES);
  assert.equal(listed.tools.length, 16);
  for (const tool of listed.tools) {
    assert.deepEqual(tool.securitySchemes, [{ type: "oauth2", scopes: ["openid", "email"] }], tool.name);
  }

  const denied = await client.callTool({
    name: "scoreWowPickRequest",
    arguments: { request_id: "oauth-http-denied", rows: [] },
  });
  assert.equal(denied.isError, true);
  assert.equal(denied.structuredContent.gateway_status, "MCP_OAUTH_PERMISSION_DENIED");
  assert.equal(denied.structuredContent.required_permission, "wow.predictions.score");
  assert.equal(denied.structuredContent.can_execute, false);
  assert.match(JSON.stringify(denied._meta || {}), /mcp\/www_authenticate/);
});
