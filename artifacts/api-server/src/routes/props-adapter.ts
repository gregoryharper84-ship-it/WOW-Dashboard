/**
 * props-adapter.ts — WOW Data Hub: Normalized Prop Intake Lane
 *
 * Accepts raw prop data from multiple providers (OpticOdds, PropLine,
 * SportsGameOdds, The Odds API) and normalizes into the WOW prop schema.
 * Falls back to mock data when no real API keys are configured.
 *
 * Principles:
 *  - Read-only. No order placement, no fabrication.
 *  - Returns DATA_UNOBTAINABLE when a source fails or data is missing.
 *  - All API keys from environment only.
 *  - Normalized output fed into /api/wow/score for gate evaluation.
 */
import { Router, type Request, type Response } from "express";

const router = Router();

// ── WOW Prop Schema ───────────────────────────────────────────────────────────
export interface WowProp {
  player:          string;
  team:            string;
  opponent:        string;
  sport:           string;
  game_date:       string;
  prop_type:       string;
  side:            "MORE" | "LESS";
  line:            number;
  platform:        string;
  payout_context:  string | null;
  source_status:   "LIVE" | "STALE" | "UNAVAILABLE" | "DATA_UNOBTAINABLE";
  source_grade:    "A" | "B" | "C" | "UNVERIFIED" | "UNKNOWN";
  source:          string;
  timestamp:       string;
}

interface NormalizeResult {
  ok:       boolean;
  props:    WowProp[];
  errors:   string[];
  provider: string;
  raw_count: number;
}

// ── Provider config ───────────────────────────────────────────────────────────
const PROVIDER_KEYS = {
  opticodds:        process.env["OPTICODDS_API_KEY"]        ?? "",
  propline:         process.env["PROPLINE_API_KEY"]         ?? "",
  sportsgameodds:   process.env["SPORTSGAMEODDS_API_KEY"]   ?? "",
  odds_api:         process.env["ODDS_API_KEY"]             ?? "",
} as const;

const OPTICODDS_BASE     = "https://api.opticodds.com/api/v3";
const PROPLINE_BASE      = "https://api.prop-line.io/v1";
const SPORTSGAMEODDS_BASE= "https://api.sportsgameodds.com/v2";
const ODDS_API_BASE      = "https://api.the-odds-api.com/v4";

// ── Sport normalisation map ───────────────────────────────────────────────────
const SPORT_MAP: Record<string, string> = {
  nba: "NBA", basketball_nba: "NBA", basketball: "NBA",
  wnba: "WNBA", basketball_wnba: "WNBA",
  mlb: "MLB", baseball_mlb: "MLB", baseball: "MLB",
  nfl: "NFL", americanfootball_nfl: "NFL",
  nhl: "NHL", icehockey_nhl: "NHL",
  soccer: "SOCCER", mls: "SOCCER",
  tennis: "TENNIS",
  golf: "GOLF",
};

function normSport(raw: string): string {
  return SPORT_MAP[raw?.toLowerCase()] ?? raw?.toUpperCase() ?? "UNKNOWN";
}

function normSide(raw: string): "MORE" | "LESS" {
  const u = (raw ?? "").toUpperCase();
  if (u === "OVER" || u === "MORE" || u === "O") return "MORE";
  if (u === "UNDER" || u === "LESS" || u === "U") return "LESS";
  return "MORE";
}

function nowIso(): string {
  return new Date().toISOString();
}

// ── Mock data (used when no API keys are configured) ──────────────────────────
function mockProps(sport: string): WowProp[] {
  const base = {
    team: "MOCK TEAM", opponent: "OPP TEAM",
    sport: normSport(sport || "NBA"),
    game_date: new Date().toISOString().slice(0, 10),
    payout_context: "2-pick Power",
    source_status: "UNAVAILABLE" as const,
    source_grade: "UNKNOWN" as const,
    source: "mock",
    timestamp: nowIso(),
  };
  return [
    { ...base, player: "Mock Player A", prop_type: "Points",   side: "MORE", line: 22.5, platform: "mock" },
    { ...base, player: "Mock Player B", prop_type: "Rebounds",  side: "MORE", line: 8.5,  platform: "mock" },
    { ...base, player: "Mock Player C", prop_type: "Assists",   side: "LESS", line: 6.5,  platform: "mock" },
    { ...base, player: "Mock Pitcher",  prop_type: "Pitcher Ks",side: "MORE", line: 5.5,  platform: "mock", sport: "MLB" },
  ];
}

