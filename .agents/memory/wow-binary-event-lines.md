---
name: WOW binary-event line structural cap
description: Why 0.5-line props (e.g. MLB Hitter Hits LESS 0.5) need a structural cap distinct from the statistical coinflip check.
---

WOW's Gate 3 `REJECT_COINFLIP` only fires on weak gap%/hit-rate stats (gap_pct < 8% AND hit-rate below floor). It does not catch props that are structurally near-binary (a 0.5 line — "did it happen at all this game") but happen to have a strong gap%/hit-rate, so those could sail through to MODEL_QUALIFIED_HOLD despite being single-occurrence, high-variance outcomes.

**Why:** an external multi-agent review of a live scan (July 2, 2026) found most "model-qualified" rows were 0.5 MLB hitter-hit LESS props that should have been purged — confirming the engine had no rule purging on line *shape*, only on score/edge quality.

**How to apply:** any prop with `line == 0.5` is capped at WATCH / purged pre-scoring, sport-agnostic, independent of stats. Enforced at all three scoring entry points so none can be missed: `POST /wow/l10/gate3` (Gate 3, app.py), `_jf_slate_purge()` (JF lane, app.py), and `classify_prop()` in `jobs/wow_daily_scan.py` (main daily scan — this is the path that actually produces "Model Qualified — PrizePicks" rows). If a new scoring/classification entry point is added later, it needs the same `line == 0.5` structural cap or the leak reappears.
