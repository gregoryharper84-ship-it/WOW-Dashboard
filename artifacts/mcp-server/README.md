# WOW Data Hub — MCP Tool Server

Exposes the WOW scoring engine as MCP-callable tools for Claude Desktop,
ChatGPT (via function calling), or any MCP-compatible agent.

## Tools

| Tool | Description |
|---|---|
| `scan_board` | Full daily scan across sports → terminal buckets |
| `normalize_props` | Fetch + normalize props from OpticOdds/PropLine/SportsGameOdds/Odds API |
| `fetch_sportsbook_odds` | Pull odds, compute no-vig probability |
| `fetch_kalshi_markets` | Scan Kalshi read-only (sports/weather/macro/politics) |
| `fetch_player_status` | Player injury/status + game log |
| `fetch_l10_ledger` | L5/L10 exact-line ledger or reconstruction |
| `compare_market_edge` | Board vs sportsbook cushion + edge math |
| `score_wow_prop` | Full WOW gate run on one prop |
| `run_final_lock` | Final Lock gate (all 12 checks) |
| `build_slip_candidates` | Gate-engine batch → slip candidates |
| `export_no_play_report` | NO_PLAY / REJECT audit report |
| `get_request_log` | Full request history with blockers |
| `get_leaderboard` | Player/market approval rates |

## Transport

stdio — plug into Claude Desktop `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "wow-data-hub": {
      "command": "node",
      "args": ["/path/to/artifacts/mcp-server/src/index.js"],
      "env": {
        "SCORING_API_URL": "http://localhost:25643",
        "SCORING_API_KEY": "<your key>",
        "API_SERVER_URL": "http://localhost:8080/api",
        "ODDS_API_KEY": "<your key>"
      }
    }
  }
}
```

## Rules

- **Read-only.** No order placement, no live execution.
- Returns `DATA_UNOBTAINABLE` when sources fail — never fabricates.
- `can_execute` is always false in all scoring responses.
- `NO_PLAY` is always a valid output — never forced picks.