// ── OpticOdds adapter ─────────────────────────────────────────────────────────
async function fetchOpticOdds(sport: string, date?: string): Promise<NormalizeResult> {
  const key = PROVIDER_KEYS.opticodds;
  if (!key) {
    return { ok: false, props: [], errors: ["OPTICODDS_API_KEY not configured"], provider: "opticodds", raw_count: 0 };
  }

  try {
    const params = new URLSearchParams({ sport: sport.toLowerCase() });
    if (date) params.set("date", date);
    const url = `${OPTICODDS_BASE}/fixtures/player-props?${params}`;
    const r = await fetch(url, {
      headers: { "X-Api-Key": key, "Accept": "application/json" },
      signal: AbortSignal.timeout(8000),
    });
    if (!r.ok) {
      return { ok: false, props: [], errors: [`OpticOdds HTTP ${r.status}`], provider: "opticodds", raw_count: 0 };
    }
    const data = await r.json() as Record<string, unknown>;
    const fixtures = (data["data"] ?? []) as Record<string, unknown>[];
    const props: WowProp[] = [];

    for (const fix of fixtures) {
      const markets = (fix["markets"] ?? []) as Record<string, unknown>[];
      for (const mkt of markets) {
        const outcomes = (mkt["outcomes"] ?? []) as Record<string, unknown>[];
        for (const out of outcomes) {
          const line = parseFloat(String(out["handicap"] ?? out["line"] ?? ""));
          if (isNaN(line)) continue;
          props.push({
            player:         String(fix["player_name"] ?? ""),
            team:           String(fix["team_abbreviation"] ?? ""),
            opponent:       String(fix["opponent_abbreviation"] ?? ""),
            sport:          normSport(String(fix["sport"] ?? sport)),
            game_date:      String(fix["game_date"] ?? date ?? nowIso().slice(0, 10)),
            prop_type:      String(mkt["name"] ?? ""),
            side:           normSide(String(out["name"] ?? "")),
            line,
            platform:       "opticodds",
            payout_context: null,
            source_status:  "LIVE",
            source_grade:   "A",
            source:         "opticodds",
            timestamp:      nowIso(),
          });
        }
      }
    }
    return { ok: true, props, errors: [], provider: "opticodds", raw_count: fixtures.length };
  } catch (err) {
    return { ok: false, props: [], errors: [String(err)], provider: "opticodds", raw_count: 0 };
  }
}

// ── PropLine adapter ──────────────────────────────────────────────────────────
async function fetchPropLine(sport: string, date?: string): Promise<NormalizeResult> {
  const key = PROVIDER_KEYS.propline;
  if (!key) {
    return { ok: false, props: [], errors: ["PROPLINE_API_KEY not configured"], provider: "propline", raw_count: 0 };
  }

  try {
    const params = new URLSearchParams({ sport: sport.toUpperCase() });
    if (date) params.set("game_date", date);
    const url = `${PROPLINE_BASE}/player-props?${params}`;
    const r = await fetch(url, {
      headers: { "Authorization": `Bearer ${key}`, "Accept": "application/json" },
      signal: AbortSignal.timeout(8000),
    });
    if (!r.ok) {
      return { ok: false, props: [], errors: [`PropLine HTTP ${r.status}`], provider: "propline", raw_count: 0 };
    }
    const data = await r.json() as Record<string, unknown>;
    const rows = (data["props"] ?? data["data"] ?? []) as Record<string, unknown>[];
    const props: WowProp[] = rows.map(row => ({
      player:         String(row["player_name"] ?? row["player"] ?? ""),
      team:           String(row["team"] ?? ""),
      opponent:       String(row["opponent"] ?? ""),
      sport:          normSport(String(row["sport"] ?? sport)),
      game_date:      String(row["game_date"] ?? date ?? nowIso().slice(0, 10)),
      prop_type:      String(row["stat_type"] ?? row["market"] ?? ""),
      side:           normSide(String(row["position"] ?? row["side"] ?? "")),
      line:           parseFloat(String(row["line_score"] ?? row["line"] ?? "0")),
      platform:       String(row["book"] ?? "propline"),
      payout_context: null,
      source_status:  "LIVE" as const,
      source_grade:   "A" as const,
      source:         "propline",
      timestamp:      nowIso(),
    })).filter(p => !isNaN(p.line));
    return { ok: true, props, errors: [], provider: "propline", raw_count: rows.length };
  } catch (err) {
    return { ok: false, props: [], errors: [String(err)], provider: "propline", raw_count: 0 };
  }
}

