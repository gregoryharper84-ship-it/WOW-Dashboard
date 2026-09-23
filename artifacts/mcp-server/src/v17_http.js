import http from "node:http";
import { randomUUID, timingSafeEqual } from "node:crypto";

import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { isInitializeRequest } from "@modelcontextprotocol/sdk/types.js";

import { CAN_EXECUTE, TERMINAL_AUTHORITY } from "./v17_contract.js";
import {
  V17OAuthError,
  assertOAuthPermissions,
  bearerChallenge,
  buildProtectedResourceMetadata,
  oauthConfig,
  oauthSessionFingerprint,
  requiredPermissionsForMcpBody,
  verifyOAuthAccessToken,
} from "./v17_oauth.js";
import { createV17McpServer } from "./v17_server.js";

const PORT = Number(process.env.PORT || "3000");
const AUTH_MODE = (process.env.WOW_MCP_AUTH_MODE || "shared-token").toLowerCase();
const SHARED_TOKEN = process.env.WOW_MCP_SHARED_TOKEN || "";
const BACKEND_CREDENTIAL_MODE = (process.env.WOW_MCP_BACKEND_CREDENTIAL_MODE || "action").toLowerCase();
const MAX_BODY_BYTES = Number(process.env.WOW_MCP_MAX_BODY_BYTES || String(2 * 1024 * 1024));
const OAUTH_CONFIG = oauthConfig();

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

function bearerToken(req) {
  const header = String(req.headers.authorization || "");
  return header.startsWith("Bearer ") ? header.slice(7) : "";
}

function oauthResourceUrl(origin) {
  return OAUTH_CONFIG.resourceUrl || `${origin}/mcp`;
}

function oauthMetadataUrl(origin) {
  return `${origin}/.well-known/oauth-protected-resource/mcp`;
}

function oauthFailure(res, error, origin) {
  const status = error instanceof V17OAuthError ? error.status : 401;
  const code = error instanceof V17OAuthError ? error.code : "MCP_OAUTH_TOKEN_INVALID";
  const payload = {
    error: code,
    message: error?.message || "OAuth authorization failed.",
    can_execute: false,
  };
  if (error instanceof V17OAuthError && Object.keys(error.details || {}).length) {
    payload.details = error.details;
  }
  const challengeError = status === 403 ? "insufficient_scope" : "invalid_token";
  return json(res, status, payload, {
    "WWW-Authenticate": bearerChallenge(oauthMetadataUrl(origin), challengeError),
  });
}

async function authenticate(req, res, origin) {
  if (AUTH_MODE === "disabled") return { mode: "disabled" };

  if (AUTH_MODE === "shared-token") {
    if (!SHARED_TOKEN) {
      json(res, 503, {
        error: "MCP_AUTH_NOT_CONFIGURED",
        message: "WOW_MCP_SHARED_TOKEN is not configured. The gateway fails closed rather than exposing private/stateful V17 tools anonymously.",
        can_execute: false,
      });
      return null;
    }
    if (!secureEqual(bearerToken(req), SHARED_TOKEN)) {
      json(
        res,
        401,
        { error: "MCP_AUTH_REQUIRED", message: "Valid MCP gateway authorization is required.", can_execute: false },
        { "WWW-Authenticate": 'Bearer realm="wow-v17-mcp"' },
      );
      return null;
    }
    return { mode: "shared-token" };
  }

  if (AUTH_MODE === "oauth") {
    const token = bearerToken(req);
    if (!token) {
      json(
        res,
        401,
        { error: "MCP_AUTH_REQUIRED", message: "OAuth bearer authorization is required.", can_execute: false },
        { "WWW-Authenticate": bearerChallenge(oauthMetadataUrl(origin)) },
      );
      return null;
    }
    try {
      return await verifyOAuthAccessToken(token, OAUTH_CONFIG);
    } catch (error) {
      oauthFailure(res, error, origin);
      return null;
    }
  }

  json(res, 503, {
    error: "MCP_AUTH_MODE_UNSUPPORTED",
    message: "WOW_MCP_AUTH_MODE must be disabled, shared-token, or oauth.",
    can_execute: false,
  });
  return null;
}

