# WOW Data Hub — Reliability, Validation & Audit Layer

_Last updated: 2026-07-07_

## Overview

The WOW Data Hub is a seven-layer reliability and audit system built on top of the Flask scoring backend. It enforces the principle that **bad data can never silently reach a money approval label**, and that every decision is observable, testable, and traceable.

---

## Component 1 — Terminal Bucket Regression Test Suite

**File:** `artifacts/flask-scoring-api/gate_engine/tests/test_terminal_buckets.py`

**Purpose:** Prove that every failure path routes to the correct terminal bucket and cannot silently reach `MONEY_QUALIFIED` or `FINAL_APPROVED`.

**Run:**
```bash
cd artifacts/flask-scoring-api
python -m pytest gate_engine/tests/test_terminal_buckets.py -v
```

### Test Scenarios

| Class | Scenario | Core Assertion |
|-------|----------|---------------|
| A | Missing L10 | Cannot approve; lands in REJECT_DATA_QUALITY / hold |
| B | Missing payout context | Cannot enter slip; no money labels |
| C | Missing status/role timestamps | Cannot approve |
| D | Missing market comparison | MARKET_UNAVAILABLE / null → blocked |
| E | Source conflict | SOURCE_CONFLICT sentinel blocked from money |
| F | Mock/fallback/DATA_UNOBTAINABLE | PROXY_ONLY, FAILED, NOT_CALLED all blocked |
| G | Negative edge | Zero or negative edge vs market → rejected |
| H | Correlated legs | Same-player multi-prop exposure flagged or blocked |
| I | Row count reconciliation | N in → N out; empty board no crash |
| J | Invalid label guard | No legacy labels (HOLD, WATCH, PASS) in output |
| K | DATA_CONTRACT_FAIL guard | Missing prop_type / sport / line never approves |

---

## Component 2 — Source Status Simulator

**Endpoint:** `POST /api/dev/source-status-sim`

**Purpose:** Simulate any `DataStatus` through the full scoring pipeline in a controlled, DEV-ONLY environment. Verifies that source-status-based approval caps are enforced.

### Request
```json
{
  "source_status": "DATA_UNOBTAINABLE",
  "sport": "WNBA",
  "prop_type": "points",
  "side": "LESS",
  "line": 20.5,
  "include_l10": true,
  "include_market": true,
  "include_status": true,
  "include_payout": true
}
```

### Approval Caps by Source Status

| Source Status | Max Label | Can Reach Money Qualified | Can Reach Final Approved |
|--------------|-----------|--------------------------|-------------------------|
| RETRIEVED | FINAL_APPROVED | ✅ | ✅ |
| RECONSTRUCTED | MONEY_QUALIFIED | ✅ | ❌ |
| PROXY_ONLY | MODEL_QUALIFIED_HOLD | ❌ | ❌ |
| DATA_UNOBTAINABLE | REJECT_DATA_QUALITY | ❌ | ❌ |
| INPUT_FAILURE | REJECT_DATA_QUALITY | ❌ | ❌ |
| SOURCE_CONFLICT | SOURCE_CONFLICT | ❌ | ❌ |
| NOT_CALLED | MODEL_QUALIFIED_HOLD | ❌ | ❌ |
| FAILED | REJECT_DATA_QUALITY | ❌ | ❌ |

### Response
```json
{
  "dev_only": true,
  "input_source_status": "DATA_UNOBTAINABLE",
  "approval_cap": "REJECT_DATA_QUALITY",
  "can_reach_money_qualified": false,
  "can_reach_final_approved": false,
  "terminal_bucket": "REJECT_DATA_QUALITY",
  "blockers": ["SOURCE_DATA_UNAVAILABLE"],
  "safety_check": {
    "expected_blocked_from_money": true,
    "actually_reached_money": false,
    "safety_gate_held": true
  }
}
```

---

## Component 3 — Postmortem / CLV Tracker

**Endpoints:**
- `GET  /api/postmortem/pending?limit=50&sport=NBA` — entries awaiting result settlement
- `POST /api/postmortem/update/:request_id` — settle a result, record CLV & process grade
- `GET  /api/postmortem/summary` — aggregate win rate, CLV beat rate, patch-needed count
- `GET  /api/postmortem/failure-tags` — ranked failure tag frequency

