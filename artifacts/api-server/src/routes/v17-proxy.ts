import { timingSafeEqual } from "node:crypto";
import { Router, type Request, type Response, type NextFunction } from "express";

const CANONICAL_BACKEND = "https://wow-governed-probability-engine.onrender.com";

interface ProxyOptions {
  backendUrl?: string;
  backendKey?: string;
  dashboardKey?: string;
  allowLocalBackend?: boolean;
  fetchImpl?: typeof fetch;
}

function failure(res: Response, status: number, code: string, message: string, extra: Record<string, unknown> = {}) {
  return res.status(status).json({ ok: false, code, message, can_execute: false, ...extra });
}

export function requireDashboardKey(key: string | undefined) {
  return (req: Request, res: Response, next: NextFunction) => {
    if (!key) return failure(res, 503, "DASHBOARD_AUTH_NOT_CONFIGURED", "Dashboard access is not configured on the server.");
    const supplied = req.get("X-Dashboard-Key") ?? "";
    const actual = Buffer.from(supplied);
    const expected = Buffer.from(key);
    if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) {
      return failure(res, 401, "DASHBOARD_AUTH_REQUIRED", "Enter the dashboard access key to use this protected service.");
    }
    return next();
  };
}

function safeBackendUrl(raw: string, allowLocalBackend: boolean): string | null {
  try {
    const url = new URL(raw);
    const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
    if (url.username || url.password || url.search || url.hash || url.pathname !== "/") return null;
    if (url.protocol !== "https:" && !(allowLocalBackend && local && url.protocol === "http:")) return null;
    return url.origin;
  } catch { return null; }
}

function grantsExecution(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  if (Array.isArray(value)) return value.some(grantsExecution);
  const record = value as Record<string, unknown>;
  return record.can_execute === true || Object.values(record).some(grantsExecution);
}

