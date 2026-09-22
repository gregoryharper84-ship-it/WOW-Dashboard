import http from "node:http";
import { randomUUID } from "node:crypto";

import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { isInitializeRequest } from "@modelcontextprotocol/sdk/types.js";

import { CAN_EXECUTE, TERMINAL_AUTHORITY } from "./v17_contract.js";
import {
  assertOAuthConfig,
  buildOAuthChallenge,
  createOAuthConfig,
  parseBearerToken,
  protectedResourceMetadata,
  secureTokenEqual,
  sharedTokenAuthContext,
  verifyOAuthAccessToken,
} from "./v17_oauth.js";
import { createV17McpServer } from "./v17_server.js";

const PORT = Number(process.env.PORT || "3000");
const AUTH_MODE = (process.env.WOW_MCP_AUTH_MODE || "shared-token").toLowerCase();
const SHARED_TOKEN = process.env.WOW_MCP_SHARED_TOKEN || "";
const MAX_BODY_BYTES = Number(process.env.WOW_MCP_MAX_BODY_BYTES || String(2 * 1024 * 1024));
const OAUTH_CONFIG = createOAuthConfig();

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

function oauthChallengeHeaders(error = "invalid_token", description = "Valid OAuth authorization is required.") {
  return { "WWW-Authenticate": buildOAuthChallenge(OAUTH_CONFIG, { error, description }) };
}

async function authorize(req, res) {
  if (AUTH_MODE === "disabled") return sharedTokenAuthContext("disabled");

  if (AUTH_MODE === "shared-token") {
    if (!SHARED_TOKEN) {
      json(res, 503, {
        error: "MCP_AUTH_NOT_CONFIGURED",
        message: "WOW_MCP_SHARED_TOKEN is not configured. The gateway fails closed rather than exposing private/stateful V17 tools anonymously.",
      });
      return null;
    }
    const token = parseBearerToken(req.headers.authorization);
    if (!secureTokenEqual(token, SHARED_TOKEN)) {
      json(
        res,
        401,
        { error: "MCP_AUTH_REQUIRED", message: "Valid MCP gateway authorization is required." },
        { "WWW-Authenticate": 'Bearer realm="wow-v17-mcp"' },
      );
      return null;
    }
    return sharedTokenAuthContext("shared-token");
  }

  if (AUTH_MODE === "oauth") {
    try {
      assertOAuthConfig(OAUTH_CONFIG);
    } catch (error) {
      json(res, 503, {
        error: error?.code || "MCP_OAUTH_NOT_CONFIGURED",
        message: "OAuth resource-server configuration is incomplete; the gateway is failing closed.",
        missing: Array.isArray(error?.missing) ? error.missing : [],
      });
      return null;
    }

    const token = parseBearerToken(req.headers.authorization);
    if (!token) {
      json(
        res,
        401,
        { error: "MCP_OAUTH_REQUIRED", message: "OAuth authorization is required for this MCP resource." },
        oauthChallengeHeaders("invalid_token", "OAuth authorization is required for this MCP resource."),
      );
      return null;
    }

    try {
      return await verifyOAuthAccessToken(token, OAUTH_CONFIG);
    } catch (error) {
      json(
        res,
        401,
        {
          error: "MCP_OAUTH_TOKEN_REJECTED",
          oauth_error: error?.code || "MCP_OAUTH_TOKEN_INVALID",
          message: "OAuth access token failed resource-server validation.",
        },
        oauthChallengeHeaders("invalid_token", "OAuth access token failed resource-server validation."),
      );
      return null;
    }
  }

  json(res, 503, {
    error: "MCP_AUTH_MODE_UNSUPPORTED",
    message: `Unsupported WOW_MCP_AUTH_MODE '${AUTH_MODE}'.`,
  });
  return null;
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

async function createSession(authContext) {
  let transport;
  const authRef = { current: authContext };
  const server = createV17McpServer({
    authMode: AUTH_MODE,
    auth: authRef,
    oauthConfig: OAUTH_CONFIG,
  });
  transport = new StreamableHTTPServerTransport({
    sessionIdGenerator: () => randomUUID(),
    onsessioninitialized: (sessionId) => {
      sessions.set(sessionId, {
        transport,
        server,
        authRef,
        principalKey: authContext.principalKey,
      });
    },
  });
  transport.onclose = () => {
    if (transport.sessionId) sessions.delete(transport.sessionId);
  };
  await server.connect(transport);
  return transport;
}

async function handleMcp(req, res) {
  const authContext = await authorize(req, res);
  if (!authContext) return;

  const sessionId = String(req.headers["mcp-session-id"] || "");
  const existing = sessionId ? sessions.get(sessionId) : null;

  if (existing && existing.principalKey !== authContext.principalKey) {
    const headers = AUTH_MODE === "oauth"
      ? oauthChallengeHeaders("invalid_token", "MCP session identity does not match the authenticated OAuth principal.")
      : { "WWW-Authenticate": 'Bearer realm="wow-v17-mcp"' };
    return json(res, 401, {
      error: "MCP_SESSION_PRINCIPAL_MISMATCH",
      message: "MCP session identity does not match the authenticated principal.",
    }, headers);
  }
  if (existing?.authRef) existing.authRef.current = authContext;

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
      transport = await createSession(authContext);
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

function oauthMetadata(res) {
  try {
    return json(res, 200, protectedResourceMetadata(OAUTH_CONFIG));
  } catch (error) {
    return json(res, 503, {
      error: error?.code || "MCP_OAUTH_NOT_CONFIGURED",
      message: "OAuth protected-resource metadata is not configured.",
    });
  }
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url || "/", `http://${req.headers.host || "localhost"}`);
    if (
      req.method === "GET"
      && (url.pathname === "/.well-known/oauth-protected-resource" || url.pathname === "/.well-known/oauth-protected-resource/mcp")
    ) {
      return oauthMetadata(res);
    }
    if (url.pathname === "/healthz" && req.method === "GET") {
      let oauthMetadataReady = false;
      if (AUTH_MODE === "oauth") {
        try {
          assertOAuthConfig(OAUTH_CONFIG);
          oauthMetadataReady = true;
        } catch {}
      }
      return json(res, 200, {
        ok: true,
        service: "wow-v17-mcp",
        transport: "streamable-http",
        terminal_authority: TERMINAL_AUTHORITY,
        can_execute: CAN_EXECUTE,
        auth_mode: AUTH_MODE,
        backend_key_present: Boolean(process.env.WOW_ACTION_API_KEY),
        oauth_metadata_ready: oauthMetadataReady,
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
