// ─────────────────────────────────────────────────────────────────────────────
// fetchTeamEvents(sport, dateStr, log)
// DROP-IN REPLACEMENT for all TheRundown event fetches in LLP + WU
//
// Fallback chain:
//   1. TheRundown  → /api/v2/sports/{id}/events/{YYYY-MM-DD}  (fixed path)
//   2. Odds API    → /v4/sports/{key}/odds  (full lines, uses existing key)
//   3. ESPN public → site.api.espn.com scoreboard  (no lines, games only)
//
// Returns: { events: [...], source: 'therundown'|'oddsapi'|'espn'|'empty' }
// Every event in the array has the same normalized shape:
//   { away_team, home_team, event_date, bookmakers: [...] }
// bookmakers follows the existing WU/LLP format the rest of the code expects.
// ─────────────────────────────────────────────────────────────────────────────

// ── SPORT ID / KEY MAPS ──────────────────────────────────────────────────────

const TRD_SPORT_IDS = {
  NFL:    1,
  MLB:    3,
  NBA:    4,
  NHL:    6,
  WNBA:   8,   // was 7 (UFC) — corrected per API probe 2026-05-24
  // Soccer: pick league(s) you care about — pass as array
  MLS:          10,
  EPL:          11,
  Ligue1:       12,
  Bundesliga:   13,
  LaLiga:       14,
  SerieA:       15,
  ChampionsLeague: 16,
};
// Alias so both LLP_TRD_SPORT_IDS and TRD_SPORT_IDS resolve correctly
const LLP_TRD_SPORT_IDS = TRD_SPORT_IDS;

const ODDS_API_SPORT_KEYS = {
  NFL:     'americanfootball_nfl',
  MLB:     'baseball_mlb',
  NBA:     'basketball_nba',
  NHL:     'icehockey_nhl',
  WNBA:    'basketball_wnba',
  Soccer:  'soccer_usa_mls',     // default soccer → MLS
  MLS:     'soccer_usa_mls',
  EPL:     'soccer_epl',
};

const ESPN_ENDPOINTS = {
  NBA:  'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard',
  WNBA: 'https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard',
  MLB:  'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard',
  NHL:  'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard',
  NFL:  'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard',
  MLS:  'https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/scoreboard',
  EPL:  'https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard',
  Soccer:'https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/scoreboard',
};

// ── NORMALIZERS ──────────────────────────────────────────────────────────────

// Converts TheRundown event → normalized shape
function normalizeTRDEvent(ev) {
  const linesObj = ev.lines || ev.affiliate_lines || {};
  // pick first affiliate that has a moneyline
  const lineData = Object.values(linesObj).find(l =>
    l?.moneyline?.moneyline_away != null && l?.moneyline?.moneyline_home != null
  ) || null;

  const ml  = lineData?.moneyline || {};
  const sp  = lineData?.spread    || {};
  const tot = lineData?.total     || {};

  const awayML = ml.moneyline_away;
  const homeML = ml.moneyline_home;

  const bookmakers = awayML != null ? [{
    key: 'therundown',
    title: 'TheRundown',
    markets: [
      {
        key: 'h2h',
        outcomes: [
          { name: ev.away_team, price: awayML },
          { name: ev.home_team, price: homeML },
        ]
      },
      ...(sp.line_away != null ? [{
        key: 'spreads',
        outcomes: [
          { name: ev.away_team, price: sp.spread_away ?? -110, point: sp.line_away },
          { name: ev.home_team, price: sp.spread_home ?? -110, point: sp.line_home },
        ]
      }] : []),
      ...(tot.total_over != null ? [{
        key: 'totals',
        outcomes: [
          { name: 'Over',  price: tot.over_odds  ?? -110, point: tot.total_over },
          { name: 'Under', price: tot.under_odds ?? -110, point: tot.total_over },
        ]
      }] : []),
    ]
  }] : [];

  return {
    away_team:  ev.away_team  || '',
    home_team:  ev.home_team  || '',
    event_date: ev.event_date || ev.event_date_utc || '',
    bookmakers,
  };
}

