interface LegacyProp {
  player?: string;
  team?: string;
  opponent?: string;
  sport?: string;
  prop_type?: string;
  side?: string;
  line?: number;
  platform?: string;
  payout_context?: string | null;
  source?: string;
  source_status?: string;
  team_confidence?: string;
  status_confirmed?: boolean;
  line_verified?: boolean;
}

// Legacy V16 compatibility only. Never convert this response into a V17
// probability or immutable receipt, and never infer confirmations from grade.
export async function scoreLegacyProp(prop: LegacyProp, options: { fetchImpl?: typeof fetch; backendUrl?: string; apiKey?: string } = {}) {
  const base = (options.backendUrl ?? process.env.SCORING_API_URL ?? "http://localhost:25643").replace(/\/$/, "");
  const apiKey = options.apiKey ?? process.env.SCORING_API_KEY ?? "";
  const metadata = { can_execute: false, execution_rule: "READ_ONLY_NO_EXECUTION", run_source: "score_batch", generation: "LEGACY_V16" };
  try {
    const response = await (options.fetchImpl ?? fetch)(`${base}/final-lock`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
      body: JSON.stringify({
        player: prop.player, team: prop.team, opponent: prop.opponent, sport: prop.sport,
        prop: prop.prop_type, side: prop.side, line: prop.line, pp_line: prop.line,
        platform: prop.platform, payout_context: prop.payout_context ?? "2-pick Power",
        source: prop.source, source_status: prop.source_status,
        status_confirmed: prop.status_confirmed === true,
        line_verified: prop.line_verified === true,
      }),
      signal: AbortSignal.timeout(15_000),
      redirect: "error",
    });
    const raw: unknown = await response.json();
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("invalid envelope");
    const payload = raw as Record<string, unknown>;
    if (payload.can_execute === true) {
      return { prop, ok: false, result: { code: "LEGACY_UPSTREAM_GOVERNANCE_VIOLATION", error: "The legacy backend attempted to grant execution authority; its result was withheld.", ...metadata } as Record<string, unknown> };
    }
    const ok = response.ok && payload.ok === true;
    return {
      prop,
      ok,
      result: { ...payload, upstream_status: response.status, team_confidence: prop.team_confidence ?? "UNKNOWN", ...metadata } as Record<string, unknown>,
    };
  } catch {
    return { prop, ok: false, result: { code: "LEGACY_SCORING_TRANSPORT_FAILURE", error: "The legacy scoring service did not return a valid JSON result.", ...metadata } as Record<string, unknown> };
  }
}
