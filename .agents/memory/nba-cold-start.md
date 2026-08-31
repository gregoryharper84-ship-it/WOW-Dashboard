---
name: nba_api cold-start fix
description: nba_api must never be imported at module level — 10.7s import causes healthcheck 500s during restarts
---

## Rule
Never import nba_api at the module level in app.py. Always use _nba_ensure() lazy-init.

**Why:** The nba_api import (stats.nba.com client + pandas) takes ~10.7 seconds.
Flask/gunicorn runs all module-level code before accepting connections.
legacy platform deployment healthchecks fire continuously during restarts.
Any caller (LLP governance sync, monitoring) hitting the server during the
~13s boot window gets a 500, which is indistinguishable from a real error.

**How to apply:**
- Call `_nba_ensure()` at the top of any function that uses `_nba_players_static`
  or `_nba_gamelog_ep`.
- The function sets `_NBA_API_AVAILABLE`, `_NBA_OK`, `_nba_players_static`,
  and `_nba_gamelog_ep` globals on first call.
- If you add a new function that uses nba_api internals, add `if not _nba_ensure(): return ...`
  as the first guard — do NOT import at module level.
- Cold start went from ~11,000ms to ~1,316ms after this fix.
