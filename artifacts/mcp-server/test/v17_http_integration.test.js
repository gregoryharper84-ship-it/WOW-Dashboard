import assert from "node:assert/strict";
import { spawn } from "node:child_process";
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

  t.after(async () => {
    if (child.exitCode === null) child.kill("SIGTERM");
    await new Promise((resolve) => {
      if (child.exitCode !== null) return resolve();
      child.once("exit", resolve);
      setTimeout(resolve, 1000).unref();
    });
  });

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

test("Streamable HTTP health-only mode hides scoring tools and refuses invocation", async (t) => {
  const port = 42000 + (process.pid % 10000);
  const child = spawn(process.execPath, ["src/v17_http.js"], {
    cwd: packageRoot,
    env: {
      ...process.env,
      PORT: String(port),
      WOW_MCP_AUTH_MODE: "disabled", // loopback-only test transport; never an internet service
      WOW_MCP_HEALTH_ONLY: "1",
      WOW_ACTION_API_KEY: "integration-placeholder-never-sent",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  t.after(async () => {
    if (child.exitCode === null) child.kill("SIGTERM");
    await new Promise((resolve) => {
      if (child.exitCode !== null) return resolve();
      child.once("exit", resolve);
      setTimeout(resolve, 1000).unref();
    });
  });

  const base = `http://127.0.0.1:${port}`;
  const health = await waitForHealth(`${base}/healthz`, child);
  assert.equal(health.health_only, true);
  assert.equal(health.can_execute, false);
  assert.equal(health.terminal_authority, "V17_TERMINAL_REDUCER");

  const client = new Client({ name: "wow-v17-health-only-ci", version: "1.0.0" }, { capabilities: {} });
  const transport = new StreamableHTTPClientTransport(new URL(`${base}/mcp`));
  t.after(async () => {
    try { await client.close(); } catch {}
  });
  await client.connect(transport);
  const listed = await client.listTools();
  assert.deepEqual(listed.tools.map((entry) => entry.name), ["getWowV17BackendHealth"]);
  assert.equal(listed.tools[0].annotations.readOnlyHint, true);

  const denied = await client.callTool({ name: "scoreWowPickRequest", arguments: { rows: [] } });
  assert.equal(denied.isError, true);
  assert.equal(denied.structuredContent.gateway_status, "MCP_TOOL_NOT_REGISTERED");
  assert.equal(denied.structuredContent.can_execute, false);
});
