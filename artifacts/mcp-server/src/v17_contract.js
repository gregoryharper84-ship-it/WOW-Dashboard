export const CAN_EXECUTE = false;
export const TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER";
export const BACKEND_DEFAULT = "https://wow-governed-probability-engine.onrender.com";

const objectOutputSchema = {
  type: "object",
  additionalProperties: true,
};

const directionSchema = { type: "string", enum: ["MORE", "LESS"] };

const pickRowSchema = {
  type: "object",
  additionalProperties: false,
  required: ["event_id", "event_start_time", "sport", "player", "stat_type", "line", "direction"],
  properties: {
    row_key: { type: ["string", "null"] },
    event_id: { type: "string" },
    event_start_time: { type: "string", format: "date-time" },
    sport: { type: "string" },
    player: { type: "string" },
    stat_type: { type: "string" },
    line: { type: "number" },
    direction: directionSchema,
    evidence: { type: ["object", "null"], additionalProperties: true },
    source_type: {
      type: "string",
      enum: ["SCREENSHOT", "PDF", "AUTONOMOUS_DISCOVERY", "PASTED_BOARD", "NORMALIZED"],
      default: "NORMALIZED",
    },
    platform: { type: ["string", "null"] },
    league: { type: ["string", "null"] },
    opponent: { type: ["string", "null"] },
    source_capture_timestamp: { type: ["string", "null"], format: "date-time" },
    seed: { type: "integer", default: 0 },
    money_lane_status: { type: "string", default: "PAYOUT_UNRESOLVED" },
    market_side_a: { type: ["object", "null"], additionalProperties: true },
    market_side_b: { type: ["object", "null"], additionalProperties: true },
  },
};

const receiptLookupRowSchema = {
  type: "object",
  additionalProperties: false,
  anyOf: [
    { required: ["prediction_id"] },
    { required: ["event_id", "player", "stat_type", "line", "direction"] },
  ],
  properties: {
    row_key: { type: ["string", "null"] },
    prediction_id: { type: ["string", "null"] },
    event_id: { type: ["string", "null"] },
    sport: { type: ["string", "null"] },
    player: { type: ["string", "null"] },
    team: { type: ["string", "null"] },
    opponent: { type: ["string", "null"] },
    stat_type: { type: ["string", "null"] },
    line: { type: ["number", "null"] },
    direction: { anyOf: [directionSchema, { type: "null" }] },
    event_start_min: { type: ["string", "null"], format: "date-time" },
    event_start_max: { type: ["string", "null"], format: "date-time" },
  },
};

function tool({ name, title, description, inputSchema, readOnly, idempotent = false }) {
  return {
    name,
    title,
    description,
    inputSchema,
    outputSchema: objectOutputSchema,
    annotations: {
      readOnlyHint: readOnly,
      destructiveHint: false,
      idempotentHint: idempotent,
      openWorldHint: true,
    },
  };
}