function authorizeBody(authContext, body, res, origin) {
  try {
    assertOAuthPermissions(authContext, requiredPermissionsForMcpBody(body));
    return true;
  } catch (error) {
    oauthFailure(res, error, origin);
    return false;
  }
}

function authorizeSession(authContext, session, res, origin) {
  if (session?.authFingerprint !== oauthSessionFingerprint(authContext)) {
    oauthFailure(
      res,
      new V17OAuthError("MCP_OAUTH_SESSION_IDENTITY_MISMATCH", "MCP session is bound to a different authenticated identity.", { status: 403 }),
      origin,
    );
    return false;
  }
  try {
    assertOAuthPermissions(authContext, new Set(["wow.runtime.read"]));
    return true;
  } catch (error) {
    oauthFailure(res, error, origin);
    return false;
  }
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
  const server = createV17McpServer();
  const authFingerprint = oauthSessionFingerprint(authContext);
  transport = new StreamableHTTPServerTransport({
    sessionIdGenerator: () => randomUUID(),
    onsessioninitialized: (sessionId) => {
      sessions.set(sessionId, { transport, server, authFingerprint });
    },
  });
  transport.onclose = () => {
    if (transport.sessionId) sessions.delete(transport.sessionId);
  };
  await server.connect(transport);
  return transport;
}

async function handleMcp(req, res, origin) {
  const authContext = await authenticate(req, res, origin);
  if (!authContext) return;

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

    if (!authorizeBody(authContext, body, res, origin)) return;
    if (existing && !authorizeSession(authContext, existing, res, origin)) return;

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
    if (!authorizeSession(authContext, existing, res, origin)) return;
    return existing.transport.handleRequest(req, res);
  }

  res.writeHead(405, { Allow: "GET, POST, DELETE" });
  res.end();
}

function protectedResourceMetadata(origin) {
  return buildProtectedResourceMetadata({
    resourceUrl: oauthResourceUrl(origin),
    authorizationServers: OAUTH_CONFIG.authorizationServers,
    scopesSupported: OAUTH_CONFIG.scopesSupported,
  });
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url || "/", `http://${req.headers.host || "localhost"}`);
    const forwardedProto = String(req.headers["x-forwarded-proto"] || "").split(",")[0].trim();
    const origin = forwardedProto ? `${forwardedProto}://${url.host}` : url.origin;

    if (
      (url.pathname === "/.well-known/oauth-protected-resource" || url.pathname === "/.well-known/oauth-protected-resource/mcp")
      && req.method === "GET"
    ) {
      try {
        return json(res, 200, protectedResourceMetadata(origin));
      } catch (error) {
        return oauthFailure(res, error, origin);
      }
    }

    if (url.pathname === "/healthz" && req.method === "GET") {
      const backendKeyPresent = BACKEND_CREDENTIAL_MODE === "acceptance"
        ? Boolean(process.env.WOW_MCP_ACCEPTANCE_API_KEY)
        : Boolean(process.env.WOW_ACTION_API_KEY);
      return json(res, 200, {
        ok: true,
        service: "wow-v17-mcp",
        transport: "streamable-http",
        terminal_authority: TERMINAL_AUTHORITY,
        can_execute: CAN_EXECUTE,
        auth_mode: AUTH_MODE,
        oauth_configured: Boolean(OAUTH_CONFIG.issuer && OAUTH_CONFIG.jwksUrl && OAUTH_CONFIG.allowedClientIds.size),
        backend_credential_mode: BACKEND_CREDENTIAL_MODE,
        backend_key_present: backendKeyPresent,
      });
    }
    if (url.pathname === "/mcp") return await handleMcp(req, res, origin);
    return json(res, 404, { error: "NOT_FOUND" });
  } catch (error) {
    console.error("wow-v17-mcp request failure", error?.message || String(error));
    if (!res.headersSent) {
      return json(res, 500, { error: "MCP_GATEWAY_INTERNAL_ERROR", can_execute: false });
    }
    res.end();
  }
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`wow-v17-mcp listening on :${PORT}; can_execute=false; terminal_authority=${TERMINAL_AUTHORITY}`);
});