**DB Table:** `wow_postmortems` (auto-created at startup)

### Process Grade Taxonomy

| Grade | Meaning |
|-------|---------|
| CLEAN_WIN | Approved, won, gate logic validated |
| FRAGILE_WIN | Approved, won, but data was thin |
| LUCKY_WIN | Approved, won, but model was wrong about the why |
| BAD_BEAT | Approved, lost, but model was right |
| GOOD_PROCESS_LOSS | Rejected, would have lost — model correct |
| BAD_PROCESS_WIN | Rejected, would have won — model error |
| MODEL_FAILURE | Systematic error requiring rule patch |

### CLV Result Taxonomy

| Value | Meaning |
|-------|---------|
| BEAT_CLOSE | Closing line was worse than entry — market confirmed edge |
| LOST_TO_CLOSE | Market moved against position |
| TIED_CLOSE | No meaningful line movement |

### Update Payload
```json
{
  "player": "A'ja Wilson",
  "sport": "WNBA",
  "prop": "points",
  "side": "MORE",
  "line": 22.5,
  "terminal_label": "FINAL_APPROVED",
  "game_date": "2026-07-07",
  "actual_result": 26.0,
  "closing_line": 22.0,
  "closing_price": -115,
  "result_status": "WIN",
  "clv_result": "BEAT_CLOSE",
  "process_grade": "CLEAN_WIN",
  "dominant_failure_tag": null,
  "patch_needed": false
}
```

---

## Component 4 — Daily Smoke Test

**CLI:** `python artifacts/flask-scoring-api/scripts/smoke_test.py`

**HTTP:** `POST /api/admin/smoke-test`

Exit code: `0` for PASS/WARN, `1` for FAIL.

### Checks Performed (11 total)

| # | Check | Critical |
|---|-------|----------|
| 1 | `flask_health` — Flask `/health` responds ok | ✅ |
| 2 | `props_providers` — provider config count | ✅ |
| 3 | `props_normalize_mock` — mock props all labeled `DATA_UNOBTAINABLE` | ✅ |
| 4 | `mock_cannot_approve` — mock props never reach money labels | ✅ |
| 5 | `row_count_reconciliation` — N rows in = N rows out | ✅ |
| 6 | `odds_api_live` — live Odds API call succeeds | ⚠️ warn |
| 7 | `request_log` — `/request-log` endpoint reachable | ✅ |
| 8 | `leaderboard` — `/leaderboard` endpoint reachable | ✅ |
| 9 | `mcp_server_package` — `@modelcontextprotocol/sdk` present | ✅ |
| 10 | `sim_data_unobtainable` — safety gate holds for `DATA_UNOBTAINABLE` | ✅ |
| 11 | `sim_source_conflict` — safety gate holds for `SOURCE_CONFLICT` | ✅ |

```bash
# Run with custom endpoints
python scripts/smoke_test.py \
  --api http://localhost:8080/api \
  --flask http://localhost:25643 \
  --json
```

---

## Component 5 — MCP Security Hardening

**File:** `artifacts/mcp-server/src/index.js` (v2.0.0)

### Security Features

| Feature | Implementation |
|---------|---------------|
| Disallowed tools | `DISALLOWED_TOOLS` Set — `place_bet`, `submit_entry`, `execute_trade`, etc. return `LIVE_EXECUTION_DISABLED` error |
| Tool allowlist | `ALLOWED_TOOLS` Set — unknown tool names rejected with `TOOL_NOT_IN_ALLOWLIST` |
| Audit log | Every call appended to `artifacts/mcp-server/logs/mcp_audit.jsonl` (tool_name, timestamp, success, duration_ms, terminal_bucket, arg_keys only — never arg values) |
| Rate limiting | Max 30 calls / tool / 60s in-process (per session) |
| Secret scrubbing | Response serializer redacts any key matching `api_key`, `secret`, `token`, `password`, `auth` |
| Auth token | Set `MCP_AUTH_TOKEN` env var; leave empty for open (default) |
| Live execution guard | `can_execute` is never set to `true` by this server |

