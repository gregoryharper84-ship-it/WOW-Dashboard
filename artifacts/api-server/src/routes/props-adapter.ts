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
 *
 * WOW v16 compliance:
 *  - source_status uses only the 8 canonical values (RETRIEVED, RECONSTRUCTED,
 *    PROXY_ONLY, DATA_UNOBTAINABLE, INPUT_FAILURE, SOURCE_CONFLICT, NOT_CALLED, FAILED).
 *  - terminal_label uses only the 12 canonical buckets.
 *  - team_confidence added: HIGH (roster-confirmed) | LOW (unknown/inferred).
 *  - LOW team_confidence props are capped at REJECT_DATA_QUALITY.
 */
import { Router, type Request, type Response } from "express";
import { pool } from "@workspace/db";

const router = Router();

// ── WOW v16 canonical source_status values ─────────────────────────────────
export type WowSourceStatus =
  | "RETRIEVED"
  | "RECONSTRUCTED"
  | "PROXY_ONLY"
  | "DATA_UNOBTAINABLE"
  | "INPUT_FAILURE"
  | "SOURCE_CONFLICT"
  | "NOT_CALLED"
  | "FAILED";

const VALID_SOURCE_STATUSES = new Set<WowSourceStatus>([
  "RETRIEVED", "RECONSTRUCTED", "PROXY_ONLY", "DATA_UNOBTAINABLE",
  "INPUT_FAILURE", "SOURCE_CONFLICT", "NOT_CALLED", "FAILED",
]);

// ── WOW v16 canonical terminal buckets ────────────────────────────────────
const VALID_TERMINAL_LABELS = new Set([
  "FINAL_APPROVED", "MONEY_QUALIFIED", "MARKET_VERIFIED_HOLD",
  "MODEL_QUALIFIED_HOLD", "RESEARCH_INTEREST", "SOURCE_CONFLICT",
  "REJECT_NO_EDGE", "REJECT_BAD_STRUCTURE", "REJECT_DATA_QUALITY",
  "SLATE_PURGE", "DUPLICATE_EXPOSURE_BLOCK", "NO_PLAY",
]);

// ── WOW Prop Schema ───────────────────────────────────────────────────────────
export interface WowProp {
  player:          string;
  team:            string;
  opponent:        string;
  /** HIGH = confirmed by roster/starter adapter. LOW = unknown or inferred. */
  team_confidence: "HIGH" | "LOW" | "UNKNOWN";
  sport:           string;
  game_date:       string;
  prop_type:       string;
  side:            "MORE" | "LESS";
  line:            number;
  platform:        string;
  provider:        string;
  payout_context:  string | null;
  source_status:   WowSourceStatus;
  source_grade:    "A" | "B" | "C" | "UNVERIFIED" | "UNKNOWN";
  source:          string;
  /** Initial blockers set by the adapter layer (before gate evaluation). */
  blockers:        string[];
  timestamp:       string;
}