// Converts Odds API event → normalized shape (already in our expected format)
function normalizeOddsAPIEvent(ev) {
  return {
    away_team:  ev.away_team  || '',
    home_team:  ev.home_team  || '',
    event_date: ev.commence_time || '',
    bookmakers: ev.bookmakers || [],
  };
}

// Converts ESPN scoreboard event → normalized shape (no lines — games only)
function normalizeESPNEvent(ev) {
  const comp = ev.competitions?.[0];
  const away = comp?.competitors?.find(c => c.homeAway === 'away');
  const home = comp?.competitors?.find(c => c.homeAway === 'home');
  return {
    away_team:  away?.team?.displayName || away?.team?.name || '',
    home_team:  home?.team?.displayName || home?.team?.name || '',
    event_date: ev.date || '',
    bookmakers: [],   // ESPN has no odds — downstream calcs will skip
  };
}

// ── MAIN FETCH FUNCTION ──────────────────────────────────────────────────────

async function fetchTeamEvents(sport, dateStr, log = console.log) {
  // sport: 'NBA' | 'MLB' | 'NHL' | 'WNBA' | 'Soccer' | 'MLS' | 'EPL' | 'NFL'
  // dateStr: 'YYYY-MM-DD'

  // ── TIER 1: TheRundown ──────────────────────────────────────────────────
  const trdId = TRD_SPORT_IDS[sport];
  if (trdId) {
    try {
      // Correct path shape confirmed by API probe 2026-05-24
      const url = `${EP.rundown}/sports/${trdId}/events/${dateStr}`;
      const r = await fx(url, { headers: { 'X-TheRundown-Key': KEYS.rundown } }, 10000);

      if (r.status === 401) {
        log(`TheRundown ${sport}: 401 — key/plan unauthorized, falling back`, 'w');
      } else if (r.status === 404) {
        log(`TheRundown ${sport}: 404 — path issue, falling back`, 'w');
      } else if (!r.ok) {
        log(`TheRundown ${sport}: ${r.status} — falling back`, 'w');
      } else {
        const data = await r.json();
        const raw = data.events || data.data || [];
        if (raw.length > 0) {
          log(`TheRundown ${sport}: ${raw.length} events ✓`, 'i');
          return { events: raw.map(normalizeTRDEvent), source: 'therundown' };
        }
        log(`TheRundown ${sport}: 0 events returned, falling back`, 'w');
      }
    } catch (e) {
      log(`TheRundown ${sport}: fetch error — ${e.message}, falling back`, 'w');
    }
  } else {
    log(`TheRundown ${sport}: no sport ID configured, skipping to fallback`, 'w');
  }

  // ── TIER 2: Odds API ────────────────────────────────────────────────────
  const oddsKey = ODDS_API_SPORT_KEYS[sport];
  if (oddsKey && KEYS.oddsApi) {
    try {
      const url = `https://api.the-odds-api.com/v4/sports/${oddsKey}/odds` +
        `?apiKey=${KEYS.oddsApi}&regions=us&markets=h2h,spreads,totals&oddsFormat=american&dateFormat=iso`;
      const r = await fx(url, {}, 10000);

      if (r.status === 401) {
        log(`Odds API ${sport}: 401 — key invalid, falling back`, 'w');
      } else if (r.status === 429) {
        log(`Odds API ${sport}: 429 — quota exceeded, falling back`, 'w');
      } else if (!r.ok) {
        log(`Odds API ${sport}: ${r.status} — falling back`, 'w');
      } else {
        const data = await r.json();
        const raw = Array.isArray(data) ? data : [];
        // Filter to today's date only
        const todayEvents = raw.filter(ev => {
          const d = new Date(ev.commence_time);
          return d.toISOString().slice(0, 10) === dateStr;
        });
        if (todayEvents.length > 0) {
          log(`Odds API ${sport}: ${todayEvents.length} events ✓ (fallback)`, 'w');
          return { events: todayEvents.map(normalizeOddsAPIEvent), source: 'oddsapi' };
        }
        log(`Odds API ${sport}: 0 events for ${dateStr}, falling back`, 'w');
      }
    } catch (e) {
      log(`Odds API ${sport}: fetch error — ${e.message}, falling back`, 'w');
    }
  }

  // ── TIER 3: ESPN public scoreboard (no lines — games only) ──────────────
  const espnUrl = ESPN_ENDPOINTS[sport];
  if (espnUrl) {
    try {
      const r = await fx(espnUrl, {}, 8000);
      if (r.ok) {
        const data = await r.json();
        const raw = data.events || [];
        // Filter to today's games
        const todayEvents = raw.filter(ev => {
          const d = new Date(ev.date);
          return d.toISOString().slice(0, 10) === dateStr;
        });
        if (todayEvents.length > 0) {
          log(`ESPN ${sport}: ${todayEvents.length} games (no lines — limited mode)`, 'w');
          return { events: todayEvents.map(normalizeESPNEvent), source: 'espn' };
        }
        log(`ESPN ${sport}: 0 games for ${dateStr}`, 'w');
      } else {
        log(`ESPN ${sport}: ${r.status}`, 'w');
      }
    } catch (e) {
      log(`ESPN ${sport}: fetch error — ${e.message}`, 'w');
    }
  }

  // ── ALL SOURCES FAILED ───────────────────────────────────────────────────
  log(`${sport}: all sources failed — no events for ${dateStr}`, 'e');
  return { events: [], source: 'empty' };
}