// ── SportsGameOdds adapter ────────────────────────────────────────────────────
async function fetchSportsGameOdds(sport: string, date?: string): Promise<NormalizeResult> {
  const key = PROVIDER_KEYS.sportsgameodds;
  if (!key) {
    return { ok: false, props: [], errors: ["SPORTSGAMEODDS_API_KEY not configured"], provider: "sportsgameodds", raw_count: 0 };
  }

  try {
    const params = new URLSearchParams({ sport: sport.toLowerCase() });
    if (date) params.set("date", date);
    const url = `${SPORTSGAMEODDS_BASE}/player-props?${params}`;
    const r = await fetch(url, {
      headers: { "X-Api-Key": key, "Accept": "application/json" },
      signal: AbortSignal.timeout(8000),
    });
    if (!r.ok) {
      return { ok: false, props: [], errors: [`SportsGameOdds HTTP ${r.status}`], provider: "sportsgameodds", raw_count: 0 };
    }
    const data = await r.json() as Record<string, unknown>;
    const events = (data["events"] ?? data["data"] ?? []) as Record<string, unknown>[];
    const props: WowProp[] = [];

    for (const evt of events) {
      const playerProps = (evt["playerProps"] ?? evt["player_props"] ?? []) as Record<string, unknown>[];
      for (const pp of playerProps) {
        const line = parseFloat(String(pp["line"] ?? pp["handicap"] ?? ""));
        if (isNaN(line)) continue;
        props.push({
          player:         String(pp["playerName"] ?? pp["player_name"] ?? ""),
          team:           String(evt["homeTeam"] ?? evt["home_team"] ?? ""),
          opponent:       String(evt["awayTeam"] ?? evt["away_team"] ?? ""),
          sport:          normSport(String(evt["sport"] ?? sport)),
          game_date:      String(evt["gameDate"] ?? evt["game_date"] ?? date ?? nowIso().slice(0, 10)),
          prop_type:      String(pp["marketType"] ?? pp["market"] ?? ""),
          side:           normSide(String(pp["side"] ?? "")),
          line,
          platform:       "sportsgameodds",
          payout_context: null,
          source_status:  "LIVE",
          source_grade:   "B",
          source:         "sportsgameodds",
          timestamp:      nowIso(),
        });
      }
    }
    return { ok: true, props, errors: [], provider: "sportsgameodds", raw_count: events.length };
  } catch (err) {
    return { ok: false, props: [], errors: [String(err)], provider: "sportsgameodds", raw_count: 0 };
  }
}

