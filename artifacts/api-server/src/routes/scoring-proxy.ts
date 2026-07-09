import { Router, type Request, type Response } from "express";

const router = Router();

const FLASK_BASE = process.env.SCORING_API_URL ?? "http://localhost:25643";
const API_KEY    = process.env.SCORING_API_KEY  ?? "";

function makeForwarder(prefix: string) {
  return async (req: Request, res: Response) => {
    const qs     = req.url.includes("?") ? req.url.slice(req.url.indexOf("?")) : "";
    const target = `${FLASK_BASE}/${prefix}${req.path}${qs}`;

    try {
      const opts: RequestInit = {
        method:  req.method,
        headers: {
          "Content-Type": "application/json",
          "Accept":       "application/json",
          "X-API-Key":    API_KEY,
        },
      };
      if (req.body != null && req.method !== "GET" && req.method !== "HEAD") {
        opts.body = JSON.stringify(req.body);
      }
      const r    = await fetch(target, opts);
      const body = await r.json() as unknown;
      return res.status(r.status).json(body);
    } catch {
      return res.status(502).json({ ok: false, error: "Scoring API unreachable" });
    }
  };
}

const GPT_BASE = {
  execution_rule: "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
  can_execute:    false,
} as const;

// ── GPT Action hard-contract routes ───────────────────────────────────────────
// These must always return HTTP 200 application/json, never HTML or 4xx/5xx.
// They use an 8-second AbortSignal timeout so OpenAI never gets a TCP hang.

// GET /wow/kalshi/health — PUBLIC, no API key required
router.get("/wow/kalshi/health", async (_req: Request, res: Response) => {
  try {
    const r = await fetch(`${FLASK_BASE}/wow/kalshi/health`, {
      headers: { Accept: "application/json" },
      signal:  AbortSignal.timeout(8_000),
    });
    let data: Record<string, unknown> = {};
    try { data = await r.json() as Record<string, unknown>; } catch { /* non-JSON fallback */ }
    return res.status(200).json({
      ok:             Boolean(data.ok ?? false),
      signal:         String(data.signal ?? "FAILED"),
      source_status:  r.ok ? "ROUTE_REACHABLE" : "UPSTREAM_ERROR",
      flask_reachable: true,
      kalshi_reachable: data.kalshi_reachable ?? null,
      kalshi_open_total:     data.kalshi_open_total     ?? null,
      kalshi_mve_null_count: data.kalshi_mve_null_count ?? null,
      deploy_version:  data.deploy_version ?? null,
      ...GPT_BASE,
    });
  } catch (err) {
    return res.status(200).json({
      ok:             false,
      signal:         "FAILED",
      source_status:  "UPSTREAM_FAILED",
      flask_reachable: false,
      kalshi_reachable: null,
      message:        String((err as Error)?.message ?? err),
      ...GPT_BASE,
    });
  }
});

// GET /wow/kalshi/debug-raw — forwarded with API key, 8s timeout
router.get("/wow/kalshi/debug-raw", async (req: Request, res: Response) => {
  const qs  = req.url.includes("?") ? req.url.slice(req.url.indexOf("?")) : "";
  try {
    const r = await fetch(`${FLASK_BASE}/wow/kalshi/debug-raw${qs}`, {
      headers: { Accept: "application/json", "X-API-Key": API_KEY },
      signal:  AbortSignal.timeout(8_000),
    });
    let data: unknown = {};
    try { data = await r.json(); } catch { /* non-JSON fallback */ }
    return res.status(200).json(data);
  } catch (err) {
    return res.status(200).json({
      ok:           false,
      signal:       "FAILED",
      source_status: "UPSTREAM_FAILED",
      message:      String((err as Error)?.message ?? err),
      ...GPT_BASE,
    });
  }
});

// ── Explicit routes for Kalshi weather lane — take precedence over wildcard.
// GET /wow/kalshi/weather/stations — no auth, health-check use after deploy
router.get("/wow/kalshi/weather/stations", async (_req: Request, res: Response) => {
  const target = `${FLASK_BASE}/wow/kalshi/weather/stations`;
  try {
    const r = await fetch(target, { headers: { "Accept": "application/json" } });
    const body = await r.json() as unknown;
    return res.status(r.status).json(body);
  } catch (err) {
    return res.status(502).json({ ok: false, error: "Scoring API unreachable", detail: String(err) });
  }
});

// GET /wow/kalshi/weather/source-health — no auth, connector health check
router.get("/wow/kalshi/weather/source-health", async (_req: Request, res: Response) => {
  const target = `${FLASK_BASE}/wow/kalshi/weather/source-health`;
  try {
    const r = await fetch(target, { headers: { "Accept": "application/json" } });
    const body = await r.json() as unknown;
    return res.status(r.status).json(body);
  } catch (err) {
    return res.status(502).json({ ok: false, error: "Scoring API unreachable", detail: String(err) });
  }
});

