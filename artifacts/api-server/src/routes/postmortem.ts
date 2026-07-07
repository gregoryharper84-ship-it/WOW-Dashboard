/**
 * postmortem.ts — WOW CLV & Postmortem Tracker
 *
 * Extends the scoring log with result tracking, CLV, and process grading.
 * Uses a dedicated `wow_postmortems` table (separate from scoring_requests).
 *
 * Routes:
 *   GET  /api/postmortem/pending          — entries without a settled result
 *   POST /api/postmortem/update/:request_id
 *   GET  /api/postmortem/summary          — aggregate stats
 *   GET  /api/postmortem/failure-tags     — ranked failure tag counts
 */
import { Router, type Request, type Response } from "express";
import { pool } from "@workspace/db";

const router = Router();

// ── Schema bootstrap ──────────────────────────────────────────────────────────
// Create wow_postmortems table if not present. Runs once at startup.
async function ensureSchema() {
  const client = await pool.connect();
  try {
    await client.query(`
      CREATE TABLE IF NOT EXISTS wow_postmortems (
        id                  SERIAL PRIMARY KEY,
        request_id          TEXT NOT NULL UNIQUE,
        player              TEXT,
        sport               TEXT,
        prop                TEXT,
        side                TEXT,
        line                NUMERIC,
        terminal_label      TEXT,
        game_date           DATE,
        created_at          TIMESTAMPTZ DEFAULT NOW(),

        -- Postmortem fields
        actual_result       NUMERIC,
        closing_line        NUMERIC,
        closing_price       INTEGER,
        result_status       TEXT DEFAULT 'UNKNOWN'
                              CHECK (result_status IN ('WIN','LOSS','PUSH','VOID','UNKNOWN')),
        clv_result          TEXT DEFAULT 'UNKNOWN'
                              CHECK (clv_result IN ('BEAT_CLOSE','LOST_TO_CLOSE','TIED_CLOSE','UNKNOWN')),
        process_grade       TEXT DEFAULT 'UNKNOWN'
                              CHECK (process_grade IN (
                                'CLEAN_WIN','FRAGILE_WIN','LUCKY_WIN','FALSE_SIGNAL_WIN',
                                'BAD_BEAT','GOOD_PROCESS_LOSS','BAD_PROCESS_WIN',
                                'MODEL_FAILURE','UNKNOWN'
                              )),
        dominant_failure_tag  TEXT,
        patch_needed          BOOLEAN DEFAULT FALSE,
        future_rule           TEXT,
        postmortem_notes      TEXT,
        settled_at            TIMESTAMPTZ
      )
    `);
    await client.query(`
      CREATE INDEX IF NOT EXISTS wow_postmortems_game_date ON wow_postmortems(game_date DESC);
      CREATE INDEX IF NOT EXISTS wow_postmortems_sport      ON wow_postmortems(sport);
      CREATE INDEX IF NOT EXISTS wow_postmortems_result     ON wow_postmortems(result_status);
      CREATE INDEX IF NOT EXISTS wow_postmortems_settled    ON wow_postmortems(settled_at);
    `);
  } finally {
    client.release();
  }
}

ensureSchema().catch(err =>
  console.error("[postmortem] Schema bootstrap error:", err)
);

// ── Helpers ───────────────────────────────────────────────────────────────────
function parseIntOr(v: unknown, fallback: number): number {
  const n = parseInt(String(v ?? ""), 10);
  return isNaN(n) ? fallback : n;
}

// ── GET /api/postmortem/pending ───────────────────────────────────────────────
router.get("/pending", async (req: Request, res: Response) => {
  const limit = Math.min(parseIntOr(req.query["limit"], 50), 200);
  const sport = req.query["sport"] as string | undefined;

  const client = await pool.connect();
  try {
    const params: unknown[] = [];
    let where = "WHERE settled_at IS NULL";
    if (sport) {
      params.push(sport.toUpperCase());
      where += ` AND sport = $${params.length}`;
    }
    params.push(limit);
    const { rows } = await client.query(
      `SELECT * FROM wow_postmortems ${where} ORDER BY created_at DESC LIMIT $${params.length}`,
      params
    );
    return res.json({ ok: true, count: rows.length, pending: rows });
  } catch (err) {
    return res.status(503).json({ ok: false, error: String(err) });
  } finally {
    client.release();
  }
});

