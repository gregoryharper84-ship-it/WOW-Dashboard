// Local fixture endpoint for the isolated TestDriver VM. Never connects to live services.
import { createServer } from "node:http";

const HOST = "127.0.0.1";
const PORT = 4184;
const PLAYER = "Test Fixture Player";
const fixture = Object.freeze({
  player: PLAYER,
  team: "TEST",
  opponent: "MOCK",
  sport: "NBA",
  game_date: "2026-11-22",
  prop_type: "Points",
  side: "MORE",
  line: 25.5,
  platform: "LOCAL_TEST_FIXTURE",
  payout_context: null,
  source_status: "UNAVAILABLE",
  source_grade: "UNVERIFIED",
  source: "TEST_FIXTURE_ONLY",
  timestamp: "2026-11-22T00:00:00Z",
});

function json(res, status, data) {
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
  });
  res.end(JSON.stringify(data));
}

const server = createServer((req, res) => {
  if (req.method === "GET" && req.url === "/health") {
    return json(res, 200, { status: "ok", fixture_only: true });
  }
  if (req.method !== "POST" || req.url !== "/api/props/normalize") {
    return json(res, 404, { ok: false, error: "Test fixture route not found" });
  }

  let body = "";
  req.on("data", chunk => {
    body += chunk;
    if (body.length > 4096) req.destroy();
  });
  req.on("end", () => {
    let parsed;
    try { parsed = JSON.parse(body); }
    catch { return json(res, 400, { ok: false, error: "Invalid fixture JSON" }); }

    const p = parsed?.props?.[0];
    if (parsed?.sport !== "NBA" || !Array.isArray(parsed.props) ||
        parsed.props.length !== 1 || p?.player !== PLAYER ||
        p?.prop_type !== "Points" || p?.side !== "MORE" || p?.line !== 25.5) {
      return json(res, 400, { ok: false, error: "Unexpected fixture payload" });
    }
    return json(res, 200, {
      ok: true,
      props: [fixture],
      errors: [],
      provider: "local_test_fixture",
      raw_count: 1,
      note: "Local deterministic fixture; not a live model prediction",
      source_status: "UNAVAILABLE",
    });
  });
});

server.listen(PORT, HOST, () => {
  process.stdout.write(`WOW fixture server listening on http://${HOST}:${PORT}\n`);
});
