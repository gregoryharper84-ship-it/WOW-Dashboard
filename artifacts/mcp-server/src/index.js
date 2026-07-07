/**
 * WOW Data Hub — MCP Tool Server
 *
 * Exposes the WOW scoring engine, Kalshi scanner, props normalizer,
 * and audit tools as MCP-callable tools for Claude / ChatGPT agents.
 *
 * ALL tools are read-only. No order placement, no trade execution.
 * can_execute is never set to true here.
 *
 * Transport: stdio (plug into Claude Desktop / GPT-4o tool calling via
 * the MCP protocol). Run with:
 *   node artifacts/mcp-server/src/index.js
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

const FLASK_BASE = process.env.SCORING_API_URL  ?? "http://localhost:25643";
const API_KEY    = process.env.SCORING_API_KEY   ?? "";
const API_BASE   = process.env.API_SERVER_URL    ?? "http://localhost:8080/api";

// ── HTTP helpers ──────────────────────────────────────────────────────────────

async function flaskGet(path, qs = {}) {
  const params = new URLSearchParams(Object.fromEntries(
    Object.entries(qs).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)])
  ));
  const url = `${FLASK_BASE}${path}${params.size ? "?" + params : ""}`;
  const r = await fetch(url, {
    headers: { "Accept": "application/json", "X-API-Key": API_KEY },
    signal: AbortSignal.timeout(20000),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status} from ${path}`);
  return r.json();
}

async function flaskPost(path, body) {
  const r = await fetch(`${FLASK_BASE}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Accept": "application/json",
      "X-API-Key": API_KEY,
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(30000),
  });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(`HTTP ${r.status} from ${path}: ${text.slice(0, 200)}`);
  }
  return r.json();
}

async function apiGet(path, qs = {}) {
  const params = new URLSearchParams(Object.fromEntries(
    Object.entries(qs).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)])
  ));
  const url = `${API_BASE}${path}${params.size ? "?" + params : ""}`;
  const r = await fetch(url, {
    headers: { "Accept": "application/json" },
    signal: AbortSignal.timeout(15000),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status} from ${path}`);
  return r.json();
}

async function apiPost(path, body) {
  const r = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Accept": "application/json" },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(30000),
  });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(`HTTP ${r.status} from ${path}: ${text.slice(0, 200)}`);
  }
  return r.json();
}

function ok(data) {
  return { content: [{ type: "text", text: JSON.stringify(data, null, 2) }] };
}

function err(e) {
  return {
    content: [{ type: "text", text: `ERROR: ${e.message ?? String(e)}` }],
    isError: true,
  };
}

// ── Tool definitions ──────────────────────────────────────────────────────────

const TOOLS = [
  {
    name: "scan_board",
    description:
      "Trigger a full WOW daily board scan for one or more sports. Returns categorized results: approved / hold / watch / reject buckets with terminal labels and blockers. Read-only.",
    inputSchema: {
      type: "object",
      properties: {
        sports: {
          type: "array",
          items: { type: "string" },
          description: "List of sports to scan, e.g. ['NBA','MLB','WNBA']",
        },
        date: { type: "string", description: "YYYY-MM-DD game date (defaults to today)" },
        slip_type: { type: "string", description: "Slip type context, e.g. '2-pick Power'" },
      },
      required: ["sports"],
    },
  },
  {
    name: "normalize_props",
    description:
      "Fetch and normalize player props from configured providers (OpticOdds, PropLine, SportsGameOdds, The Odds API) into the WOW schema. Returns DATA_UNOBTAINABLE if all providers fail.",
    inputSchema: {
      type: "object",
      properties: {
        sport:     { type: "string", description: "Sport key, e.g. 'NBA', 'MLB', 'WNBA'" },
        date:      { type: "string", description: "YYYY-MM-DD" },
        providers: {
          type: "array",
          items: { type: "string" },
          description: "Subset of providers: opticodds, propline, sportsgameodds, odds_api",
        },
        props: {
          type: "array",
          description: "Caller-supplied raw props to normalize (bypasses provider fetch)",
          items: { type: "object" },
        },
      },
      required: ["sport"],
    },
  },
  {
    name: "fetch_sportsbook_odds",
    description:
      "Fetch sportsbook odds for a sport. Returns implied probability, no-vig probability, and spread. Read-only.",
    inputSchema: {
      type: "object",
      properties: {
        sport:  { type: "string", description: "e.g. 'NBA', 'MLB'" },
        market: { type: "string", description: "e.g. 'h2h', 'spreads', 'totals', 'player_points'" },
        date:   { type: "string", description: "YYYY-MM-DD" },
      },
      required: ["sport"],
    },
  },
  {
    name: "fetch_kalshi_markets",
    description:
      "Scan Kalshi prediction markets. Supports sports, weather, macro, politics, entertainment categories. Returns ticker, yes/no prices, liquidity, adjusted edge. Read-only — no orders placed.",
    inputSchema: {
      type: "object",
      properties: {
        category: {
          type: "string",
          enum: ["sports", "weather", "macro", "politics", "entertainment", "news"],
          description: "Market category",
        },
        sport:  { type: "string", description: "For sports category: NBA, MLB, WNBA, etc." },
        limit:  { type: "number", description: "Max markets to return (default 20)" },
        min_edge: { type: "number", description: "Minimum edge % filter (0–100)" },
      },
    },
  },
  {
    name: "fetch_player_status",
    description:
      "Fetch current player injury/status, probable starters, and recent game log from ESPN or configured data feeds. Returns DATA_UNOBTAINABLE if source is unavailable.",
    inputSchema: {
      type: "object",
      properties: {
        player:  { type: "string", description: "Player full name" },
        sport:   { type: "string", description: "e.g. 'NBA', 'MLB', 'WNBA'" },
        team:    { type: "string", description: "Team abbreviation (optional)" },
      },
      required: ["player", "sport"],
    },
  },
  {
    name: "fetch_l10_ledger",
    description:
      "Fetch the L5/L10 exact-line ledger for a player prop. Returns median, average, hit rates, and per-game log where available. Returns DATA_UNOBTAINABLE if reconstruction fails.",
    inputSchema: {
      type: "object",
      properties: {
        player:    { type: "string" },
        sport:     { type: "string" },
        prop_type: { type: "string", description: "e.g. 'Points', 'Pitcher Ks', 'Rebounds'" },
        side:      { type: "string", enum: ["MORE", "LESS"] },
        line:      { type: "number" },
        version:   { type: "string", description: "API version (default v2)" },
      },
      required: ["player", "sport", "prop_type"],
    },
  },
  {
    name: "compare_market_edge",
    description:
      "Compare a board line against sportsbook / Kalshi market to compute edge, no-vig probability, and drift grade. Never approves from market drift alone.",
    inputSchema: {
      type: "object",
      properties: {
        player:         { type: "string" },
        sport:          { type: "string" },
        prop_type:      { type: "string" },
        side:           { type: "string", enum: ["MORE", "LESS"] },
        board_line:     { type: "number", description: "PrizePicks / board line" },
        sb_line:        { type: "number", description: "Sportsbook comparison line" },
        sb_no_vig_prob: { type: "number", description: "Sportsbook no-vig probability 0–1" },
        model_prob:     { type: "number", description: "Model/projection probability 0–1" },
      },
      required: ["player", "sport", "prop_type", "side", "board_line"],
    },
  },
  {
    name: "score_wow_prop",
    description:
      "Run a single prop through the full WOW gate engine (Source → Calibration → Board Lock → Cross-Market → Role/Status → L5/L10 → Market/Projection → Probability → Payout → Failure Path → Slip Exposure → Terminal Bucket). Returns one of the 12 valid terminal labels. Never fabricates data.",
    inputSchema: {
      type: "object",
      properties: {
        player:              { type: "string" },
        team:                { type: "string" },
        opponent:            { type: "string" },
        sport:               { type: "string" },
        market:              { type: "string", description: "Prop type, e.g. 'Points', 'Pitcher Ks'" },
        side:                { type: "string", enum: ["MORE", "LESS"] },
        pp_line:             { type: "number" },
        slip_type:           { type: "string" },
        pick_count:          { type: "number" },
        pp_payout:           { type: "number" },
        injury_status:       { type: "string" },
        teammate_status:     { type: "string" },
        sb_comp_line:        { type: "number" },
        sb_no_vig_prob:      { type: "number" },
        model_probability:   { type: "number" },
        shrinkage_probability: { type: "number" },
        environment:         { type: "string" },
        notes:               { type: "string" },
      },
      required: ["player", "sport", "market", "side", "pp_line"],
    },
  },
  {
    name: "run_final_lock",
    description:
      "Run the WOW v14.9+ Final Lock gate on a prop. Checks live status, current line verification, L10/reconstruction, market+projection support, positive EV, and clean exposure. FINAL_APPROVED requires all gates to pass. Returns full gate breakdown and terminal label.",
    inputSchema: {
      type: "object",
      properties: {
        player:              { type: "string" },
        team:                { type: "string" },
        opponent:            { type: "string" },
        sport:               { type: "string" },
        market:              { type: "string" },
        side:                { type: "string", enum: ["MORE", "LESS"] },
        pp_line:             { type: "number" },
        slip_type:           { type: "string" },
        pick_count:          { type: "number" },
        pp_payout:           { type: "number" },
        injury_status:       { type: "string" },
        teammate_status:     { type: "string" },
        correlation_flag:    { type: "boolean" },
        sb_comp_line:        { type: "number" },
        sb_no_vig_prob:      { type: "number" },
        proj_source1:        { type: "string" },
        proj_source2:        { type: "string" },
        model_probability:   { type: "number" },
        shrinkage_probability: { type: "number" },
        environment:         { type: "string" },
        notes:               { type: "string" },
      },
      required: ["player", "sport", "market", "side", "pp_line"],
    },
  },
  {
    name: "build_slip_candidates",
    description:
      "Run the gate engine pipeline across a batch of props and return slip-building candidates grouped by terminal label. Flags correlation, duplicate exposure, and JS-style conversion sub-tags.",
    inputSchema: {
      type: "object",
      properties: {
        rows: {
          type: "array",
          description: "Array of prop rows to evaluate",
          items: { type: "object" },
        },
        target_date: { type: "string", description: "YYYY-MM-DD" },
        slip_type:   { type: "string" },
      },
      required: ["rows"],
    },
  },
  {
    name: "export_no_play_report",
    description:
      "Export all NO_PLAY, REJECT_*, and DATA_UNOBTAINABLE decisions from the request log as a structured audit report. Includes blocker breakdown per prop.",
    inputSchema: {
      type: "object",
      properties: {
        date:  { type: "string", description: "YYYY-MM-DD (defaults to today)" },
        sport: { type: "string", description: "Filter by sport (optional)" },
        limit: { type: "number", description: "Max rows (default 100)" },
      },
    },
  },
  {
    name: "get_request_log",
    description:
      "Retrieve the full request log showing past terminal bucket decisions, source status, blockers, and edge results.",
    inputSchema: {
      type: "object",
      properties: {
        limit:          { type: "number", description: "Max entries (default 50)" },
        terminal_label: { type: "string", description: "Filter by label" },
        sport:          { type: "string" },
      },
    },
  },
  {
    name: "get_leaderboard",
    description:
      "Get the leaderboard showing player/market approval rates, average edge, and terminal bucket distribution across all scoring runs.",
    inputSchema: {
      type: "object",
      properties: {
        sport:    { type: "string", description: "Filter by sport (optional)" },
        min_runs: { type: "number", description: "Minimum scoring runs to include a row" },
      },
    },
  },
];

// ── Tool handler dispatch ─────────────────────────────────────────────────────

async function handleTool(name, args) {
  switch (name) {

    case "scan_board": {
      const body = {
        sports:    args.sports ?? ["NBA"],
        date:      args.date,
        slip_type: args.slip_type ?? "2-pick Power",
      };
      return ok(await flaskPost("/wow-daily-scan", body));
    }

    case "normalize_props": {
      const body = {
        sport:     args.sport,
        date:      args.date,
        providers: args.providers,
        props:     args.props,
      };
      return ok(await apiPost("/props/normalize", body));
    }

    case "fetch_sportsbook_odds": {
      const sport   = (args.sport ?? "NBA").toLowerCase();
      const market  = args.market ?? "player_points";
      const SPORT_KEYS = {
        nba: "basketball_nba", wnba: "basketball_wnba",
        mlb: "baseball_mlb",   nfl: "americanfootball_nfl",
        nhl: "icehockey_nhl",
      };
      const sportKey = SPORT_KEYS[sport] ?? sport;
      const ODDS_KEY = process.env.ODDS_API_KEY ?? "";
      if (!ODDS_KEY) {
        return ok({ ok: false, source_status: "DATA_UNOBTAINABLE", error: "ODDS_API_KEY not configured" });
      }
      const params = new URLSearchParams({ apiKey: ODDS_KEY, regions: "us", markets: market });
      const r = await fetch(`https://api.the-odds-api.com/v4/sports/${sportKey}/odds?${params}`, {
        signal: AbortSignal.timeout(10000),
      });
      if (!r.ok) return ok({ ok: false, source_status: "DATA_UNOBTAINABLE", error: `HTTP ${r.status}` });
      const events = await r.json();
      // Compute no-vig probabilities
      const enriched = events.map(evt => {
        const books = (evt.bookmakers ?? []).map(bm => {
          const mkts = (bm.markets ?? []).map(mkt => {
            const odds = mkt.outcomes?.map(o => ({ name: o.name, price: o.price, point: o.point })) ?? [];
            // Simple no-vig: for 2-way markets
            if (odds.length === 2) {
              const p1 = 1 / odds[0].price;
              const p2 = 1 / odds[1].price;
              const juice = p1 + p2;
              return { ...mkt, outcomes: odds, no_vig: { [odds[0].name]: +(p1 / juice).toFixed(4), [odds[1].name]: +(p2 / juice).toFixed(4) } };
            }
            return { ...mkt, outcomes: odds };
          });
          return { bookmaker: bm.key, markets: mkts };
        });
        return { ...evt, bookmakers: books };
      });
      return ok({ ok: true, events: enriched, count: enriched.length });
    }

    case "fetch_kalshi_markets": {
      const body = {
        category:  args.category ?? "sports",
        sport:     args.sport,
        limit:     args.limit ?? 20,
        min_edge:  args.min_edge,
      };
      try {
        return ok(await flaskPost("/wow/kalshi/scan", body));
      } catch (e) {
        return ok({ ok: false, source_status: "DATA_UNOBTAINABLE", error: e.message });
      }
    }

    case "fetch_player_status": {
      try {
        const data = await flaskGet(`/api-sports/${(args.sport ?? "basketball").toLowerCase()}/players`, {
          name: args.player,
          team: args.team,
        });
        return ok(data);
      } catch (e) {
        return ok({ ok: false, source_status: "DATA_UNOBTAINABLE", player: args.player, error: e.message });
      }
    }

    case "fetch_l10_ledger": {
      const version = args.version ?? "v2";
      try {
        const data = await flaskPost(`/wow/l10/${version}`, {
          player:    args.player,
          sport:     args.sport,
          prop_type: args.prop_type,
          side:      args.side ?? "MORE",
          line:      args.line,
        });
        return ok(data);
      } catch (e) {
        return ok({ ok: false, source_status: "DATA_UNOBTAINABLE", player: args.player, error: e.message });
      }
    }

    case "compare_market_edge": {
      const boardLine    = args.board_line;
      const sbLine       = args.sb_line;
      const sbNoVigProb  = args.sb_no_vig_prob;
      const modelProb    = args.model_prob;
      const side         = args.side ?? "MORE";

      // Cushion calculation
      const cushion = sbLine != null
        ? side === "MORE" ? sbLine - boardLine : boardLine - sbLine
        : null;

      // Edge vs sportsbook no-vig
      const sbEdge = sbNoVigProb != null && modelProb != null
        ? +(modelProb - sbNoVigProb).toFixed(4)
        : null;

      // Drift grade
      let drift_grade = "UNKNOWN";
      if (cushion != null) {
        if (cushion >= 1.5)     drift_grade = "STRONG_CUSHION";
        else if (cushion >= 0.5) drift_grade = "MODERATE_CUSHION";
        else if (cushion >= 0)   drift_grade = "THIN_CUSHION";
        else                     drift_grade = "NEGATIVE_CUSHION";
      }

      return ok({
        player:      args.player,
        sport:       args.sport,
        prop_type:   args.prop_type,
        side,
        board_line:  boardLine,
        sb_line:     sbLine,
        cushion,
        drift_grade,
        sb_no_vig_prob: sbNoVigProb,
        model_prob:  modelProb,
        edge_vs_sb:  sbEdge,
        warning:     "Market drift alone is insufficient for approval. Run score_wow_prop for full gate evaluation.",
      });
    }

    case "score_wow_prop": {
      return ok(await flaskPost("/gpt-score", args));
    }

    case "run_final_lock": {
      return ok(await flaskPost("/final-lock", args));
    }

    case "build_slip_candidates": {
      return ok(await flaskPost("/gate-engine/run", {
        raw_rows:    args.rows,
        target_date: args.target_date,
      }));
    }

    case "export_no_play_report": {
      const logs = await flaskGet("/request-log", {
        limit: args.limit ?? 100,
        date:  args.date,
        sport: args.sport,
      });
      // Filter to rejection/no-play labels
      const REJECT_LABELS = new Set([
        "NO_PLAY", "REJECT_NO_EDGE", "REJECT_BAD_STRUCTURE",
        "REJECT_DATA_QUALITY", "SLATE_PURGE", "DATA_UNOBTAINABLE",
        "DUPLICATE_EXPOSURE_BLOCK", "SOURCE_CONFLICT",
      ]);
      const entries = (logs.requests ?? logs.entries ?? logs.data ?? [])
        .filter(e => REJECT_LABELS.has(e.terminal_label));

      const blockerFreq = {};
      for (const e of entries) {
        const blist = Array.isArray(e.blockers) ? e.blockers
          : typeof e.blockers === "string" ? JSON.parse(e.blockers || "[]")
          : [];
        for (const b of blist) blockerFreq[b] = (blockerFreq[b] ?? 0) + 1;
      }

      const topBlockers = Object.entries(blockerFreq)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 10)
        .map(([blocker, count]) => ({ blocker, count }));

      return ok({
        report_date:     args.date ?? new Date().toISOString().slice(0, 10),
        total_no_play:   entries.length,
        by_label:        Object.fromEntries(
          [...REJECT_LABELS].map(l => [l, entries.filter(e => e.terminal_label === l).length])
        ),
        top_blockers:    topBlockers,
        entries:         entries.slice(0, 50),
        execution_rule:  "READ_ONLY_NO_EXECUTION",
      });
    }

    case "get_request_log": {
      return ok(await flaskGet("/request-log", {
        limit:          args.limit ?? 50,
        terminal_label: args.terminal_label,
        sport:          args.sport,
      }));
    }

    case "get_leaderboard": {
      return ok(await flaskGet("/leaderboard", {
        sport:    args.sport,
        min_runs: args.min_runs,
      }));
    }

    default:
      throw new Error(`Unknown tool: ${name}`);
  }
}

// ── MCP server setup ──────────────────────────────────────────────────────────

const server = new Server(
  { name: "wow-data-hub", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const { name, arguments: args } = req.params;
  try {
    return await handleTool(name, args ?? {});
  } catch (e) {
    return err(e);
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);
// MCP server running — communicates via stdio