// ── POST /api/postmortem/update/:request_id ───────────────────────────────────
router.post("/update/:request_id", async (req: Request, res: Response) => {
  const { request_id } = req.params;
  const {
    player,
    sport,
    prop,
    side,
    line,
    terminal_label,
    game_date,
    actual_result,
    closing_line,
    closing_price,
    result_status      = "UNKNOWN",
    clv_result         = "UNKNOWN",
    process_grade      = "UNKNOWN",
    dominant_failure_tag,
    patch_needed       = false,
    future_rule,
    postmortem_notes,
  } = req.body as Record<string, unknown>;

  const client = await pool.connect();
  try {
    await client.query(`
      INSERT INTO wow_postmortems (
        request_id, player, sport, prop, side, line, terminal_label, game_date,
        actual_result, closing_line, closing_price,
        result_status, clv_result, process_grade,
        dominant_failure_tag, patch_needed, future_rule, postmortem_notes,
        settled_at
      ) VALUES (
        $1,$2,$3,$4,$5,$6,$7,$8,
        $9,$10,$11,
        $12,$13,$14,
        $15,$16,$17,$18,
        NOW()
      )
      ON CONFLICT (request_id) DO UPDATE SET
        actual_result        = EXCLUDED.actual_result,
        closing_line         = EXCLUDED.closing_line,
        closing_price        = EXCLUDED.closing_price,
        result_status        = EXCLUDED.result_status,
        clv_result           = EXCLUDED.clv_result,
        process_grade        = EXCLUDED.process_grade,
        dominant_failure_tag = EXCLUDED.dominant_failure_tag,
        patch_needed         = EXCLUDED.patch_needed,
        future_rule          = EXCLUDED.future_rule,
        postmortem_notes     = EXCLUDED.postmortem_notes,
        settled_at           = CASE
          WHEN wow_postmortems.settled_at IS NULL THEN NOW()
          ELSE wow_postmortems.settled_at
        END
    `, [
      request_id, player ?? null, sport ?? null, prop ?? null,
      side ?? null, line ?? null, terminal_label ?? null, game_date ?? null,
      actual_result ?? null, closing_line ?? null, closing_price ?? null,
      result_status, clv_result, process_grade,
      dominant_failure_tag ?? null, patch_needed, future_rule ?? null,
      postmortem_notes ?? null,
    ]);

    const { rows } = await client.query(
      "SELECT * FROM wow_postmortems WHERE request_id = $1", [request_id]
    );
    return res.json({ ok: true, postmortem: rows[0] });
  } catch (err) {
    return res.status(400).json({ ok: false, error: String(err) });
  } finally {
    client.release();
  }
});

// ── GET /api/postmortem/summary ───────────────────────────────────────────────
router.get("/summary", async (_req: Request, res: Response) => {
  const client = await pool.connect();
  try {
    const { rows: [agg] } = await client.query(`
      SELECT
        COUNT(*)                                            AS total_settled,
        COUNT(*) FILTER (WHERE result_status = 'WIN')      AS wins,
        COUNT(*) FILTER (WHERE result_status = 'LOSS')     AS losses,
        COUNT(*) FILTER (WHERE result_status = 'PUSH')     AS pushes,
        COUNT(*) FILTER (WHERE result_status = 'VOID')     AS voids,
        COUNT(*) FILTER (WHERE result_status = 'UNKNOWN')  AS unsettled,
        COUNT(*) FILTER (WHERE clv_result = 'BEAT_CLOSE')  AS clv_beat,
        COUNT(*) FILTER (WHERE clv_result = 'LOST_TO_CLOSE') AS clv_lost,
        COUNT(*) FILTER (WHERE patch_needed = TRUE)        AS patch_needed_count,
        ROUND(
          100.0 * COUNT(*) FILTER (WHERE result_status = 'WIN') /
          NULLIF(COUNT(*) FILTER (WHERE result_status IN ('WIN','LOSS')), 0),
          2
        )                                                   AS win_rate_pct,
        ROUND(
          100.0 * COUNT(*) FILTER (WHERE clv_result = 'BEAT_CLOSE') /
          NULLIF(COUNT(*) FILTER (WHERE clv_result != 'UNKNOWN'), 0),
          2
        )                                                   AS clv_beat_rate_pct
      FROM wow_postmortems
      WHERE settled_at IS NOT NULL
    `);

    const { rows: byBucket } = await client.query(`
      SELECT
        terminal_label,
        COUNT(*) AS runs,
        COUNT(*) FILTER (WHERE result_status = 'WIN') AS wins,
        ROUND(
          100.0 * COUNT(*) FILTER (WHERE result_status = 'WIN') /
          NULLIF(COUNT(*) FILTER (WHERE result_status IN ('WIN','LOSS')), 0),
          2
        ) AS win_rate_pct
      FROM wow_postmortems
      WHERE settled_at IS NOT NULL AND terminal_label IS NOT NULL
      GROUP BY terminal_label
      ORDER BY runs DESC
    `);

    const { rows: bySport } = await client.query(`
      SELECT sport,
        COUNT(*) AS runs,
        ROUND(
          100.0 * COUNT(*) FILTER (WHERE result_status = 'WIN') /
          NULLIF(COUNT(*) FILTER (WHERE result_status IN ('WIN','LOSS')), 0),
          2
        ) AS approval_rate_pct
      FROM wow_postmortems
      WHERE settled_at IS NOT NULL AND sport IS NOT NULL
      GROUP BY sport ORDER BY runs DESC
    `);

    return res.json({
      ok: true,
      summary: agg,
      by_terminal_bucket: byBucket,
      by_sport: bySport,
    });
  } catch (err) {
    return res.status(503).json({ ok: false, error: String(err) });
  } finally {
    client.release();
  }
});

// ── GET /api/postmortem/failure-tags ─────────────────────────────────────────
router.get("/failure-tags", async (_req: Request, res: Response) => {
  const client = await pool.connect();
  try {
    const { rows } = await client.query(`
      SELECT
        dominant_failure_tag AS tag,
        COUNT(*)             AS count,
        ARRAY_AGG(DISTINCT sport ORDER BY sport) AS sports
      FROM wow_postmortems
      WHERE dominant_failure_tag IS NOT NULL AND settled_at IS NOT NULL
      GROUP BY dominant_failure_tag
      ORDER BY count DESC
      LIMIT 30
    `);
    return res.json({ ok: true, failure_tags: rows });
  } catch (err) {
    return res.status(503).json({ ok: false, error: String(err) });
  } finally {
    client.release();
  }
});

export default router;