// GET /wow/kalshi/weather/calibration — NCEI CDO historical sigma_f estimate
router.get("/wow/kalshi/weather/calibration", async (req: Request, res: Response) => {
  const target = `${FLASK_BASE}/wow/kalshi/weather/calibration`;
  const qs     = new URLSearchParams(req.query as Record<string, string>).toString();
  try {
    const r = await fetch(qs ? `${target}?${qs}` : target, {
      headers: { "Accept": "application/json" },
    });
    const body = await r.json() as unknown;
    return res.status(r.status).json(body);
  } catch (err) {
    return res.status(502).json({ ok: false, error: "Scoring API unreachable", detail: String(err) });
  }
});

// GET /wow/kalshi/weather/scout/log — WEATHER_SCOUT calibration ledger
router.get("/wow/kalshi/weather/scout/log", async (req: Request, res: Response) => {
  const target = `${FLASK_BASE}/wow/kalshi/weather/scout/log`;
  const qs     = new URLSearchParams(req.query as Record<string, string>).toString();
  try {
    const r = await fetch(qs ? `${target}?${qs}` : target, {
      headers: { "Accept": "application/json" },
    });
    const body = await r.json() as unknown;
    return res.status(r.status).json(body);
  } catch (err) {
    return res.status(502).json({ ok: false, error: "Scoring API unreachable", detail: String(err) });
  }
});

// POST /wow/kalshi/weather/scout/settle — settle a scout row with observed high
router.post("/wow/kalshi/weather/scout/settle", async (req: Request, res: Response) => {
  const target = `${FLASK_BASE}/wow/kalshi/weather/scout/settle`;
  try {
    const r = await fetch(target, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept":       "application/json",
        "X-API-Key":    API_KEY,
      },
      body: JSON.stringify(req.body),
    });
    const body = await r.json() as unknown;
    return res.status(r.status).json(body);
  } catch (err) {
    return res.status(502).json({ ok: false, error: "Scoring API unreachable", detail: String(err) });
  }
});

// POST /wow/kalshi/weather/evaluate — scored bracket evaluation
router.post("/wow/kalshi/weather/evaluate", async (req: Request, res: Response) => {
  const target = `${FLASK_BASE}/wow/kalshi/weather/evaluate`;
  try {
    const r = await fetch(target, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept":       "application/json",
        "X-API-Key":    API_KEY,
      },
      body: JSON.stringify(req.body),
    });
    const body = await r.json() as unknown;
    return res.status(r.status).json(body);
  } catch (err) {
    return res.status(502).json({
      ok:             false,
      terminal_label: "INPUT_FAILURE",
      error:          "Scoring API unreachable",
      detail:         String(err),
    });
  }
});

// Explicit route for the Kalshi scan endpoint — takes precedence over the
// wildcard below so OPTIONS/preflight and POST are always handled correctly.
router.post("/wow/kalshi/scan", async (req: Request, res: Response) => {
  const target = `${FLASK_BASE}/wow/kalshi/scan`;
  try {
    const r = await fetch(target, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept":       "application/json",
        "X-API-Key":    API_KEY,
      },
      body: JSON.stringify(req.body),
    });
    const body = await r.json() as unknown;
    return res.status(r.status).json(body);
  } catch (err) {
    return res.status(502).json({
      ok:             false,
      terminal_label: "INPUT_FAILURE",
      error:          "Scoring API unreachable",
      detail:         String(err),
      execution_rule: "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
    });
  }
});

router.use("/wow",           makeForwarder("wow"));
router.use("/kalshi",        makeForwarder("kalshi"));
router.use("/lock-api",      makeForwarder("lock-api"));
router.use("/gate-engine",   makeForwarder("gate-engine"));
router.use("/analyze-board", makeForwarder("analyze-board"));
router.use("/lines",         makeForwarder("lines"));

// Generic catch-all: forward unmatched routes directly to Flask root.
// This covers /request-log, /leaderboard, /stats, /final-lock, /gpt-score, etc.
// Routes already handled above (api-server-native /props, /dev, /admin, /postmortem,
// /health) will never reach here because they were registered in routes/index.ts first.
router.use(async (req: Request, res: Response) => {
  const qs     = req.url.includes("?") ? req.url.slice(req.url.indexOf("?")) : "";
  const target = `${FLASK_BASE}${req.path}${qs}`;
  try {
    const opts: RequestInit = {
      method:  req.method,
      headers: {
        "Content-Type": "application/json",
        "Accept":       "application/json",
        "X-API-Key":    API_KEY,
      },
    };
    if (req.body != null && req.method !== "GET" && req.method !== "HEAD") {
      opts.body = JSON.stringify(req.body);
    }
    const r    = await fetch(target, opts);
    const body = await r.json() as unknown;
    return res.status(r.status).json(body);
  } catch {
    return res.status(502).json({ ok: false, error: "Scoring API unreachable" });
  }
});

export default router;
