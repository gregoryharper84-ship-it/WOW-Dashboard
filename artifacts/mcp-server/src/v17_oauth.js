import { createPublicKey, timingSafeEqual, verify as verifySignature } from "node:crypto";

import { V17_OPERATIONS } from "./v17_contract.js";

export const DEFAULT_OAUTH_IDENTITY_SCOPES = Object.freeze(["openid", "email"]);
export const DEFAULT_PERMISSION_CLAIM = "wow_mcp_permissions";

const JWKS_CACHE = new Map();

function words(value) {
  if (Array.isArray(value)) return value.map(String).map((item) => item.trim()).filter(Boolean);
  return String(value || "").split(/[\s,]+/).map((item) => item.trim()).filter(Boolean);
}

function withoutTrailingSlash(value) {
  return String(value || "").replace(/\/+$/, "");
}

export function createOAuthConfig(env = process.env) {
  const publicUrl = withoutTrailingSlash(env.WOW_MCP_PUBLIC_URL);
  const issuer = withoutTrailingSlash(env.WOW_MCP_OAUTH_ISSUER);
  const resource = String(env.WOW_MCP_OAUTH_RESOURCE || (publicUrl ? `${publicUrl}/mcp` : ""));
  const jwksUrl = String(env.WOW_MCP_OAUTH_JWKS_URL || (issuer ? `${issuer}/.well-known/jwks.json` : ""));
  const identityScopes = words(env.WOW_MCP_OAUTH_SCOPES || DEFAULT_OAUTH_IDENTITY_SCOPES.join(" "));
  const allowedSubjects = new Set(words(env.WOW_MCP_OAUTH_ALLOWED_SUBJECTS));
  const permissionClaim = String(env.WOW_MCP_OAUTH_PERMISSION_CLAIM || DEFAULT_PERMISSION_CLAIM);
  const jwksTtlMs = Number(env.WOW_MCP_OAUTH_JWKS_TTL_MS || "300000");
  const fetchTimeoutMs = Number(env.WOW_MCP_OAUTH_FETCH_TIMEOUT_MS || "10000");
  return {
    publicUrl,
    issuer,
    resource,
    jwksUrl,
    identityScopes,
    allowedSubjects,
    permissionClaim,
    jwksTtlMs: Number.isFinite(jwksTtlMs) && jwksTtlMs > 0 ? jwksTtlMs : 300000,
    fetchTimeoutMs: Number.isFinite(fetchTimeoutMs) && fetchTimeoutMs > 0 ? fetchTimeoutMs : 10000,
  };
}

export function assertOAuthConfig(config, { requireAllowedSubjects = true } = {}) {
  const missing = [];
  if (!config?.publicUrl) missing.push("WOW_MCP_PUBLIC_URL");
  if (!config?.issuer) missing.push("WOW_MCP_OAUTH_ISSUER");
  if (!config?.resource) missing.push("WOW_MCP_OAUTH_RESOURCE");
  if (!config?.jwksUrl) missing.push("WOW_MCP_OAUTH_JWKS_URL");
  if (!Array.isArray(config?.identityScopes) || config.identityScopes.length === 0) missing.push("WOW_MCP_OAUTH_SCOPES");
  if (requireAllowedSubjects && (!config?.allowedSubjects || config.allowedSubjects.size === 0)) {
    missing.push("WOW_MCP_OAUTH_ALLOWED_SUBJECTS");
  }
  if (missing.length) {
    const error = new Error(`OAuth resource-server configuration is incomplete: ${missing.join(", ")}`);
    error.code = "MCP_OAUTH_NOT_CONFIGURED";
    error.missing = missing;
    throw error;
  }
  return true;
}

export function protectedResourceMetadata(config) {
  assertOAuthConfig(config, { requireAllowedSubjects: false });
  return {
    resource: config.resource,
    authorization_servers: [config.issuer],
    scopes_supported: [...config.identityScopes],
  };
}

export function protectedResourceMetadataUrl(config) {
  assertOAuthConfig(config, { requireAllowedSubjects: false });
  return `${config.publicUrl}/.well-known/oauth-protected-resource/mcp`;
}