// ── The Odds API player props adapter ────────────────────────────────────────
async function fetchOddsApiProps(sport: string, date?: string): Promise<NormalizeResult> {
  const key = PROVIDER_KEYS.odds_api;
  if (!key) {
    return { ok: false, props: [], errors: ["ODDS_API_KEY not configured"], provider: "odds_api", raw_count: 0 };
  }

  // Map sport to Odds API sport key
  const SPORT_KEYS: Record<string, string> = {
    NBA: "basketball_nba", WNBA: "basketball_wnba",
    MLB: "baseball_mlb", NFL: "americanfootball_nfl", NHL: "icehockey_nhl",
  };
  const sportKey = SPORT_KEYS[normSport(sport)] ?? sport.toLowerCase();

  // Odds API player prop markets by sport
  const MARKETS: Record<string, string> = {
    basketball_nba:   "player_points,player_rebounds,player_assists,player_threes",
    basketball_wnba:  "player_points,player_rebounds,player_assists",
    baseball_mlb:     "batter_hits,batter_home_runs,pitcher_strikeouts",
    americanfootball_nfl: "player_reception_yards,player_rush_yards,player_receptions",
    icehockey_nhl:    "player_shots_on_goal,player_points",
  };
  const markets = MARKETS[sportKey] ?? "player_points";

  try {
    const params = new URLSearchParams({
      apiKey: key,
      regions: "us",
      markets,
      bookmakers: "draftkings,fanduel,betmgm",
    });
    if (date) params.set("dateFormat", "iso");
    const url = `${ODDS_API_BASE}/sports/${sportKey}/events/odds?${params}`;
    const r = await fetch(url, {
      headers: { "Accept": "application/json" },
      signal: AbortSignal.timeout(10000),
    });
    if (!r.ok) {
      return { ok: false, props: [], errors: [`Odds API HTTP ${r.status}`], provider: "odds_api", raw_count: 0 };
    }
    const events = await r.json() as Record<string, unknown>[];
    const props: WowProp[] = [];

    for (const evt of events) {
      const bookmakers = (evt["bookmakers"] ?? []) as Record<string, unknown>[];
      for (const bm of bookmakers) {
        const markets_arr = (bm["markets"] ?? []) as Record<string, unknown>[];
        for (const mkt of markets_arr) {
          const outcomes = (mkt["outcomes"] ?? []) as Record<string, unknown>[];
          for (const out of outcomes) {
            const line = parseFloat(String(out["point"] ?? ""));
            if (isNaN(line)) continue;
            const desc = String(out["description"] ?? "");
            const name = String(out["name"] ?? "");
            props.push({
              player:         desc || name,
              team:           String(evt["home_team"] ?? ""),
              opponent:       String(evt["away_team"] ?? ""),
              sport:          normSport(String(evt["sport_key"] ?? sport)),
              game_date:      String((evt["commence_time"] as string)?.slice(0, 10) ?? nowIso().slice(0, 10)),
              prop_type:      String(mkt["key"] ?? "").replace(/_/g, " "),
              side:           normSide(name),
              line,
              platform:       String(bm["key"] ?? "odds_api"),
              payout_context: null,
              source_status:  "LIVE",
              source_grade:   "A",
              source:         "odds_api",
              timestamp:      nowIso(),
            });
          }
        }
      }
    }
    return { ok: true, props, errors: [], provider: "odds_api", raw_count: events.length };
  } catch (err) {
    return { ok: false, props: [], errors: [String(err)], provider: "odds_api", raw_count: 0 };
  }
}