### Audit Log Entry Shape
```json
{
  "tool_name": "score_wow_prop",
  "timestamp": "2026-07-07T14:32:11.123Z",
  "request_id": "mcp-1720362731-ab3f2",
  "source": "mcp-stdio",
  "success": true,
  "terminal_bucket": "MODEL_QUALIFIED_HOLD",
  "duration_ms": 812,
  "error": null,
  "arg_keys": ["player", "sport", "market", "side", "pp_line"]
}
```

---

## Component 6 — Dashboard Filter Upgrades

### Request Log (`/final-lock` → Request Log tab)

New filters added (all client-side, composable):
- **Label** — terminal bucket filter (existing, now with chip toggle)
- **Sport** — NBA / WNBA / MLB / NFL / NHL / TENNIS
- **Source Status** — RETRIEVED / RECONSTRUCTED / PROXY_ONLY / DATA_UNOBTAINABLE / etc.
- **Process Grade** — CLEAN_WIN / FRAGILE_WIN / BAD_BEAT / MODEL_FAILURE / etc.
- **Blockers** — "Has blockers" / "No blockers" / Any
- **Date From / To** — ISO date range picker
- **Search** — now searches blockers text in addition to player/market/label

New features:
- **Export CSV** — downloads visible filtered rows as `.csv`
- Colored `source_status` badges in row list
- Expanded drawer shows Source Grade + Process Grade columns
- "Clear all filters" shortcut
- Live count "Showing N of M entries"

### Leaderboard (`/final-lock` → Leaderboard tab)

New filters:
- **Sport** — pill filter (only shows sports present in data)
- **Min Runs** — 1 / 3 / 5 / 10 / 25
- **Last Label** — filter rows by most recent terminal bucket
- **Approval Band** — ≥50% / 20–49% / <20%
- **Edge** — Positive / Negative only

New features:
- **Export CSV** — downloads visible filtered rows as `.csv`
- Row count indicator "N of M rows"
- Clear all filters shortcut

---

## Component 7 — Architecture Decisions

### Why separate `wow_postmortems` table?

`scoring_requests` is the engine's write-ahead log — it's written at scoring time. Postmortem fields (actual result, CLV, process grade) are populated _after_ the game plays out, sometimes days later. Keeping them separate:
- Zero migration risk to existing `scoring_requests` data
- Independent retention policies (postmortems kept longer)
- CLV analytics can join on a stable request_id key

### Why `DataStatus` caps are enforced at two layers?

1. **Python gate engine** (`pipeline.py`) — blocks per-row at source_grade / data_contract gates
2. **TypeScript simulator** (`dev-simulator.ts`) — re-derives caps from the same source_status taxonomy

Two-layer enforcement means the Python engine controls the ground truth, and the TypeScript layer verifies it via HTTP round-trip. A regression in either layer is detectable by the smoke test.

### Why MCP audit logs to a file, not the DB?

The MCP server is a stdio process — it may be started by Claude Desktop or GPT-4o without a database connection. File-based JSONL is always available, survives process restarts, and can be tail-piped or ingested by any log aggregator. DB logging can be added later via a sidecar process.

---

## Running Everything

```bash
# 1. Unit regression tests (no API keys required)
cd artifacts/flask-scoring-api
python -m pytest gate_engine/tests/test_terminal_buckets.py -v

# 2. Full smoke test (requires running services)
python scripts/smoke_test.py

# 3. Source status simulator (via HTTP)
curl -X POST http://localhost:8080/api/dev/source-status-sim \
  -H 'Content-Type: application/json' \
  -d '{"source_status":"DATA_UNOBTAINABLE","sport":"WNBA","prop_type":"points","side":"LESS","line":20.5}'

# 4. Admin smoke test via HTTP
curl -X POST http://localhost:8080/api/admin/smoke-test | jq .status

# 5. Postmortem summary
curl http://localhost:8080/api/postmortem/summary | jq .summary

# 6. MCP server (stdio transport — connect from Claude Desktop)
node artifacts/mcp-server/src/index.js

# Audit log location:
# artifacts/mcp-server/logs/mcp_audit.jsonl
```
