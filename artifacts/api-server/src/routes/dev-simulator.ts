/**
 * dev-simulator.ts — WOW Data Hub Dev Tools
 *
 * DEV ONLY — not for production betting guidance.
 *
 * Routes:
 *   POST /api/dev/source-status-sim  — simulate any DataStatus through the pipeline
 *   POST /api/admin/smoke-test       — full hub health + safety smoke test
 */
import { Router, type Request, type Response } from "express";
import { logger } from "../lib/logger";

const router = Router();

const FLASK_BASE = process.env["SCORING_API_URL"] ?? "http://localhost:25643";
const API_KEY    = process.env["SCORING_API_KEY"]  ?? "";
const API_BASE   = process.env["API_SERVER_URL"]   ?? "http://localhost:8080/api";

// ── Valid DataStatus values ───────────────────────────────────────────────────
const VALID_SOURCE_STATUSES = [
  "RETRIEVED",
  "RECONSTRUCTED",
  "PROXY_ONLY",
  "DATA_UNOBTAINABLE",
  "INPUT_FAILURE",
  "SOURCE_CONFLICT",
  "NOT_CALLED",
  "FAILED",
] as const;
type DataStatus = typeof VALID_SOURCE_STATUSES[number];

// Approval cap rules per source status (enforced before gate scoring)
const APPROVAL_CAPS: Record<DataStatus, string> = {
  RETRIEVED:         "FINAL_APPROVED",        // may score normally if all gates pass
  RECONSTRUCTED:     "MONEY_QUALIFIED",       // ok if reconstruction documented
  PROXY_ONLY:        "MODEL_QUALIFIED_HOLD",  // cannot approve; max hold
  DATA_UNOBTAINABLE: "REJECT_DATA_QUALITY",   // cannot approve
  INPUT_FAILURE:     "REJECT_DATA_QUALITY",   // raw present but scoring blocked
  SOURCE_CONFLICT:   "SOURCE_CONFLICT",       // conflict blocks money labels
  NOT_CALLED:        "MODEL_QUALIFIED_HOLD",  // not unavailable but cap below money
  FAILED:            "REJECT_DATA_QUALITY",   // blocks approval
};

const CAN_REACH_MONEY_QUALIFIED: Record<DataStatus, boolean> = {
  RETRIEVED:         true,
  RECONSTRUCTED:     true,
  PROXY_ONLY:        false,
  DATA_UNOBTAINABLE: false,
  INPUT_FAILURE:     false,
  SOURCE_CONFLICT:   false,
  NOT_CALLED:        false,
  FAILED:            false,
};

const CAN_REACH_FINAL_APPROVED: Record<DataStatus, boolean> = {
  RETRIEVED:         true,
  RECONSTRUCTED:     false, // reconstruction alone not sufficient
  PROXY_ONLY:        false,
  DATA_UNOBTAINABLE: false,
  INPUT_FAILURE:     false,
  SOURCE_CONFLICT:   false,
  NOT_CALLED:        false,
  FAILED:            false,
};