export const V17_TOOLS = [
  tool({
    name: "getWowV17BackendHealth",
    title: "WOW V17 Backend Health",
    description: "Read production V17 runtime health. This never creates or substitutes a sporting probability.",
    readOnly: true,
    idempotent: true,
    inputSchema: { type: "object", additionalProperties: false, properties: {} },
  }),
  tool({
    name: "getWowV17Governance",
    title: "WOW V17 Governance",
    description: "Read active V17 governance/version state, including terminal authority and execution safety.",
    readOnly: true,
    idempotent: true,
    inputSchema: { type: "object", additionalProperties: false, properties: {} },
  }),
  tool({
    name: "getWowV17HostContract",
    title: "WOW V17 Host Contract",
    description: "Read the canonical V17 host/lane routing contract.",
    readOnly: true,
    idempotent: true,
    inputSchema: { type: "object", additionalProperties: false, properties: {} },
  }),
  tool({
    name: "getWowV17DetailedEvidenceContract",
    title: "WOW V17 Detailed Evidence Contract",
    description: "Read the evidence boundary. Market evidence and evidence-only fields never become sporting-model authority.",
    readOnly: true,
    idempotent: true,
    inputSchema: { type: "object", additionalProperties: false, properties: {} },
  }),
  tool({
    name: "getWowV17Capabilities",
    title: "WOW V17 Capabilities",
    description: "Read the specialist/certification capability matrix. This does not fabricate missing model capability.",
    readOnly: true,
    idempotent: true,
    inputSchema: { type: "object", additionalProperties: false, properties: {} },
  }),
  tool({
    name: "getWowV17RundownMarketHealth",
    title: "WOW V17 TheRundown Health",
    description: "Read TheRundown market-provider health. Market health never substitutes for fitted sporting probability.",
    readOnly: true,
    idempotent: true,
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: {
        sport_key: { type: "string", default: "baseball_mlb" },
        date: { type: "string" },
      },
    },
  }),
  tool({
    name: "getWowV17OddsApiMarketHealth",
    title: "WOW V17 Odds API Health",
    description: "Read fallback market-provider health without exposing credentials or creating sporting probabilities.",
    readOnly: true,
    idempotent: true,
    inputSchema: { type: "object", additionalProperties: false, properties: {} },
  }),
  tool({
    name: "getWowV17CompactEspnDiscovery",
    title: "WOW V17 ESPN Discovery",
    description: "Read bounded identity-only ESPN discovery. Provider identities carry no prediction authority.",
    readOnly: true,
    idempotent: true,
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: {
        sport_key: { type: "string", default: "baseball_mlb" },
        date: { type: "string" },
        page: { type: "integer", minimum: 1, default: 1 },
        page_size: { type: "integer", minimum: 1, maximum: 250, default: 100 },
      },
    },
  }),
  tool({
    name: "scoreWowPickRequest",
    title: "WOW V17 Score Pick Request",
    description: "Canonical player/scalar prop batch bridge. Preserve stable request_id and row_key across recovery. Never use market probability or narrative reasoning as the governed model probability.",
    readOnly: false,
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["rows"],
      properties: {
        request_id: { type: ["string", "null"] },
        response_mode: { type: "string", enum: ["COMPACT", "FULL"], default: "COMPACT" },
        rows: { type: "array", minItems: 1, maxItems: 50, items: pickRowSchema },
      },
    },
  }),
  tool({
    name: "scoreWowProp",
    title: "WOW V17 Score One Prop",
    description: "Governed one-off player/scalar prop route. Do not use for team/event winners.",
    readOnly: false,
    inputSchema: {
      type: "object",
      additionalProperties: true,
      required: ["event_id", "event_start_time", "sport", "stat_type", "line", "direction", "source_snapshot_id"],
      properties: {
        requester_host_identity: { type: "string", enum: ["WOW_BETTING_ENGINE"], default: "WOW_BETTING_ENGINE" },
        event_id: { type: "string" },
        event_start_time: { type: "string", format: "date-time" },
        sport: { type: "string" },
        league: { type: ["string", "null"] },
        player: { type: ["string", "null"] },
        stat_type: { type: "string" },
        line: { type: "number" },
        direction: directionSchema,
        source_snapshot_id: { type: "string" },
        seed: { type: "integer", default: 0 },
        evidence: { type: ["object", "null"], additionalProperties: true },
      },
    },
  }),
  tool({
    name: "runWowV17DailySnapshot",
    title: "WOW V17 Daily Snapshot",
    description: "Run the canonical bounded Daily orchestration on the governed backend. COMPACT is preferred; retrieve row detail separately.",
    readOnly: false,
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["requested_slate_date", "requested_timezone"],
      properties: {
        requested_slate_date: { type: "string", format: "date" },
        requested_timezone: { type: "string" },
        lanes: { type: "array", items: { type: "string", enum: ["PROPS", "MONEYLINE"] }, default: ["PROPS", "MONEYLINE"] },
        max_props: { type: "integer", minimum: 0, maximum: 12, default: 6 },
        max_team_events: { type: "integer", minimum: 0, maximum: 12, default: 6 },
        response_mode: { type: "string", enum: ["COMPACT", "FULL"], default: "COMPACT" },
      },
    },
  }),
  tool({
    name: "readWowV17DailySnapshotRowDetail",
    title: "WOW V17 Daily Row Detail",
    description: "Read one bounded page of full per-row evidence for a completed Daily run.",
    readOnly: true,
    idempotent: true,
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["run_id"],
      properties: {
        run_id: { type: "string" },
        offset: { type: "integer", minimum: 0, default: 0 },
        limit: { type: "integer", minimum: 1, maximum: 25, default: 5 },
      },
    },
  }),
  tool({
    name: "scoreWowV17TeamEventFromWowHost",
    title: "WOW V17 Team Event",
    description: "Governed team/event ingress. Backend resolves LLP_TEAM_BETTING_ENGINE and the registered sport specialist; unsupported lanes fail closed.",
    readOnly: false,
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: [
        "requester_host_identity", "research_run_id", "requested_slate_date", "requested_timezone",
        "scan_stage", "candidate_family", "decision_intent", "event_key", "official_event_id",
        "event_start_time_utc", "sport", "league", "market_family", "settlement_basis",
        "home_team", "away_team", "source_snapshot_id"
      ],
      properties: {
        requester_host_identity: { type: "string" },
        research_run_id: { type: "string" },
        requested_slate_date: { type: "string", format: "date" },
        requested_timezone: { type: "string" },
        scan_stage: { type: "string", enum: ["PREGAME"] },
        candidate_family: { type: "string", enum: ["TEAM_EVENT", "OUTRIGHT_WINNER", "MONEYLINE", "FAVORITE", "UNDERDOG", "UPSET", "MATCH_WINNER", "FIGHT_WINNER"] },
        decision_intent: { type: "string", enum: ["WINNER", "FAVORITE", "UNDERDOG", "UPSET", "BEST_SIDE"] },
        event_key: { type: "string" },
        official_event_id: { type: "string" },
        event_start_time_utc: { type: "string", format: "date-time" },
        sport: { type: "string" },
        league: { type: "string" },
        market_family: { type: "string", enum: ["OUTRIGHT_WINNER"] },
        settlement_basis: { type: "string" },
        home_team: { type: "string" },
        away_team: { type: "string" },
        source_snapshot_id: { type: "string" },
        latest_material_update_timestamp: { type: ["string", "null"], format: "date-time" },
        market_prior: { type: ["object", "null"], additionalProperties: true },
        sport_specific_evidence: { type: "object", additionalProperties: true },
      },
    },
  }),
  tool({
    name: "lookupWowV17PredictionReceipts",
    title: "WOW V17 Prediction Receipt Lookup",
    description: "Read immutable prediction receipts after timeout/disconnect ambiguity. Receipt lookup is the required recovery path before any retry.",
    readOnly: true,
    idempotent: true,
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["rows"],
      properties: {
        request_id: { type: ["string", "null"] },
        rows: { type: "array", minItems: 1, maxItems: 50, items: receiptLookupRowSchema },
      },
    },
  }),
  tool({
    name: "recordWowV17Recommendations",
    title: "WOW V17 Record Recommendations",
    description: "Persist governed recommendation records before display. This is stateful but never executes a wager or market order.",
    readOnly: false,
    inputSchema: {
      type: "object",
      additionalProperties: true,
      required: ["records"],
      properties: { records: { type: "array", items: { type: "object", additionalProperties: true } } },
    },
  }),
  tool({
    name: "settleWowV17Recommendations",
    title: "WOW V17 Settle Recommendations",
    description: "Record post-event settlement against linked recommendation records. This never places, modifies, or cancels a wager.",
    readOnly: false,
    inputSchema: {
      type: "object",
      additionalProperties: true,
      required: ["records"],
      properties: { records: { type: "array", items: { type: "object", additionalProperties: true } } },
    },
  }),
];