interface NormalizeResult {
  ok:        boolean;
  props:     WowProp[];
  errors:    string[];
  provider:  string;
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

// ── Label normalisation helpers ───────────────────────────────────────────────

/**
 * Map any non-canonical source_status string to the nearest valid WOW v16 value.
 * LIVE → RETRIEVED, STALE → RECONSTRUCTED, UNAVAILABLE → DATA_UNOBTAINABLE.
 */
export function normalizeSourceStatus(raw: string | undefined): WowSourceStatus {
  if (!raw) return "DATA_UNOBTAINABLE";
  const up = raw.toUpperCase().trim();
  const REMAP: Record<string, WowSourceStatus> = {
    LIVE:        "RETRIEVED",
    ACTIVE:      "RETRIEVED",
    FRESH:       "RETRIEVED",
    STALE:       "RECONSTRUCTED",
    CACHED:      "RECONSTRUCTED",
    UNAVAILABLE: "DATA_UNOBTAINABLE",
    MISSING:     "DATA_UNOBTAINABLE",
    ERROR:       "FAILED",
    TIMEOUT:     "FAILED",
  };
  if (up in REMAP) return REMAP[up]!;
  if (VALID_SOURCE_STATUSES.has(up as WowSourceStatus)) return up as WowSourceStatus;
  return "DATA_UNOBTAINABLE";
}

/**
 * Map any non-canonical terminal label to the nearest valid WOW v16 bucket.
 * Never returns bare "REJECT", "PASS", "HOLD", "WATCH", or "UNKNOWN".
 */
export function normalizeTerminalLabel(
  raw: string | undefined,
  context?: { source_status?: string; blockers?: string[] },
): string {
  if (!raw) return "REJECT_DATA_QUALITY";
  const up = raw.toUpperCase().trim();
  if (VALID_TERMINAL_LABELS.has(up)) return up;

  const ss = (context?.source_status ?? "").toUpperCase();
  const bs = (context?.blockers ?? []).map(b => b.toUpperCase()).join(" ");

  // Bare REJECT family → bucket by context
  if (up === "REJECT" || up === "REJECTED" || up === "REJECT_COINFLIP") {
    if (/EDGE|NEGATIVE_EDGE/.test(bs)) return "REJECT_NO_EDGE";
    if (/STRUCT|SLIP|CORREL|BINARY/.test(bs)) return "REJECT_BAD_STRUCTURE";
    if (ss === "DATA_UNOBTAINABLE" || ss === "FAILED" || ss === "INPUT_FAILURE") return "REJECT_DATA_QUALITY";
    if (/DATA|STATUS|UNVERIFIED|UNAVAIL/.test(bs)) return "REJECT_DATA_QUALITY";
    return "REJECT_DATA_QUALITY"; // safe default
  }

  // Invalid approval-adjacent labels
  if (up === "PASS" || up === "APPROVED") return "RESEARCH_INTEREST";
  if (up === "HOLD" || up === "WATCH" || up === "CONDITIONAL") return "MODEL_QUALIFIED_HOLD";
  if (up === "UNKNOWN" || up === "INPUT_FAILURE") return "REJECT_DATA_QUALITY";
  if (up === "NO_PLAY" || up === "PURGE" || up === "SLATE_PURGE") return "SLATE_PURGE";
  if (up === "DUPLICATE" || up === "DUPE") return "DUPLICATE_EXPOSURE_BLOCK";

  return "REJECT_DATA_QUALITY";
}

/**
 * Enforce team_confidence cap: LOW confidence props cannot reach money labels.
 * Returns the capped label if necessary.
 */
function enforcTeamConfidenceCap(
  terminal_label: string,
  team_confidence: WowProp["team_confidence"],
): string {
  if (team_confidence !== "LOW") return terminal_label;
  const MONEY_LABELS = new Set(["FINAL_APPROVED", "MONEY_QUALIFIED", "MARKET_VERIFIED_HOLD"]);
  if (MONEY_LABELS.has(terminal_label)) return "REJECT_DATA_QUALITY";
  return terminal_label;
}

// ── Mock data (used when no API keys are configured) ──────────────────────────
function mockProps(sport: string): WowProp[] {
  const base = {
    team:            "MOCK TEAM",
    opponent:        "OPP TEAM",
    team_confidence: "UNKNOWN" as const,
    sport:           normSport(sport || "NBA"),
    game_date:       new Date().toISOString().slice(0, 10),
    payout_context:  "2-pick Power",
    source_status:   "DATA_UNOBTAINABLE" as const,
    source_grade:    "UNKNOWN" as const,
    source:          "mock",
    provider:        "mock_fallback",
    // DATA_UNOBTAINABLE + MOCK_FALLBACK_SOURCE must precede STATUS_NOT_CONFIRMED
    blockers:        ["DATA_UNOBTAINABLE", "MOCK_FALLBACK_SOURCE"],
    timestamp:       nowIso(),
  };
  return [
    { ...base, player: "Mock Player A", prop_type: "Points",    side: "MORE", line: 22.5, platform: "mock" },
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
          const team     = String(fix["team_abbreviation"] ?? "");
          const opponent = String(fix["opponent_abbreviation"] ?? "");
          props.push({
            player:          String(fix["player_name"] ?? ""),
            team,
            opponent,
            // OpticOdds includes team_abbreviation; treat as HIGH confidence
            team_confidence: (team && team !== "UNKNOWN") ? "HIGH" : "LOW",
            sport:           normSport(String(fix["sport"] ?? sport)),
            game_date:       String(fix["game_date"] ?? date ?? nowIso().slice(0, 10)),
            prop_type:       String(mkt["name"] ?? ""),
            side:            normSide(String(out["name"] ?? "")),
            line,
            platform:        "opticodds",
            provider:        "opticodds",
            payout_context:  null,
            source_status:   "RETRIEVED",
            source_grade:    "A",
            source:          "opticodds",
            blockers:        (team && team !== "UNKNOWN") ? [] : ["PLAYER_TEAM_UNVERIFIED"],
            timestamp:       nowIso(),
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
    const props: WowProp[] = rows.map((row): WowProp => {
      const team     = String(row["team"] ?? "");
      const opponent = String(row["opponent"] ?? "");
      const tc: WowProp["team_confidence"] = (team && team !== "UNKNOWN") ? "HIGH" : "LOW";
      return {
        player:          String(row["player_name"] ?? row["player"] ?? ""),
        team,
        opponent,
        team_confidence: tc,
        sport:           normSport(String(row["sport"] ?? sport)),
        game_date:       String(row["game_date"] ?? date ?? nowIso().slice(0, 10)),
        prop_type:       String(row["stat_type"] ?? row["market"] ?? ""),
        side:            normSide(String(row["position"] ?? row["side"] ?? "")),
        line:            parseFloat(String(row["line_score"] ?? row["line"] ?? "0")),
        platform:        String(row["book"] ?? "propline"),
        provider:        "propline",
        payout_context:  null,
        source_status:   "RETRIEVED",
        source_grade:    "A",
        source:          "propline",
        blockers:        (team && team !== "UNKNOWN") ? [] : ["PLAYER_TEAM_UNVERIFIED"],
        timestamp:       nowIso(),
      };
    }).filter(p => !isNaN(p.line));
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
        // SportsGameOdds does not expose player-level team; only event-level home/away.
        // We cannot safely assign a player to home or away without participant data.
        props.push({
          player:          String(pp["playerName"] ?? pp["player_name"] ?? ""),
          team:            "UNKNOWN",
          opponent:        "UNKNOWN",
          team_confidence: "LOW",
          sport:           normSport(String(evt["sport"] ?? sport)),
          game_date:       String(evt["gameDate"] ?? evt["game_date"] ?? date ?? nowIso().slice(0, 10)),
          prop_type:       String(pp["marketType"] ?? pp["market"] ?? ""),
          side:            normSide(String(pp["side"] ?? "")),
          line,
          platform:        "sportsgameodds",
          provider:        "sportsgameodds",
          payout_context:  null,
          source_status:   "RETRIEVED",
          source_grade:    "B",
          source:          "sportsgameodds",
          blockers:        ["PLAYER_TEAM_UNVERIFIED"],
          timestamp:       nowIso(),
        });
      }
    }
    return { ok: true, props, errors: [], provider: "sportsgameodds", raw_count: events.length };
  } catch (err) {
    return { ok: false, props: [], errors: [String(err)], provider: "sportsgameodds", raw_count: 0 };
  }
}

// ── The Odds API player props adapter ────────────────────────────────────────
// Player props require a 2-step call:
//   Step 1: GET /v4/sports/{sport}/events  → list of event IDs
//   Step 2: GET /v4/sports/{sport}/events/{id}/odds?markets=... → per-event player props
//
// NOTE: The Odds API player prop outcomes include player name (in `description`)
// but do NOT include the player's actual team. We must NOT infer team from
// evt.home_team or evt.away_team — that is event-level, not player-level.
// All Odds API props get team: "UNKNOWN", team_confidence: "LOW".
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