async function callFlask(path: string, body: Record<string, unknown>) {
  const r = await fetch(`${FLASK_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(15000),
  });
  if (!r.ok) {
    const txt = await r.text().catch(() => "");
    throw new Error(`Flask HTTP ${r.status}: ${txt.slice(0, 120)}`);
  }
  return r.json() as Promise<Record<string, unknown>>;
}

// ── POST /api/dev/source-status-sim ──────────────────────────────────────────
router.post("/source-status-sim", async (req: Request, res: Response) => {
  const {
    source_status,
    sport         = "WNBA",
    prop_type     = "points",
    side          = "LESS",
    line          = 20.5,
    include_market = true,
    include_l10    = true,
    include_status = true,
    include_payout = true,
  } = req.body as {
    source_status:   string;
    sport?:          string;
    prop_type?:      string;
    side?:           string;
    line?:           number;
    include_market?: boolean;
    include_l10?:    boolean;
    include_status?: boolean;
    include_payout?: boolean;
  };

  if (!VALID_SOURCE_STATUSES.includes(source_status as DataStatus)) {
    return res.status(400).json({
      ok: false,
      error: `Invalid source_status. Must be one of: ${VALID_SOURCE_STATUSES.join(", ")}`,
    });
  }

  const ss = source_status as DataStatus;
  const cap = APPROVAL_CAPS[ss];
  const canMoney   = CAN_REACH_MONEY_QUALIFIED[ss];
  const canFinal   = CAN_REACH_FINAL_APPROVED[ss];

  // Build a synthetic normalized prop
  const normalizedProp = {
    player:         `Sim Player [${ss}]`,
    team:           "SIM TEAM",
    opponent:       "OPP TEAM",
    sport:          sport.toUpperCase(),
    prop_type,
    side:           side.toUpperCase(),
    line,
    platform:       "simulator",
    payout_context: include_payout ? "2-pick Power" : null,
    source_status:  ss,
    source_grade:   ss === "RETRIEVED" ? "A" : ss === "RECONSTRUCTED" ? "B" : "UNKNOWN",
    source:         "simulator",
    timestamp:      new Date().toISOString(),
  };

  // Determine blockers from missing fields
  const blockers: string[] = [];
  if (!include_l10)    blockers.push("L10_MISSING");
  if (!include_market) blockers.push("MARKET_COMPARISON_MISSING");
  if (!include_status) blockers.push("STATUS_TIMESTAMP_MISSING");
  if (!include_payout) blockers.push("PAYOUT_CONTEXT_MISSING");

  if (ss === "DATA_UNOBTAINABLE") blockers.push("SOURCE_DATA_UNAVAILABLE");
  if (ss === "PROXY_ONLY")        blockers.push("PROXY_SOURCE_APPROVAL_CAP");
  if (ss === "SOURCE_CONFLICT")   blockers.push("CONFLICTING_SOURCE_DATA");
  if (ss === "FAILED")            blockers.push("SOURCE_FETCH_FAILED");
  if (ss === "INPUT_FAILURE")     blockers.push("INPUT_PROCESSING_FAILURE");
  if (ss === "NOT_CALLED")        blockers.push("SOURCE_NOT_QUERIED");

  // Score through Flask /final-lock
  let scoredResult: Record<string, unknown> = {};
  let scoringError: string | null = null;
  let terminalBucket = "NOT_SCORED";

  try {
    scoredResult = await callFlask("/final-lock", {
      player:         normalizedProp.player,
      team:           normalizedProp.team,
      opponent:       normalizedProp.opponent,
      sport:          normalizedProp.sport,
      prop:           normalizedProp.prop_type,
      side:           normalizedProp.side,
      line:           normalizedProp.line,
      pp_line:        normalizedProp.line,
      platform:       normalizedProp.platform,
      payout_context: normalizedProp.payout_context ?? "2-pick Power",
      source:         normalizedProp.source,
      source_status:  normalizedProp.source_status,
    });

    terminalBucket = String(
      scoredResult["terminal_label"] ??
      scoredResult["classification"] ??
      scoredResult["label"] ??
      "UNKNOWN"
    );

    // Normalize Flask classification → terminal_label
    if (!scoredResult["terminal_label"]) {
      scoredResult["terminal_label"] = terminalBucket;
    }
    scoredResult["can_execute"] = false;
  } catch (err) {
    scoringError = String(err);
    terminalBucket = "DATA_UNOBTAINABLE";
    scoredResult = { error: scoringError, terminal_label: "DATA_UNOBTAINABLE", can_execute: false };
  }

  // Verify safety gate: non-retrievable statuses must never reach money labels
  const MONEY_LABELS = new Set(["MONEY_QUALIFIED", "FINAL_APPROVED"]);
  const actuallyReachedMoney = MONEY_LABELS.has(terminalBucket);
  if (!canMoney && actuallyReachedMoney) {
    logger.warn({ source_status: ss, terminal_bucket: terminalBucket },
      "SAFETY VIOLATION: source_status that should not reach money labels did");
  }

  return res.json({
    dev_only: true,
    disclaimer: "DEV ONLY — simulator output. Not for betting decisions.",
    input_source_status: ss,
    approval_cap:        cap,
    can_reach_money_qualified: canMoney,
    can_reach_final_approved:  canFinal,
    normalized_prop:     normalizedProp,
    scored_result:       scoredResult,
    terminal_bucket:     terminalBucket,
    blockers,
    scoring_error:       scoringError,
    safety_check: {
      expected_blocked_from_money: !canMoney,
      actually_reached_money:      actuallyReachedMoney,
      safety_gate_held:            !canMoney ? !actuallyReachedMoney : true,
    },
  });
});

// ── POST /api/admin/smoke-test ────────────────────────────────────────────────
interface SmokeCheck {
  name:    string;
  status:  "PASS" | "WARN" | "FAIL";
  details: string;
}

async function runCheck(
  name: string,
  fn: () => Promise<string>,
  isCritical = true,
): Promise<SmokeCheck> {
  try {
    const details = await fn();
    return { name, status: "PASS", details };
  } catch (err) {
    return { name, status: isCritical ? "FAIL" : "WARN", details: String(err).slice(0, 200) };
  }
}

router.post("/smoke-test", async (_req: Request, res: Response) => {
  const timestamp = new Date().toISOString();
  const checks: SmokeCheck[] = [];

  // 1. Flask /health
  checks.push(await runCheck("flask_health", async () => {
    const r = await fetch(`${FLASK_BASE}/health`, { signal: AbortSignal.timeout(5000) });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return "Flask /health OK";
  }));

  // 2. GET /api/props/providers
  checks.push(await runCheck("props_providers", async () => {
    const r = await fetch(`${API_BASE}/props/providers`, { signal: AbortSignal.timeout(5000) });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json() as { providers?: Record<string, { configured: boolean }> };
    const configured = Object.values(d.providers ?? {}).filter(p => p.configured).length;
    const total = Object.keys(d.providers ?? {}).length;
    return `${configured}/${total} providers configured`;
  }));

  // 3. POST /api/props/normalize with mock fallback
  let mockProps: Record<string, unknown>[] = [];
  checks.push(await runCheck("props_normalize_mock", async () => {
    const r = await fetch(`${API_BASE}/props/normalize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sport: "NBA", providers: ["mock"] }),
      signal: AbortSignal.timeout(10000),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json() as { props?: Record<string, unknown>[]; data_unobtainable?: boolean };
    mockProps = d.props ?? [];
    if (!d.data_unobtainable) throw new Error("Mock response missing data_unobtainable=true flag");
    const badStatus = mockProps.filter(p => p["source_status"] !== "DATA_UNOBTAINABLE");
    if (badStatus.length > 0) throw new Error(`${badStatus.length} mock props NOT labeled DATA_UNOBTAINABLE`);
    return `${mockProps.length} mock props all labeled DATA_UNOBTAINABLE ✓`;
  }));

  // 4. Mock props cannot approve
  checks.push(await runCheck("mock_cannot_approve", async () => {
    if (mockProps.length === 0) throw new Error("No mock props available to test");
    const r = await fetch(`${API_BASE}/props/score-batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ props: mockProps.slice(0, 2) }),
      signal: AbortSignal.timeout(20000),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json() as { results?: { result: { terminal_label?: string; can_execute?: boolean } }[]; execution_rule?: string };
    const results = d.results ?? [];
    const approvals = results.filter(r =>
      r.result?.terminal_label === "MONEY_QUALIFIED" ||
      r.result?.terminal_label === "FINAL_APPROVED"
    );
    if (approvals.length > 0) throw new Error(`SAFETY FAIL: ${approvals.length} mock props reached approval`);
    const execTrue = results.filter(r => r.result?.can_execute === true);
    if (execTrue.length > 0) throw new Error(`SAFETY FAIL: can_execute=true on ${execTrue.length} rows`);
    if (d.execution_rule !== "READ_ONLY_NO_EXECUTION") {
      throw new Error(`execution_rule unexpected: ${d.execution_rule}`);
    }
    return `${results.length} mock props all rejected, can_execute=false, READ_ONLY_NO_EXECUTION ✓`;
  }));

  // 5. Row count reconciliation
  checks.push(await runCheck("row_count_reconciliation", async () => {
    const testProps = mockProps.slice(0, 3);
    if (testProps.length === 0) return "Skipped — no mock props";
    const r = await fetch(`${API_BASE}/props/score-batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ props: testProps }),
      signal: AbortSignal.timeout(20000),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json() as { count?: number; results?: unknown[] };
    const inCount  = testProps.length;
    const outCount = d.count ?? (d.results ?? []).length;
    if (inCount !== outCount) throw new Error(`ROW COUNT MISMATCH: sent ${inCount}, got ${outCount}`);
    return `${inCount} in → ${outCount} out ✓`;
  }));

  // 6. Odds API live check (warn, not fail, if missing key)
  checks.push(await runCheck("odds_api_live", async () => {
    const r = await fetch(`${API_BASE}/props/providers`, { signal: AbortSignal.timeout(5000) });
    const d = await r.json() as { providers?: Record<string, { configured: boolean }> };
    if (!d.providers?.["odds_api"]?.configured) {
      throw new Error("ODDS_API_KEY not configured — live provider unavailable");
    }
    const norm = await fetch(`${API_BASE}/props/normalize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sport: "baseball_mlb", providers: ["odds_api"] }),
      signal: AbortSignal.timeout(20000),
    });
    if (!norm.ok) throw new Error(`Normalize HTTP ${norm.status}`);
    const nd = await norm.json() as { props?: unknown[]; data_unobtainable?: boolean };
    if (nd.data_unobtainable) throw new Error("Odds API returned no data (data_unobtainable=true)");
    return `${(nd.props ?? []).length} live props fetched from Odds API ✓`;
  }, false /* warn only */));

  // 7. Flask request-log reachable
  checks.push(await runCheck("request_log", async () => {
    const r = await fetch(`${FLASK_BASE}/request-log?limit=1`, {
      headers: { "X-API-Key": API_KEY },
      signal: AbortSignal.timeout(5000),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json() as { count?: number };
    return `request-log OK — ${d.count ?? "?"} total entries`;
  }));

  // 8. Flask leaderboard reachable
  checks.push(await runCheck("leaderboard", async () => {
    const r = await fetch(`${FLASK_BASE}/leaderboard`, {
      headers: { "X-API-Key": API_KEY },
      signal: AbortSignal.timeout(5000),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return "leaderboard OK";
  }));

  // 9. MCP server package present
  checks.push(await runCheck("mcp_server_package", async () => {
    const fs = await import("fs");
    const path = await import("path");
    const mcpPkg = path.join(process.cwd(), "../mcp-server/package.json");
    if (!fs.existsSync(mcpPkg)) throw new Error("mcp-server/package.json not found");
    const pkg = JSON.parse(fs.readFileSync(mcpPkg, "utf8")) as { name?: string; dependencies?: Record<string, string> };
    if (!pkg.dependencies?.["@modelcontextprotocol/sdk"]) {
      throw new Error("@modelcontextprotocol/sdk not in mcp-server deps");
    }
    return `MCP server package found — ${pkg.name} with @modelcontextprotocol/sdk ✓`;
  }));

  // 10. Source-status simulator (DATA_UNOBTAINABLE cannot approve)
  checks.push(await runCheck("sim_data_unobtainable", async () => {
    const r = await fetch(`${API_BASE}/dev/source-status-sim`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_status: "DATA_UNOBTAINABLE",
        sport: "WNBA",
        prop_type: "points",
        side: "LESS",
        line: 20.5,
      }),
      signal: AbortSignal.timeout(20000),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json() as {
      safety_check?: { safety_gate_held?: boolean };
      can_reach_money_qualified?: boolean;
      can_reach_final_approved?: boolean;
      terminal_bucket?: string;
    };
    if (d.can_reach_money_qualified !== false) throw new Error("Simulator says DATA_UNOBTAINABLE CAN reach money");
    if (!d.safety_check?.safety_gate_held) throw new Error("Safety gate did NOT hold for DATA_UNOBTAINABLE");
    return `DATA_UNOBTAINABLE → terminal_bucket=${d.terminal_bucket}, safety_gate_held=true ✓`;
  }));

  // 11. SOURCE_CONFLICT cannot approve
  checks.push(await runCheck("sim_source_conflict", async () => {
    const r = await fetch(`${API_BASE}/dev/source-status-sim`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_status: "SOURCE_CONFLICT",
        sport: "MLB",
        prop_type: "pitcher strikeouts",
        side: "MORE",
        line: 6.5,
      }),
      signal: AbortSignal.timeout(20000),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json() as {
      safety_check?: { safety_gate_held?: boolean };
      can_reach_final_approved?: boolean;
      terminal_bucket?: string;
    };
    if (d.can_reach_final_approved !== false) throw new Error("Simulator says SOURCE_CONFLICT CAN reach final approval");
    if (!d.safety_check?.safety_gate_held) throw new Error("Safety gate did NOT hold for SOURCE_CONFLICT");
    return `SOURCE_CONFLICT → terminal_bucket=${d.terminal_bucket}, safety_gate_held=true ✓`;
  }));

  // Compile result
  const critical = checks.filter(c => c.status === "FAIL");
  const warnings = checks.filter(c => c.status === "WARN");
  const passed   = checks.filter(c => c.status === "PASS");

  const status = critical.length > 0 ? "FAIL" : warnings.length > 0 ? "WARN" : "PASS";

  return res.json({
    status,
    timestamp,
    checks_total:     checks.length,
    checks_pass:      passed.length,
    checks_warn:      warnings.length,
    checks_fail:      critical.length,
    checks,
    critical_failures: critical.map(c => c.name),
    warnings:          warnings.map(c => ({ name: c.name, details: c.details })),
    execution_rule:    "READ_ONLY_NO_EXECUTION",
    live_execution:    false,
  });
});

export default router;