function containsCredential(value: unknown, credentials: string[]): boolean {
  if (typeof value === "string") return credentials.some(secret => value.includes(secret));
  if (!value || typeof value !== "object") return false;
  if (Array.isArray(value)) return value.some(item => containsCredential(item, credentials));
  return Object.entries(value).some(([key, item]) =>
    credentials.some(secret => key.includes(secret)) || containsCredential(item, credentials));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function invalidReceiptRows(payload: Record<string, unknown>, required: boolean): boolean {
  if (!("rows" in payload)) return required;
  if (!Array.isArray(payload.rows)) return true;
  return payload.rows.some(row => !isRecord(row) ||
    ("matches" in row && (!Array.isArray(row.matches) || row.matches.some(match => !isRecord(match)))));
}

// This deliberately has no wildcard upstream forwarding. Internal routes and
// caller-supplied URLs never inherit the server-held Action credential.
export function createV17Proxy(options: ProxyOptions = {}) {
  const router = Router();
  const backendKey = options.backendKey ?? process.env.WOW_V17_BACKEND_API_KEY ?? "";
  const dashboardKey = options.dashboardKey ?? process.env.WOW_DASHBOARD_API_KEY ?? "";
  const backendUrl = safeBackendUrl(
    options.backendUrl ?? process.env.WOW_V17_BACKEND_URL ?? CANONICAL_BACKEND,
    options.allowLocalBackend ?? process.env.WOW_V17_ALLOW_LOCAL_BACKEND === "true",
  );
  const fetchImpl = options.fetchImpl ?? fetch;

  router.use((_req, res, next) => {
    res.set("Cache-Control", "no-store");
    next();
  });
  router.use(requireDashboardKey(dashboardKey));

  function forward(path: string, score = false) {
    return async (req: Request, res: Response) => {
      if (!backendUrl) return failure(res, 503, "BACKEND_URL_INVALID", "The server backend URL must be an HTTPS origin; local HTTP requires an explicit development override.");
      if (!backendKey) return failure(res, 503, "BACKEND_AUTH_NOT_CONFIGURED", "The server-held V17 backend credential is not configured.");
      if (backendKey === dashboardKey) return failure(res, 503, "DASHBOARD_CREDENTIAL_SEPARATION_REQUIRED", "Dashboard access and backend Action credentials must be distinct.");
      if (Object.keys(req.query).length) return failure(res, 400, "DASHBOARD_QUERY_UNSUPPORTED", "This operation does not accept query parameters.");
      const requestId = typeof req.body?.request_id === "string" ? req.body.request_id : null;
      if (req.method === "POST" && (!req.body || !Array.isArray(req.body.rows) || req.body.rows.length < 1 || req.body.rows.length > 50)) {
        return failure(res, 400, "DASHBOARD_INPUT_INVALID", "Provide a canonical request with between 1 and 50 rows.");
      }
      // Once dispatched, an unusable response cannot prove that scoring did
      // not persist. Every withheld/invalid completion must recover receipts.
      const recovery = score ? { request_id: requestId, recovery_operation: "prediction-receipts/lookup", completion_unknown: true } : {};
      try {
        const response = await fetchImpl(`${backendUrl}${path}`, {
          method: req.method,
          headers: { Accept: "application/json", "Content-Type": "application/json", Authorization: `Bearer ${backendKey}` },
          ...(req.method === "POST" ? { body: JSON.stringify(req.body) } : {}),
          redirect: "error",
          signal: AbortSignal.timeout(score ? 330_000 : 30_000),
        });
        const text = await response.text();
        if (text.includes(backendKey) || text.includes(dashboardKey)) {
          return failure(res, 502, "UPSTREAM_CREDENTIAL_DISCLOSURE_BLOCKED", "The backend returned protected credential material; its response was withheld.", recovery);
        }
        let payload: unknown;
        try { payload = JSON.parse(text); }
        catch { return failure(res, 502, "UPSTREAM_RESPONSE_INVALID", "The V17 backend did not return a JSON receipt.", recovery); }
        if (containsCredential(payload, [backendKey, dashboardKey])) {
          return failure(res, 502, "UPSTREAM_CREDENTIAL_DISCLOSURE_BLOCKED", "The backend returned protected credential material; its response was withheld.", recovery);
        }
        if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
          return failure(res, 502, "UPSTREAM_RESPONSE_INVALID", "The V17 backend returned an invalid receipt envelope.", recovery);
        }
        if ((score || path === "/v17/prediction-receipts/lookup") && invalidReceiptRows(payload as Record<string, unknown>, response.ok)) {
          return failure(res, 502, "UPSTREAM_RESPONSE_INVALID", "The V17 backend returned an invalid row receipt envelope.", recovery);
        }
        if (grantsExecution(payload)) return failure(res, 502, "UPSTREAM_GOVERNANCE_VIOLATION", "The backend response attempted to grant execution authority and was withheld.", recovery);
        // A native server error after dispatch cannot prove scoring did not
        // persist. Recover with the submitted identity, never an upstream ID.
        if (score && response.status >= 500 && response.status < 600) {
          return res.status(response.status).json({ ...payload, ...recovery });
        }
        // Preserve native HTTP errors, blockers, publication flags and immutable
        // identifiers. The dashboard does not create or normalize V17 outcomes.
        return res.status(response.status).json(payload);
      } catch (error) {
        const timeout = error instanceof Error && ["TimeoutError", "AbortError"].includes(error.name);
        return failure(res, timeout ? 504 : 502, timeout ? "ACTION_TRANSPORT_TIMEOUT" : "ACTION_TRANSPORT_FAILURE",
          score ? "No terminal response was received. Keep this request ID and recover immutable receipts before retrying scoring." : "The V17 backend could not be reached.",
          recovery);
      }
    };
  }

  router.get("/host-contract", forward("/v17/host-contract"));
  router.get("/capabilities", forward("/v17/capabilities"));
  router.post("/score-pick-request", forward("/score-pick-request", true));
  router.post("/prediction-receipts/lookup", forward("/v17/prediction-receipts/lookup"));
  router.use((_req, res) => failure(res, 404, "DASHBOARD_ROUTE_UNSUPPORTED", "This V17 operation is not exposed by the dashboard."));
  return router;
}
