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

router.use("/wow",           makeForwarder("wow"));
router.use("/kalshi",        makeForwarder("kalshi"));
router.use("/lock-api",      makeForwarder("lock-api"));
router.use("/gate-engine",   makeForwarder("gate-engine"));
router.use("/analyze-board", makeForwarder("analyze-board"));

export default router;