export const V17_OPERATIONS = Object.freeze({
  getWowV17BackendHealth: { method: "GET", path: "/health", scope: "wow.runtime.read" },
  getWowV17Governance: { method: "GET", path: "/governance", scope: "wow.governance.read" },
  getWowV17HostContract: { method: "GET", path: "/v17/host-contract", scope: "wow.governance.read" },
  getWowV17DetailedEvidenceContract: { method: "GET", path: "/v17/detailed-evidence-contract", scope: "wow.evidence.read" },
  getWowV17Capabilities: { method: "GET", path: "/v17/capabilities", scope: "wow.governance.read" },
  getWowV17RundownMarketHealth: { method: "GET", path: "/v17/market-health/rundown", scope: "wow.evidence.read", query: ["sport_key", "date"] },
  getWowV17OddsApiMarketHealth: { method: "GET", path: "/v17/market-health/odds-api", scope: "wow.evidence.read" },
  getWowV17CompactEspnDiscovery: { method: "GET", path: "/v17/discovery/espn-compact", scope: "wow.evidence.read", query: ["sport_key", "date", "page", "page_size"] },
  scoreWowPickRequest: { method: "POST", path: "/score-pick-request", scope: "wow.predictions.score", timeoutMs: 330000 },
  scoreWowProp: { method: "POST", path: "/score-prop", scope: "wow.predictions.score", timeoutMs: 180000 },
  runWowV17DailySnapshot: { method: "POST", path: "/v17/daily-snapshot-run", scope: "wow.daily.run", timeoutMs: 330000 },
  readWowV17DailySnapshotRowDetail: { method: "GET", path: "/v17/daily-snapshot-run/{run_id}/rows", scope: "wow.predictions.read", pathParams: ["run_id"], query: ["offset", "limit"] },
  scoreWowV17TeamEventFromWowHost: { method: "POST", path: "/score-team-event", scope: "wow.predictions.score", timeoutMs: 180000 },
  lookupWowV17PredictionReceipts: { method: "POST", path: "/v17/prediction-receipts/lookup", scope: "wow.predictions.read", timeoutMs: 30000 },
  recordWowV17Recommendations: { method: "POST", path: "/record-recommendations", scope: "wow.recommendations.write", timeoutMs: 30000 },
  settleWowV17Recommendations: { method: "POST", path: "/settle-recommendations", scope: "wow.settlements.write", timeoutMs: 30000 },
});

export const V17_TOOL_NAMES = Object.freeze(V17_TOOLS.map((entry) => entry.name));

export function assertV17Contract() {
  const tools = new Set(V17_TOOL_NAMES);
  const ops = new Set(Object.keys(V17_OPERATIONS));
  if (tools.size !== V17_TOOLS.length) throw new Error("DUPLICATE_MCP_TOOL_NAME");
  if (tools.size !== ops.size || [...tools].some((name) => !ops.has(name))) {
    throw new Error("MCP_OPERATION_MAP_MISMATCH");
  }
  if (CAN_EXECUTE !== false || TERMINAL_AUTHORITY !== "V17_TERMINAL_REDUCER") {
    throw new Error("V17_GOVERNANCE_INVARIANT_BROKEN");
  }
  const forbidden = /(place|submit|execute|route|approve|cancel).*(bet|wager|order)|(?:bet|wager|order).*(place|submit|execute|route|approve|cancel)/i;
  const unsafe = V17_TOOL_NAMES.filter((name) => forbidden.test(name));
  if (unsafe.length) throw new Error(`WAGER_EXECUTION_TOOL_FORBIDDEN:${unsafe.join(",")}`);
  return true;
}

assertV17Contract();