  // Odds API v4 player prop market keys by sport
  const MARKETS: Record<string, string> = {
    basketball_nba:       "player_points,player_rebounds,player_assists,player_threes",
    basketball_wnba:      "player_points,player_rebounds,player_assists",
    baseball_mlb:         "batter_hits,batter_home_runs,pitcher_strikeouts",
    americanfootball_nfl: "player_reception_yards,player_rush_yards,player_receptions",
    icehockey_nhl:        "player_shots_on_goal,player_points",
  };
  const markets = MARKETS[sportKey] ?? "player_points";

  try {
    // Step 1: fetch upcoming events for the sport
    const eventsUrl = `${ODDS_API_BASE}/sports/${sportKey}/events?apiKey=${key}&dateFormat=iso`;
    const eventsResp = await fetch(eventsUrl, {
      headers: { "Accept": "application/json" },
      signal: AbortSignal.timeout(10000),
    });
    if (!eventsResp.ok) {
      const txt = await eventsResp.text().catch(() => "");
      return { ok: false, props: [], errors: [`Odds API events HTTP ${eventsResp.status}: ${txt.slice(0,120)}`], provider: "odds_api", raw_count: 0 };
    }
    const events = await eventsResp.json() as Record<string, unknown>[];
    if (!events.length) {
      return { ok: false, props: [], errors: [`Odds API: no ${sportKey} events found`], provider: "odds_api", raw_count: 0 };
    }

    // Filter by date if supplied; cap at 6 events to stay within API quota
    const targetDate = date ?? nowIso().slice(0, 10);
    const filtered = events
      .filter(e => !date || String(e["commence_time"] ?? "").startsWith(targetDate))
      .slice(0, 6);

    if (!filtered.length) {
      return { ok: false, props: [], errors: [`Odds API: no ${sportKey} events for ${targetDate}`], provider: "odds_api", raw_count: 0 };
    }

    // Step 2: fetch per-event player prop odds (parallel, up to 6 events)
    const props: WowProp[] = [];
    const oddsResults = await Promise.allSettled(
      filtered.map(async evt => {
        const evtId = String(evt["id"] ?? "");
        const oddsParams = new URLSearchParams({
          apiKey:    key,
          regions:   "us",
          markets,
          bookmakers:"draftkings,fanduel,betmgm",
          dateFormat:"iso",
        });
        const oddsUrl = `${ODDS_API_BASE}/sports/${sportKey}/events/${evtId}/odds?${oddsParams}`;
        const oddsResp = await fetch(oddsUrl, {
          headers: { "Accept": "application/json" },
          signal: AbortSignal.timeout(10000),
        });
        if (!oddsResp.ok) return [];
        const evtOdds = await oddsResp.json() as Record<string, unknown>;
        const bookmakers = (evtOdds["bookmakers"] ?? []) as Record<string, unknown>[];
        const out: WowProp[] = [];
        for (const bm of bookmakers) {
          const mkts = (bm["markets"] ?? []) as Record<string, unknown>[];
          for (const mkt of mkts) {
            const outcomes = (mkt["outcomes"] ?? []) as Record<string, unknown>[];
            for (const o of outcomes) {
              const line = parseFloat(String(o["point"] ?? ""));
              if (isNaN(line)) continue;
              const desc = String(o["description"] ?? "");
              const name = String(o["name"] ?? "");
              out.push({
                player:          desc || name,
                // The Odds API does NOT provide player-team mapping in player prop responses.
                // home_team/away_team are event-level and must NOT be assigned to a player.
                team:            "UNKNOWN",
                opponent:        "UNKNOWN",
                team_confidence: "LOW",
                sport:           normSport(String(evt["sport_key"] ?? sport)),
                game_date:       String((evt["commence_time"] as string)?.slice(0, 10) ?? targetDate),
                prop_type:       String(mkt["key"] ?? "").replace(/_/g, " "),
                side:            normSide(name),
                line,
                platform:        String(bm["key"] ?? "odds_api"),
                provider:        "odds_api",
                payout_context:  null,
                source_status:   "RETRIEVED",
                source_grade:    "A",
                source:          "odds_api",
                blockers:        ["PLAYER_TEAM_UNVERIFIED"],
                timestamp:       nowIso(),
              });
            }
          }
        }
        return out;
      })
    );

    for (const r of oddsResults) {
      if (r.status === "fulfilled") props.push(...r.value);
    }

    return { ok: props.length > 0, props, errors: props.length === 0 ? ["Odds API: no player prop markets in response"] : [], provider: "odds_api", raw_count: filtered.length };
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

// ── Score-batch audit log ─────────────────────────────────────────────────────
// Writes lightweight entries to `scoring_requests` (Flask's table) so
// /request-log returns score_batch rows. Filter: environment=score_batch.
async function writeBatchAuditEntry(
  prop: WowProp,
  terminal_label: string,
): Promise<void> {
  try {
    await pool.query(
      `INSERT INTO scoring_requests
         (timestamp, player, sport, prop, side, line, score, label, game_date, environment)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
      [
        nowIso(),
        prop.player,
        prop.sport,
        prop.prop_type,
        prop.side,
        prop.line,
        terminal_label,
        terminal_label,
        prop.game_date,
        "score_batch",
      ],
    );
  } catch {
    // Best-effort; never fail the main response due to audit write failure.
  }
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
    const normalized: WowProp[] = rawProps.map(r => {
      const rawSS = normalizeSourceStatus(String(r["source_status"] ?? "UNAVAILABLE"));
      return {
        player:          String(r["player"] ?? ""),
        team:            String(r["team"] ?? "UNKNOWN"),
        opponent:        String(r["opponent"] ?? "UNKNOWN"),
        team_confidence: "UNKNOWN" as const,
        sport:           normSport(String(r["sport"] ?? sport)),
        game_date:       String(r["game_date"] ?? date ?? nowIso().slice(0, 10)),
        prop_type:       String(r["prop_type"] ?? r["market"] ?? ""),
        side:            normSide(String(r["side"] ?? r["direction"] ?? "")),
        line:            parseFloat(String(r["line"] ?? "0")),
        platform:        String(r["platform"] ?? "manual"),
        provider:        "manual",
        payout_context:  r["payout_context"] ? String(r["payout_context"]) : null,
        source_status:   rawSS,
        source_grade:    "UNVERIFIED" as const,
        source:          "manual",
        blockers:        [],
        timestamp:       nowIso(),
      };
    }).filter(p => p.player && !isNaN(p.line));
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
      data_unobtainable: true,
      provider_used: "mock_fallback",
      props: mocks,
      errors: allErrors,
      raw_count: 0,
      note: "All provider fetches failed. Mock data returned. source_status=DATA_UNOBTAINABLE on every row. Do not use for approval.",
    });
  }

  // Determine which provider(s) actually contributed
  const successProviders = results
    .map(r => (r.status === "fulfilled" && r.value?.ok) ? r.value.provider : null)
    .filter(Boolean);

  return res.json({
    ok: true,
    data_unobtainable: false,
    provider_used: successProviders.length === 1 ? successProviders[0] : "multi",
    props: dedup(allProps),
    errors: allErrors.length > 0 ? allErrors : undefined,
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
 *
 * Audit trail: every scored prop is written to scoring_requests
 * (environment='score_batch') so it appears in /request-log.
 * Filter: GET /request-log?environment=score_batch  (maps to run_source=score_batch)
 *
 * LOW team_confidence props are capped at REJECT_DATA_QUALITY and
 * can never reach MONEY_QUALIFIED or FINAL_APPROVED.
 */
router.post("/score-batch", async (req: Request, res: Response) => {
  const FLASK_BASE = process.env["SCORING_API_URL"] ?? "http://localhost:25643";
  const API_KEY    = process.env["SCORING_API_KEY"]  ?? "";

  const { props } = req.body as { props?: WowProp[] };
  if (!props || !Array.isArray(props) || props.length === 0) {
    return res.status(400).json({ ok: false, error: "props array required" });
  }

  const results = await Promise.allSettled(props.map(async prop => {
    // Normalize source_status before sending to Flask
    const normalizedSS = normalizeSourceStatus(prop.source_status);

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
          prop:              prop.prop_type,
          side:              prop.side,
          line:              prop.line,
          pp_line:           prop.line,
          platform:          prop.platform,
          payout_context:    prop.payout_context ?? "2-pick Power",
          source:            prop.source,
          source_status:     normalizedSS,
        }),
        signal: AbortSignal.timeout(15000),
      });
      const raw = await r.json() as Record<string, unknown>;

      // Normalize Flask /final-lock response terminal label
      const rawLabel = (
        (raw["terminal_label"] as string | undefined) ??
        (raw["classification"] as string | undefined) ??
        (raw["label"] as string | undefined) ??
        "UNKNOWN"
      );
      const flaskBlockers = [
        ...(prop.blockers ?? []),
        ...(Array.isArray(raw["blockers"]) ? (raw["blockers"] as string[]) : []),
        ...(raw["blocker_code"] ? [String(raw["blocker_code"])] : []),
        ...(raw["blocker"] ? [String(raw["blocker"])] : []),
      ];
      const terminal_label = enforcTeamConfidenceCap(
        normalizeTerminalLabel(rawLabel, { source_status: normalizedSS, blockers: flaskBlockers }),
        prop.team_confidence ?? "UNKNOWN",
      );

      const result = {
        ...raw,
        terminal_label,
        source_status:   normalizedSS,
        team_confidence: prop.team_confidence ?? "UNKNOWN",
        blocker:         raw["blocker_code"] ?? raw["blocker"] ?? null,
        can_execute:     false,
        execution_rule:  "READ_ONLY_NO_EXECUTION",
        run_source:      "score_batch",
      };

      // Audit log — best-effort, never blocks response
      void writeBatchAuditEntry({ ...prop, source_status: normalizedSS }, terminal_label);

      return { prop, result, ok: true };
    } catch (err) {
      const terminal_label = "REJECT_DATA_QUALITY";
      void writeBatchAuditEntry({ ...prop, source_status: normalizedSS }, terminal_label);
      return {
        prop,
        result: {
          error: String(err),
          terminal_label,
          source_status: normalizedSS,
          team_confidence: prop.team_confidence ?? "UNKNOWN",
          can_execute: false,
          execution_rule: "READ_ONLY_NO_EXECUTION",
          run_source: "score_batch",
        },
        ok: false,
      };
    }
  }));

  const rows = results.map(r => {
    if (r.status === "fulfilled") return r.value;
    return { prop: null, result: { error: String(r.reason), terminal_label: "REJECT_DATA_QUALITY" }, ok: false };
  });

  return res.json({
    ok:          rows.every(r => r.ok),
    count:       rows.length,
    results:     rows,
    can_execute: false,
    execution_rule: "READ_ONLY_NO_EXECUTION",
    audit_note:  "Each scored prop written to scoring_requests (environment=score_batch). Filter via GET /request-log?environment=score_batch.",
  });
});

/**
 * POST /api/props/normalize-labels
 * DEV endpoint: accepts a raw label and returns its normalized WOW v16 form.
 * Useful for verifying the normalization table is correct.
 */
router.post("/normalize-labels", (req: Request, res: Response) => {
  const { source_status, terminal_label, blockers } = req.body as {
    source_status?: string;
    terminal_label?: string;
    blockers?: string[];
  };
  res.json({
    input: { source_status, terminal_label, blockers },
    normalized: {
      source_status:  normalizeSourceStatus(source_status),
      terminal_label: normalizeTerminalLabel(terminal_label, { source_status, blockers }),
    },
    valid_source_statuses: [...VALID_SOURCE_STATUSES],
    valid_terminal_labels: [...VALID_TERMINAL_LABELS],
  });
});

export default router;
