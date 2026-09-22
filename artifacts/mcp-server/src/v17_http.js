import http from "node:http";
import { randomUUID, timingSafeEqual } from "node:crypto";

import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { isInitializeRequest } from "@modelcontextprotocol/sdk/types.js";

import { CAN_EXECUTE, TERMINAL_AUTHORITY } from "./v17_contract.js";
import { createV17McpServer } from "./v17_server.js";

const PORT = Number(process.env.PORT || "3000");
const AUTH_MODE = (process.env.WOW_MCP_AUTH_MODE || "shared-token").toLowerCase();
const SHARED_TOKEN = process.env.WOW_MCP_SHARED_TOKEN || "";
const MAX_BODY_BYTES = Number(process.env.WOW_MCP_MAX_BODY_BYTES || String(2 * 1024 * 1024));

const sessions = new Map();

function json(res, status, payload, extraHeaders = {}) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
    "Cache-Control": "no-store",
    ...extraHeaders,
  });
  res.end(body);
}

function secureEqual(a, b) {
  const aa = Buffer.from(a || "");
  const bb = Buffer.from(b || "");
  if (aa.length !== bb.length || aa.length === 0) return false;
  return timingSafeEqual(aa, bb);
}

function authorize(req, res) {
  if (AUTH_MODE === "disabled") return true;
  if (AUTH_MODE !== "shared-token") {
    json(res, 503, {
      error: "MCP_AUTH_MODE_UNSUPPORTED",
      message: "This migration gateway currently supports shared-token acceptance testing only. OAuth 2.1 must be bound before production ChatGPT migration.",
    });
    return false;
  }
  if (!SHARED_TOKEN) {
    json(res, 503, {
      error: "MCP_AUTH_NOT_CONFIGURED",
      message: "WOW_MCP_SHARED_TOKEN is not configured. The gateway fails closed rather than exposing private/stateful V17 tools anonymously.",
    });
    return false;
  }
  const header = String(req.headers.authorization || "");
  const token = header.startsWith("Bearer ") ? header.slice(7) : "";
  if (!secureEqual(token, SHARED_TOKEN)) {
    json(
      res,
      401,
      { error: "MCP_AUTH_REQUIRED", message: "Valid MCP gateway authorization is required." },
      { "WWW-Authenticate": 'Bearer realm="wow-v17-mcp"' },
    );
    return false;
  }
  return true;
}

async function readJsonBody(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) {
      const error = new Error("MCP_REQUEST_TOO_LARGE");
      error.code = "MCP_REQUEST_TOO_LARGE";
      throw error;
    }
    chunks.push(chunk);
  }
  if (!chunks.length) return {};
  const text = Buffer.concat(chunks).toString("utf8");
  return JSON.parse(text);
}

async function createSession() {
  let transport;
  const server = createV17McpServer();
  transport = new StreamableHTTPServerTransport({
    sessionIdGenerator: () => randomUUID(),
    onsessioninitialized: (sessionId) => {
      sessions.set(sessionId, { transport, server });
    },
  });
  transport.onclose = () => {
    if (transport.sessionId) sessions.delete(transport.sessionId);
  };
  await server.connect(transport);
  return transport;
}

async function handleMcp(req, res) {
  if (!authorize(req, res)) return;

  const sessionId = String(req.headers["mcp-session-id"] || "");
  const existing = sessionId ? sessions.get(sessionId) : null;

  if (req.method === "POST") {
    let body;
    try {
      body = await readJsonBody(req);
    } catch (error) {
      return json(res, error?.code === "MCP_REQUEST_TOO_LARGE" ? 413 : 400, {
        jsonrpc: "2.0",
        id: null,
        error: { code: -32700, message: error?.message || "Invalid JSON request body" },
      });
    }

    let transport = existing?.transport;
    if (!transport && !sessionId && isInitializeRequest(body)) {
      transport = await createSession();
    }
    if (!transport) {
      return json(res, 400, {
        jsonrpc: "2.0",
        id: body?.id ?? null,
        error: { code: -32000, message: "Missing or invalid MCP session. Initialize the Streamable HTTP session first." },
      });
    }
    return transport.handleRequest(req, res, body);
  }

  if (req.method === "GET" || req.method === "DELETE") {
    if (!existing?.transport) {
      return json(res, 400, {
        jsonrpc: "2.0",
        id: null,
        error: { code: -32000, message: "Missing or invalid MCP session." },
      });
    }
    return existing.transport.handleRequest(req, res);
  }

  res.writeHead(405, { Allow: "GET, POST, DELETE" });
  res.end();
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url || "/", `http://${req.headers.host || "localhost"}`);
    if (url.pathname === "/healthz" && req.method === "GET") {
      return json(res, 200, {
        ok: true,
        service: "wow-v17-mcp",
        transport: "streamable-http",
        terminal_authority: TERMINAL_AUTHORITY,
        can_execute: CAN_EXECUTE,
        auth_mode: AUTH_MODE,
        backend_key_present: Boolean(process.env.WOW_ACTION_API_KEY),
      });
    }
    if (url.pathname === "/mcp") return await handleMcp(req, res);
    return json(res, 404, { error: "NOT_FOUND" });
  } catch (error) {
    console.error("wow-v17-mcp request failure", error?.message || String(error));
    if (!res.headersSent) {
      return json(res, 500, { error: "MCP_GATEWAY_INTERNAL_ERROR" });
    }
    res.end();
  }
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`wow-v17-mcp listening on :${PORT}; can_execute=false; terminal_authority=${TERMINAL_AUTHORITY}`);
});