// ── Dedup by player+prop_type+side+platform ───────────────────────────────────
function dedup(props: WowProp[]): WowProp[] {
  const seen = new Set<string>();
  return props.filter(p => {
    const key = `${p.player}|${p.prop_type}|${p.side}|${p.platform}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

// ── Routes ────────────────────────────────────────────────────────────────────

/**
 * POST /api/props/normalize
 * Body: { sport, date?, providers?: string[], props?: RawProp[] }
 * Fetches from all available providers and normalizes into WOW schema.
 * Returns DATA_UNOBTAINABLE when all providers fail.
 */
router.post("/normalize", async (req: Request, res: Response) => {
  const { sport = "NBA", date, providers, props: rawProps } = req.body as {
    sport?: string;
    date?: string;
    providers?: string[];
    props?: Record<string, unknown>[];
  };

  // Caller-supplied raw props (manual paste / board intake)
  if (rawProps && Array.isArray(rawProps) && rawProps.length > 0) {
    const normalized: WowProp[] = rawProps.map(r => ({
      player:         String(r["player"] ?? ""),
      team:           String(r["team"] ?? ""),
      opponent:       String(r["opponent"] ?? ""),
      sport:          normSport(String(r["sport"] ?? sport)),
      game_date:      String(r["game_date"] ?? date ?? nowIso().slice(0, 10)),
      prop_type:      String(r["prop_type"] ?? r["market"] ?? ""),
      side:           normSide(String(r["side"] ?? r["direction"] ?? "")),
      line:           parseFloat(String(r["line"] ?? "0")),
      platform:       String(r["platform"] ?? "manual"),
      payout_context: r["payout_context"] ? String(r["payout_context"]) : null,
      source_status:  "UNAVAILABLE" as const,
      source_grade:   "UNVERIFIED" as const,
      source:         "manual",
      timestamp:      nowIso(),
    })).filter(p => p.player && !isNaN(p.line));
    return res.json({ ok: true, props: normalized, errors: [], provider: "manual", raw_count: rawProps.length });
  }

  // Determine which providers to query
  const wantedProviders = new Set<string>(providers ?? ["opticodds", "propline", "sportsgameodds", "odds_api"]);
  const results = await Promise.allSettled([
    wantedProviders.has("opticodds")      ? fetchOpticOdds(sport, date)       : Promise.resolve(null),
    wantedProviders.has("propline")       ? fetchPropLine(sport, date)        : Promise.resolve(null),
    wantedProviders.has("sportsgameodds") ? fetchSportsGameOdds(sport, date)  : Promise.resolve(null),
    wantedProviders.has("odds_api")       ? fetchOddsApiProps(sport, date)    : Promise.resolve(null),
  ]);

  const allProps: WowProp[] = [];
  const allErrors: string[] = [];
  let anySuccess = false;

  for (const r of results) {
    if (r.status === "fulfilled" && r.value) {
      if (r.value.ok) {
        allProps.push(...r.value.props);
        anySuccess = true;
      } else {
        allErrors.push(...r.value.errors);
      }
    } else if (r.status === "rejected") {
      allErrors.push(String((r as PromiseRejectedResult).reason));
    }
  }

  if (!anySuccess) {
    // All providers failed — return mock with DATA_UNOBTAINABLE status
    const mocks = mockProps(sport);
    return res.json({
      ok: false,
      props: mocks,
      errors: allErrors,
      provider: "mock_fallback",
      source_status: "DATA_UNOBTAINABLE",
      raw_count: 0,
      note: "All provider fetches failed. Mock data returned. Do not use for approval.",
    });
  }

  return res.json({
    ok: true,
    props: dedup(allProps),
    errors: allErrors.length > 0 ? allErrors : undefined,
    provider: "multi",
    raw_count: allProps.length,
  });
});

/**
 * GET /api/props/providers
 * Returns which providers are configured (key present) vs not.
 */
router.get("/providers", (_req: Request, res: Response) => {
  res.json({
    providers: {
      opticodds:      { configured: !!PROVIDER_KEYS.opticodds,      grade: "A", note: "Player props, boxscores, odds" },
      propline:       { configured: !!PROVIDER_KEYS.propline,        grade: "A", note: "PrizePicks-compatible prop feed" },
      sportsgameodds: { configured: !!PROVIDER_KEYS.sportsgameodds,  grade: "B", note: "Player props, game lines" },
      odds_api:       { configured: !!PROVIDER_KEYS.odds_api,        grade: "A", note: "Sportsbook odds, player props" },
    },
    mock_available: true,
  });
});

/**
 * POST /api/props/score-batch
 * Normalize props then fire each through the Flask /final-lock endpoint.
 * Returns the full gate result per prop. All execution flags remain false.
 */
router.post("/score-batch", async (req: Request, res: Response) => {
  const FLASK_BASE = process.env["SCORING_API_URL"] ?? "http://localhost:25643";
  const API_KEY    = process.env["SCORING_API_KEY"]  ?? "";

  const { props } = req.body as { props?: WowProp[] };
  if (!props || !Array.isArray(props) || props.length === 0) {
    return res.status(400).json({ ok: false, error: "props array required" });
  }

  const results = await Promise.allSettled(props.map(async prop => {
    try {
      const r = await fetch(`${FLASK_BASE}/final-lock`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key":    API_KEY,
        },
        body: JSON.stringify({
          player:            prop.player,
          team:              prop.team,
          opponent:          prop.opponent,
          sport:             prop.sport,
          market:            prop.prop_type,
          side:              prop.side,
          pp_line:           prop.line,
          platform:          prop.platform,
          payout_context:    prop.payout_context ?? "2-pick Power",
          source:            prop.source,
          source_status:     prop.source_status,
        }),
        signal: AbortSignal.timeout(15000),
      });
      const body = await r.json() as unknown;
      return { prop, result: body, ok: true };
    } catch (err) {
      return { prop, result: { error: String(err), terminal_label: "DATA_UNOBTAINABLE" }, ok: false };
    }
  }));

  const output = results.map(r => {
    if (r.status === "fulfilled") return r.value;
    return { prop: null, result: { error: String(r.reason), terminal_label: "DATA_UNOBTAINABLE" }, ok: false };
  });

  return res.json({ ok: true, results: output, count: output.length, execution_rule: "READ_ONLY_NO_EXECUTION" });
});

export default router;
