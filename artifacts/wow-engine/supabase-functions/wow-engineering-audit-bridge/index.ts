import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const EXPECTED_TOKEN_SHA256 = "3ae63812c223264461697823a2477bef3e58bcc1d8856226609c5243d18251eb";
const ALLOWED_TABLES = new Set([
  "wow_agent_audit_events",
  "wow_engineering_audit_work_items",
  "wow_engineering_audit_findings",
  "wow_engineering_backlog",
  "wow_engineering_auditor_runtime",
]);
const ALLOWED_OPERATIONS = new Set(["select", "insert", "upsert", "update"]);
const ALLOWED_FILTERS = new Set(["eq", "lte"]);
const FORBIDDEN_PAYLOAD_KEYS = new Set([
  "model_probability",
  "sporting_probability",
  "projected_probability",
  "calibrated_probability",
  "implied_probability",
  "no_vig_probability",
  "probability_override",
  "probability_blend",
  "pick_probability",
  "wager",
  "market_order",
]);
const IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_]*$/;

function json(status: number, body: Record<string, unknown>) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}

async function sha256(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

function constantTimeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let diff = 0;
  for (let i = 0; i < left.length; i++) diff |= left.charCodeAt(i) ^ right.charCodeAt(i);
  return diff === 0;
}

function validColumns(value: string): boolean {
  if (value === "*") return true;
  const columns = value.split(",").map((item) => item.trim()).filter(Boolean);
  return columns.length > 0 && columns.every((column) => IDENTIFIER.test(column));
}

function payloadBoundary(value: unknown): string | null {
  if (Array.isArray(value)) {
    for (const nested of value) {
      const violation = payloadBoundary(nested);
      if (violation) return violation;
    }
    return null;
  }
  if (!value || typeof value !== "object") return null;
  for (const [key, nested] of Object.entries(value as Record<string, unknown>)) {
    if (FORBIDDEN_PAYLOAD_KEYS.has(key)) return `FORBIDDEN_FIELD:${key}`;
    if (key === "can_execute" && nested !== false) return "CAN_EXECUTE_MUST_REMAIN_FALSE";
    if (key === "terminal_authority" && nested !== "V17_TERMINAL_REDUCER") {
      return "TERMINAL_AUTHORITY_MISMATCH";
    }
    const violation = payloadBoundary(nested);
    if (violation) return violation;
  }
  return null;
}

Deno.serve(async (req: Request) => {
  try {
    if (req.method !== "POST") return json(405, { error: "METHOD_NOT_ALLOWED" });
    const contentLength = Number(req.headers.get("content-length") || "0");
    if (Number.isFinite(contentLength) && contentLength > 65536) {
      return json(413, { error: "REQUEST_TOO_LARGE" });
    }

    const token = req.headers.get("x-wow-engineering-token") || "";
    if (!token || !constantTimeEqual(await sha256(token), EXPECTED_TOKEN_SHA256)) {
      return json(401, { error: "UNAUTHORIZED" });
    }

    const body = await req.json();
    if (body?.can_execute !== false || body?.terminal_authority !== "V17_TERMINAL_REDUCER") {
      return json(400, { error: "V17_GOVERNANCE_BOUNDARY_REJECTED" });
    }

    const table = String(body?.table || "");
    const operation = String(body?.operation || "").toLowerCase();
    const columns = String(body?.columns || "*");
    const filters = Array.isArray(body?.filters) ? body.filters : [];
    const limit = body?.limit == null ? null : Number(body.limit);
    const onConflict = body?.on_conflict == null ? null : String(body.on_conflict);
    const payload = body?.payload ?? null;

    if (!ALLOWED_TABLES.has(table)) return json(403, { error: "TABLE_FORBIDDEN" });
    if (!ALLOWED_OPERATIONS.has(operation)) return json(403, { error: "OPERATION_FORBIDDEN" });
    if (!validColumns(columns)) return json(400, { error: "INVALID_COLUMNS" });
    if (filters.length > 8) return json(400, { error: "TOO_MANY_FILTERS" });
    if (limit != null && (!Number.isInteger(limit) || limit < 1 || limit > 1000)) {
      return json(400, { error: "INVALID_LIMIT" });
    }
    if (onConflict && !onConflict.split(",").every((value: string) => IDENTIFIER.test(value.trim()))) {
      return json(400, { error: "INVALID_ON_CONFLICT" });
    }
    if (operation !== "select") {
      if (!payload || Array.isArray(payload) || typeof payload !== "object") {
        return json(400, { error: "INVALID_PAYLOAD" });
      }
      const boundaryViolation = payloadBoundary(payload);
      if (boundaryViolation) return json(400, { error: boundaryViolation });
    }

    const supabaseUrl = Deno.env.get("SUPABASE_URL");
    const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
    if (!supabaseUrl || !serviceRoleKey) return json(500, { error: "SERVER_CREDENTIALS_UNAVAILABLE" });
    const supabase = createClient(supabaseUrl, serviceRoleKey, {
      auth: { persistSession: false, autoRefreshToken: false },
    });

    let query: any;
    if (operation === "select") {
      query = supabase.from(table).select(columns);
    } else if (operation === "insert") {
      query = supabase.from(table).insert(payload).select("*");
    } else if (operation === "upsert") {
      query = supabase.from(table).upsert(payload, onConflict ? { onConflict } : undefined).select("*");
    } else {
      query = supabase.from(table).update(payload).select("*");
    }

    for (const raw of filters) {
      const operator = String(raw?.operator || "").toLowerCase();
      const column = String(raw?.column || "");
      if (!ALLOWED_FILTERS.has(operator) || !IDENTIFIER.test(column)) {
        return json(400, { error: "FILTER_FORBIDDEN" });
      }
      if (operator === "eq") query = query.eq(column, raw?.value);
      if (operator === "lte") query = query.lte(column, raw?.value);
    }
    if (limit != null) query = query.limit(limit);

    const { data, error } = await query;
    if (error) {
      console.error("WOW_ENGINEERING_AUDIT_BRIDGE query_failed", {
        table,
        operation,
        code: error.code || "UNKNOWN",
      });
      return json(500, { error: "QUERY_FAILED", code: error.code || "UNKNOWN" });
    }
    return json(200, { data: data ?? [], can_execute: false, terminal_authority: "V17_TERMINAL_REDUCER" });
  } catch (error) {
    console.error("WOW_ENGINEERING_AUDIT_BRIDGE request_failed", {
      error_type: error instanceof Error ? error.name : "UNKNOWN",
    });
    return json(500, { error: "REQUEST_FAILED" });
  }
});