function quoteAuthValue(value) {
  return String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

export function buildOAuthChallenge(
  config,
  { error = "invalid_token", description = "Authentication is required to access WOW V17 MCP." } = {},
) {
  const metadataUrl = protectedResourceMetadataUrl(config);
  return `Bearer resource_metadata="${quoteAuthValue(metadataUrl)}", error="${quoteAuthValue(error)}", error_description="${quoteAuthValue(description)}"`;
}

export function parseBearerToken(headerValue) {
  const header = String(headerValue || "");
  const match = /^Bearer\s+(.+)$/i.exec(header);
  return match ? match[1].trim() : "";
}

function decodeJsonSegment(segment, label) {
  try {
    return JSON.parse(Buffer.from(segment, "base64url").toString("utf8"));
  } catch {
    const error = new Error(`Invalid JWT ${label}.`);
    error.code = "MCP_OAUTH_TOKEN_INVALID";
    throw error;
  }
}

function parseJwt(token) {
  const parts = String(token || "").split(".");
  if (parts.length !== 3 || parts.some((part) => !part)) {
    const error = new Error("OAuth access token is not a valid compact JWT.");
    error.code = "MCP_OAUTH_TOKEN_INVALID";
    throw error;
  }
  return {
    header: decodeJsonSegment(parts[0], "header"),
    payload: decodeJsonSegment(parts[1], "payload"),
    signingInput: Buffer.from(`${parts[0]}.${parts[1]}`, "utf8"),
    signature: Buffer.from(parts[2], "base64url"),
  };
}

async function loadJwks(config, options = {}) {
  if (options.jwks) return options.jwks;
  const now = Date.now();
  const cached = JWKS_CACHE.get(config.jwksUrl);
  if (cached && cached.expiresAt > now) return cached.value;

  const fetchImpl = options.fetchImpl || globalThis.fetch;
  if (typeof fetchImpl !== "function") {
    const error = new Error("Global fetch is unavailable for JWKS retrieval.");
    error.code = "MCP_OAUTH_JWKS_UNAVAILABLE";
    throw error;
  }

  let response;
  try {
    response = await fetchImpl(config.jwksUrl, {
      headers: { Accept: "application/json", "User-Agent": "wow-v17-mcp-gateway/1.0" },
      signal: AbortSignal.timeout(config.fetchTimeoutMs),
    });
  } catch (cause) {
    const error = new Error(`Unable to retrieve OAuth signing keys: ${cause?.message || String(cause)}`);
    error.code = "MCP_OAUTH_JWKS_UNAVAILABLE";
    throw error;
  }
  if (!response.ok) {
    const error = new Error(`OAuth JWKS endpoint returned HTTP ${response.status}.`);
    error.code = "MCP_OAUTH_JWKS_UNAVAILABLE";
    throw error;
  }
  const value = await response.json();
  if (!value || !Array.isArray(value.keys) || value.keys.length === 0) {
    const error = new Error("OAuth JWKS endpoint returned no signing keys.");
    error.code = "MCP_OAUTH_JWKS_INVALID";
    throw error;
  }
  JWKS_CACHE.set(config.jwksUrl, { value, expiresAt: now + config.jwksTtlMs });
  return value;
}

function verifyJwtSignature(parsed, jwks) {
  const algorithm = String(parsed.header?.alg || "");
  if (!new Set(["RS256", "ES256"]).has(algorithm)) {
    const error = new Error(`JWT signing algorithm '${algorithm || "missing"}' is not allowed.`);
    error.code = "MCP_OAUTH_ALGORITHM_REJECTED";
    throw error;
  }
  const kid = String(parsed.header?.kid || "");
  const candidates = jwks.keys.filter((key) => !kid || key.kid === kid);
  if (!candidates.length) {
    const error = new Error("No OAuth signing key matches the token kid.");
    error.code = "MCP_OAUTH_SIGNING_KEY_NOT_FOUND";
    throw error;
  }

  for (const jwk of candidates) {
    try {
      const key = createPublicKey({ key: jwk, format: "jwk" });
      const verified = algorithm === "RS256"
        ? verifySignature("RSA-SHA256", parsed.signingInput, key, parsed.signature)
        : verifySignature("sha256", parsed.signingInput, { key, dsaEncoding: "ieee-p1363" }, parsed.signature);
      if (verified) return true;
    } catch {
      // Try another key if the JWKS contained overlapping rotations.
    }
  }

  const error = new Error("OAuth access-token signature verification failed.");
  error.code = "MCP_OAUTH_SIGNATURE_INVALID";
  throw error;
}

function audienceContains(audience, expected) {
  return Array.isArray(audience) ? audience.includes(expected) : audience === expected;
}

export function permissionsFromClaims(payload, claimName = DEFAULT_PERMISSION_CLAIM) {
  return new Set(words(payload?.[claimName]));
}

export async function verifyOAuthAccessToken(token, config, options = {}) {
  assertOAuthConfig(config);
  const parsed = parseJwt(token);
  const jwks = await loadJwks(config, options);
  verifyJwtSignature(parsed, jwks);

  const nowSeconds = Math.floor((options.nowMs ?? Date.now()) / 1000);
  const payload = parsed.payload || {};
  if (payload.iss !== config.issuer) {
    const error = new Error("OAuth access-token issuer does not match the configured authorization server.");
    error.code = "MCP_OAUTH_ISSUER_MISMATCH";
    throw error;
  }
  if (!audienceContains(payload.aud, config.resource)) {
    const error = new Error("OAuth access token was not minted for this MCP resource.");
    error.code = "MCP_OAUTH_AUDIENCE_MISMATCH";
    throw error;
  }
  if (!Number.isFinite(payload.exp) || payload.exp <= nowSeconds) {
    const error = new Error("OAuth access token is expired or missing exp.");
    error.code = "MCP_OAUTH_TOKEN_EXPIRED";
    throw error;
  }
  if (payload.nbf != null && (!Number.isFinite(payload.nbf) || payload.nbf > nowSeconds)) {
    const error = new Error("OAuth access token is not yet valid.");
    error.code = "MCP_OAUTH_TOKEN_NOT_YET_VALID";
    throw error;
  }
  const subject = String(payload.sub || "");
  if (!subject) {
    const error = new Error("OAuth access token is missing a stable subject.");
    error.code = "MCP_OAUTH_SUBJECT_MISSING";
    throw error;
  }
  if (!config.allowedSubjects.has(subject)) {
    const error = new Error("OAuth subject is not authorized for this private WOW MCP resource.");
    error.code = "MCP_OAUTH_SUBJECT_NOT_ALLOWED";
    throw error;
  }
  const clientId = String(payload.client_id || "");
  if (!clientId) {
    const error = new Error("OAuth access token is missing client_id.");
    error.code = "MCP_OAUTH_CLIENT_ID_MISSING";
    throw error;
  }

  return {
    mode: "oauth",
    issuer: config.issuer,
    subject,
    clientId,
    principalKey: `${config.issuer}|${subject}|${clientId}`,
    permissions: permissionsFromClaims(payload, config.permissionClaim),
    claims: payload,
  };
}

export function requiredPermission(operationName) {
  return String(V17_OPERATIONS[operationName]?.scope || "");
}

export function hasOperationPermission(authContext, operationName) {
  if (!authContext || authContext.mode !== "oauth") return true;
  const required = requiredPermission(operationName);
  return Boolean(required && authContext.permissions instanceof Set && authContext.permissions.has(required));
}

export function sharedTokenAuthContext(mode = "shared-token") {
  return {
    mode,
    principalKey: mode,
    permissions: new Set(Object.values(V17_OPERATIONS).map((operation) => operation.scope).filter(Boolean)),
  };
}

export function secureTokenEqual(a, b) {
  const aa = Buffer.from(String(a || ""));
  const bb = Buffer.from(String(b || ""));
  if (aa.length !== bb.length || aa.length === 0) return false;
  return timingSafeEqual(aa, bb);
}