// ── USAGE GUARD: log warning if ESPN fallback is being used ─────────────────
// Call this after fetchTeamEvents to warn downstream that no lines are available
function assertHasLines(fetchResult, sport, log = console.log) {
  if (fetchResult.source === 'espn') {
    log(`⚠️ ${sport} running in LIMITED MODE — ESPN fallback has no betting lines.` +
        ` No-vig calc, edge calc, and tier scoring will be skipped for this sport.`, 'w');
    return false;
  }
  if (fetchResult.source === 'empty') {
    log(`⚠️ ${sport}: no data available — board will be empty.`, 'w');
    return false;
  }
  return true;
}
// ─────────────────────────────────────────────────────────────────────────────
// CALL SITE PATCH — apply in both runLLPBoard() and the WU sport loop
//
// FIND in runLLPBoard() and in the WU sport loop — something like:
//   const trdR = await fx(`${EP.rundown}/events?key=...`, ...)
//   const trdData = await trdR.json();
//   const eventList = trdData.events || trdData.data || [];
//
// REPLACE the entire fetch + parse block with:
// ─────────────────────────────────────────────────────────────────────────────

// LLP call site (in runLLPBoard, inside the sport loop):
const { events: eventList, source: eventSource } = await fetchTeamEvents(sport, localDate(), log);
if (!assertHasLines({ events: eventList, source: eventSource }, sport, log) && eventList.length === 0) continue;
// "continue" skips this sport if ESPN fallback returned 0 games too
// If ESPN returned games but no lines, eventList.length > 0 but assertHasLines returns false
// — the board will render game names but skip all no-vig / edge calculations

// ─────────────────────────────────────────────────────────────────────────────
// WU call site (in the WU sport loop, same pattern):
const { events: eventList2, source: eventSource2 } = await fetchTeamEvents(cfg.sport, localDate(), log);
if (!assertHasLines({ events: eventList2, source: eventSource2 }, cfg.sport, log) && eventList2.length === 0) continue;
// Same logic as LLP — ESPN fallback shows games without lines

// ─────────────────────────────────────────────────────────────────────────────
// DOWNSTREAM — anywhere that reads bookmakers needs a null guard.
// Add this before the no-vig calculation in both engines:
//
// BEFORE:
//   const bk = ev.bookmakers[0];
//   const awayML = bk.markets.find(m => m.key === 'h2h')...
//
// AFTER:
//   const bk = ev.bookmakers?.[0];
//   if (!bk) continue;   // ESPN fallback — no lines, skip this game
//   const awayML = bk.markets?.find(m => m.key === 'h2h')...
// ─────────────────────────────────────────────────────────────────────────────

// SOURCE INDICATOR — optionally show data source in the Teams tab log:
// log(`Data source for ${sport}: ${eventSource}`, eventSource === 'therundown' ? 'i' : 'w');
