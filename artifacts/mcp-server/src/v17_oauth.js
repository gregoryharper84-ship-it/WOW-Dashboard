import { createPublicKey, verify as verifySignature } from "node:crypto";

import { V17_OPERATIONS } from "./v17_contract.js";

const DEFAULT_AUDIENCE = "authenticated";
const DEFAULT_CLOCK_SKEW_SECONDS = 30;
const DEFAULT_JWKS_CACHE_MS = 5 * 60 * 1000;
const SUPPORTED_ALGORITHMS = new Set(["RS256", "ES256"]);
const jwksCache = new Map();

export const WOW_PERMISSION_SET = Object.freeze(
  [...new Set(Object.values(V17_OPERATIONS).map((operation) => operation.scope))].sort(),
);

export class V17OAuthError extends Error {
  constructor(code, message, { status = 401, details = {} } = {}) {
    super(message || code);
    this.name = "V17OAuthError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

function splitCsv(value) {
  return String(value || "")
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
}

function decodeBase64Url(value) {
  try {
    return Buffer.from(String(value || ""), "base64url");
  } catch {
    throw new V17OAuthError("MCP_OAUTH_TOKEN_MALFORMED", "OAuth access token is not valid base64url.");
  }
}

function decodeJsonSegment(value, label) {
  try {
    return JSON.parse(decodeBase64Url(value).toString("utf8"));
  } catch (error) {
    if (error instanceof V17OAuthError) throw error;
    throw new V17OAuthError("MCP_OAUTH_TOKEN_MALFORMED", `OAuth access token ${label} is not valid JSON.`);
  }
}

function parseJwt(token) {
  const parts = String(token || "").split(".");
  if (parts.length !== 3 || parts.some((part) => !part)) {
    throw new V17OAuthError("MCP_OAUTH_TOKEN_MALFORMED", "OAuth access token must be a three-segment JWT.");
  }
  const header = decodeJsonSegment(parts[0], "header");
  const payload = decodeJsonSegment(parts[1], "payload");
  return {
    header,
    payload,
    signingInput: Buffer.from(`${parts[0]}.${parts[1]}`, "ascii"),
    signature: decodeBase64Url(parts[2]),
  };
}

function audienceMatches(actual, expected) {
  if (Array.isArray(actual)) return actual.includes(expected);
  return actual === expected;
}

function normalizePermissionValues(value) {
  if (Array.isArray(value)) {
    return value.filter((entry) => typeof entry === "string").flatMap((entry) => entry.split(/\s+/)).filter(Boolean);
  }
  if (typeof value === "string") return value.split(/\s+/).filter(Boolean);
  return [];
}

function claimAtPath(payload, path) {
  return String(path || "")
    .split(".")
    .filter(Boolean)
    .reduce((value, key) => (value && typeof value === "object" ? value[key] : undefined), payload);
}

export function extractOAuthPermissions(payload, claimPath = "app_metadata.wow_permissions") {
  const permissions = new Set();
  for (const value of [payload?.scope, payload?.scp, claimAtPath(payload, claimPath)]) {
    for (const permission of normalizePermissionValues(value)) permissions.add(permission);
  }
  return permissions;
}

async function readJwks(jwksUrl, { fetchImpl = fetch, cacheMs = DEFAULT_JWKS_CACHE_MS } = {}) {
  const now = Date.now();
  const cached = jwksCache.get(jwksUrl);
  if (cached && now - cached.fetchedAt < cacheMs) return cached.jwks;

  let response;
  try {
    response = await fetchImpl(jwksUrl, {
      method: "GET",
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(10000),
    });
  } catch (error) {
    throw new V17OAuthError("MCP_OAUTH_JWKS_UNAVAILABLE", "OAuth JWKS endpoint could not be reached.", {
      status: 503,
      details: { cause: error?.name || "FETCH_FAILURE" },
    });
  }
  if (!response.ok) {
    throw new V17OAuthError("MCP_OAUTH_JWKS_UNAVAILABLE", "OAuth JWKS endpoint returned a non-success response.", {
      status: 503,
      details: { http_status: response.status },
    });
  }

  const jwks = await response.json();
  if (!jwks || !Array.isArray(jwks.keys) || jwks.keys.length === 0) {
    throw new V17OAuthError("MCP_OAUTH_JWKS_INVALID", "OAuth JWKS response does not contain signing keys.", { status: 503 });
  }
  jwksCache.set(jwksUrl, { fetchedAt: now, jwks });
  return jwks;
}

function verifyJwtSignature({ header, signingInput, signature }, jwk) {
  const alg = header?.alg;
  if (!SUPPORTED_ALGORITHMS.has(alg)) {
    throw new V17OAuthError("MCP_OAUTH_ALG_UNSUPPORTED", "OAuth access token signing algorithm is not allowed.");
  }
  if (jwk.alg && jwk.alg !== alg) {
    throw new V17OAuthError("MCP_OAUTH_KEY_MISMATCH", "OAuth access token signing key does not match its algorithm.");
  }

  let key;
  try {
    key = createPublicKey({ key: jwk, format: "jwk" });
  } catch {
    throw new V17OAuthError("MCP_OAUTH_JWKS_INVALID", "OAuth signing key could not be imported.", { status: 503 });
  }

  const verified = alg === "ES256"
    ? verifySignature("sha256", signingInput, { key, dsaEncoding: "ieee-p1363" }, signature)
    : verifySignature("RSA-SHA256", signingInput, key, signature);
  if (!verified) {
    throw new V17OAuthError("MCP_OAUTH_SIGNATURE_INVALID", "OAuth access token signature verification failed.");
  }
}

export function oauthConfig(env = process.env) {
  const issuer = String(env.WOW_MCP_OAUTH_ISSUER || "").replace(/\/$/, "");
  const audience = String(env.WOW_MCP_OAUTH_AUDIENCE || DEFAULT_AUDIENCE);
  const jwksUrl = String(env.WOW_MCP_OAUTH_JWKS_URL || (issuer ? `${issuer}/.well-known/jwks.json` : ""));
  const authorizationServers = splitCsv(env.WOW_MCP_OAUTH_AUTHORIZATION_SERVERS || issuer);
  const allowedClientIds = new Set(splitCsv(env.WOW_MCP_OAUTH_ALLOWED_CLIENT_IDS));
  const permissionClaim = String(env.WOW_MCP_OAUTH_PERMISSION_CLAIM || "app_metadata.wow_permissions");
  const scopesSupported = splitCsv(env.WOW_MCP_OAUTH_SCOPES_SUPPORTED || "openid,email,profile");
  const resourceUrl = String(env.WOW_MCP_OAUTH_RESOURCE_URL || "").replace(/\/$/, "");

  return {
    issuer,
    audience,
    jwksUrl,
    authorizationServers,
    allowedClientIds,
    permissionClaim,
    scopesSupported,
    resourceUrl,
  };
}

export async function verifyOAuthAccessToken(token, {
  issuer,
  audience = DEFAULT_AUDIENCE,
  jwksUrl,
  allowedClientIds = new Set(),
  permissionClaim = "app_metadata.wow_permissions",
  clockSkewSeconds = DEFAULT_CLOCK_SKEW_SECONDS,
  nowSeconds = Math.floor(Date.now() / 1000),
  fetchImpl = fetch,
  jwks = null,
} = {}) {
  if (!issuer || !jwksUrl || !(allowedClientIds instanceof Set) || allowedClientIds.size === 0) {
    throw new V17OAuthError(
      "MCP_OAUTH_NOT_CONFIGURED",
      "OAuth mode requires issuer, JWKS, and an explicit allowed client allowlist.",
      { status: 503 },
    );
  }

  const parsed = parseJwt(token);
  const { header, payload } = parsed;
  if (!SUPPORTED_ALGORITHMS.has(header?.alg) || typeof header?.kid !== "string" || !header.kid) {
    throw new V17OAuthError("MCP_OAUTH_TOKEN_HEADER_INVALID", "OAuth access token must use an allowed asymmetric algorithm and key id.");
  }

  const keySet = jwks || await readJwks(jwksUrl, { fetchImpl });
  const signingKey = keySet.keys?.find((key) => key.kid === header.kid && (!key.use || key.use === "sig"));
  if (!signingKey) {
    throw new V17OAuthError("MCP_OAUTH_SIGNING_KEY_NOT_FOUND", "OAuth access token signing key is not present in JWKS.");
  }
  verifyJwtSignature(parsed, signingKey);

  if (payload?.iss !== issuer) {
    throw new V17OAuthError("MCP_OAUTH_ISSUER_INVALID", "OAuth access token issuer is not trusted.");
  }
  if (!audienceMatches(payload?.aud, audience)) {
    throw new V17OAuthError("MCP_OAUTH_AUDIENCE_INVALID", "OAuth access token audience is not valid for WOW MCP.");
  }
  if (!Number.isFinite(payload?.exp) || nowSeconds >= payload.exp + clockSkewSeconds) {
    throw new V17OAuthError("MCP_OAUTH_TOKEN_EXPIRED", "OAuth access token is expired or missing expiration.");
  }
  if (Number.isFinite(payload?.nbf) && nowSeconds + clockSkewSeconds < payload.nbf) {
    throw new V17OAuthError("MCP_OAUTH_TOKEN_NOT_YET_VALID", "OAuth access token is not yet valid.");
  }
  if (Number.isFinite(payload?.iat) && payload.iat > nowSeconds + clockSkewSeconds) {
    throw new V17OAuthError("MCP_OAUTH_TOKEN_IAT_INVALID", "OAuth access token issued-at time is in the future.");
  }
  if (typeof payload?.sub !== "string" || !payload.sub) {
    throw new V17OAuthError("MCP_OAUTH_SUBJECT_MISSING", "OAuth access token is missing subject identity.");
  }
  if (typeof payload?.client_id !== "string" || !allowedClientIds.has(payload.client_id)) {
    throw new V17OAuthError("MCP_OAUTH_CLIENT_NOT_ALLOWED", "OAuth client is not authorized for WOW MCP.");
  }

  return {
    mode: "oauth",
    subject: payload.sub,
    clientId: payload.client_id,
    sessionId: typeof payload.session_id === "string" ? payload.session_id : null,
    permissions: extractOAuthPermissions(payload, permissionClaim),
    expiresAt: payload.exp,
    payload,
  };
}

export function requiredPermissionsForMcpBody(body) {
  const messages = Array.isArray(body) ? body : [body];
  const required = new Set();
  for (const message of messages) {
    const method = message?.method;
    if (!method) continue;
    if (method === "tools/call") {
      const toolName = message?.params?.name;
      const operation = V17_OPERATIONS[toolName];
      if (!operation) {
        throw new V17OAuthError("MCP_OAUTH_TOOL_UNKNOWN", "OAuth caller requested an unknown V17 tool.", { status: 403 });
      }
      required.add(operation.scope);
      continue;
    }
    required.add("wow.runtime.read");
  }
  return required;
}

export function assertOAuthPermissions(authContext, requiredPermissions) {
  if (authContext?.mode !== "oauth") return true;
  const missing = [...requiredPermissions].filter((permission) => !authContext.permissions.has(permission));
  if (missing.length) {
    throw new V17OAuthError("MCP_OAUTH_PERMISSION_DENIED", "OAuth token lacks required WOW MCP permission.", {
      status: 403,
      details: { required_permissions: missing },
    });
  }
  return true;
}

export function oauthSessionFingerprint(authContext) {
  if (authContext?.mode !== "oauth") return authContext?.mode || "unknown";
  return `oauth:${authContext.subject}:${authContext.clientId}`;
}

export function buildProtectedResourceMetadata({ resourceUrl, authorizationServers, scopesSupported = [] } = {}) {
  if (!resourceUrl || !Array.isArray(authorizationServers) || authorizationServers.length === 0) {
    throw new V17OAuthError("MCP_OAUTH_METADATA_NOT_CONFIGURED", "OAuth protected-resource metadata is not configured.", { status: 503 });
  }
  return {
    resource: resourceUrl,
    authorization_servers: authorizationServers,
    bearer_methods_supported: ["header"],
    scopes_supported: scopesSupported,
    resource_name: "WOW V17 MCP",
    wow_permissions_supported: WOW_PERMISSION_SET,
  };
}

export function bearerChallenge(resourceMetadataUrl, error = null) {
  const params = [`resource_metadata="${String(resourceMetadataUrl).replaceAll('"', "")}"`];
  if (error) params.push(`error="${String(error).replaceAll('"', "")}"`);
  return `Bearer ${params.join(", ")}`;
}
