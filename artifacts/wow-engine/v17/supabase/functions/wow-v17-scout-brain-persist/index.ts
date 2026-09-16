import { createRemoteJWKSet, jwtVerify } from "npm:jose@5.9.6";
import postgres from "npm:postgres@3.4.5";

const ISSUER = "https://token.actions.githubusercontent.com";
const JWKS = createRemoteJWKSet(new URL(`${ISSUER}/.well-known/jwks`));
const AUDIENCE = "wow-v17-multiscout";
const REPOSITORY = "gregoryharper84-ship-it/WOW-Dashboard";
const REPOSITORY_ID = "1240256887";
const REPOSITORY_OWNER_ID = "285088163";
const REF = "refs/heads/main";
const WORKFLOW_REF = `${REPOSITORY}/.github/workflows/wow-v17-scout-brain-persist.yml@${REF}`;
const ALLOWED_EVENTS = new Set(["workflow_run", "workflow_dispatch"]);
const ALLOWED_STATUSES = new Set([
  "WATCH", "RESEARCH_INTEREST_LOW", "RESEARCH_INTEREST_MEDIUM",
  "RESEARCH_INTEREST_HIGH", "QUARANTINED", "NO_INTEREST",
]);
const ALLOWED_PHASES = new Set(["BEGIN", "APPEND", "FINALIZE"]);

type Row = Record<string, unknown>;

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

async function authorize(req: Request): Promise<void> {
  const auth = req.headers.get("authorization") || "";
  if (!auth.startsWith("Bearer ")) throw new Error("GITHUB_OIDC_TOKEN_MISSING");
  const token = auth.slice("Bearer ".length).trim();
  const { payload } = await jwtVerify(token, JWKS, {
    issuer: ISSUER,
    audience: AUDIENCE,
    algorithms: ["RS256"],
  });
  const exact: Record<string, string> = {
    repository: REPOSITORY,
    repository_id: REPOSITORY_ID,
    repository_owner_id: REPOSITORY_OWNER_ID,
    ref: REF,
    workflow_ref: WORKFLOW_REF,
    runner_environment: "github-hosted",
  };
  for (const [field, expected] of Object.entries(exact)) {
    if (String(payload[field] || "") !== expected) throw new Error(`GITHUB_OIDC_${field.toUpperCase()}_MISMATCH`);
  }
  if (!ALLOWED_EVENTS.has(String(payload.event_name || ""))) throw new Error("GITHUB_OIDC_EVENT_NOT_ALLOWED");
}

async function sha256(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

function asObj(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}

function asList(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function evidenceList(row: Row): Row[] {
  if (Array.isArray(row.market_evidence)) return row.market_evidence.map(asObj).filter((x) => Object.keys(x).length > 0);
  const one = asObj(row.market_evidence);
  return Object.keys(one).length ? [one] : [];
}

function str(value: unknown): string {
  return value == null ? "" : String(value);
}

function nullableStr(value: unknown): string | null {
  const v = str(value);
  return v ? v : null;
}

function normalizeStatus(row: Row): string {
  const status = str(row.research_status || "WATCH");
  return ALLOWED_STATUSES.has(status) ? status : "WATCH";
}

function stageFor(row: Row): string {
  const cycle = asList(row.research_cycle);
  return cycle.length ? str(cycle[cycle.length - 1]) : "DISCOVERY";
}

function priorityScore(row: Row): number {
  const evidenceCount = evidenceList(row).length;
  const edgeCount = asList(row.edge_classes).length;
  const redCount = asList(row.required_red_team_checks).length;
  return Math.min(100, 35 + Math.min(evidenceCount, 20) * 2 + Math.min(edgeCount, 10) * 2 + Math.min(redCount, 10) * 0.5);
}

async function stableCandidateId(row: Row): Promise<string> {
  const evidence = evidenceList(row)[0] || {};
  const raw = [
    row.sport_key, row.official_event_id, row.route,
    evidence.market_key, evidence.description, evidence.outcome_name, evidence.point,
  ].map(str).join("|");
  return `scout_${(await sha256(raw)).slice(0, 24)}`;
}

async function stableSourceSnapshot(row: Row, evidence: Row): Promise<{id: string; payload: string; hash: string; provider: string}> {
  const payload = JSON.stringify(evidence, Object.keys(evidence).sort());
  const hash = await sha256(payload);
  const provider = str(evidence.source_provider || evidence.bookmaker || evidence.bookmaker_title || "UNKNOWN");
  const raw = [provider, str(row.sport_key || "UNKNOWN"), "MARKET_EVIDENCE", hash].join("|");
  return { id: `source_${(await sha256(raw)).slice(0, 24)}`, payload, hash, provider };
}

function scoutTeam(row: Row): string {
  const raw = row.scout_team_id || row.sport_scout_team || "GENERIC_SCOUT_TEAM";
  if (raw && typeof raw === "object" && !Array.isArray(raw)) return str(asObj(raw).team_id || "GENERIC_SCOUT_TEAM");
  return str(raw);
}

/**
 * Candidate rows carried by a request, in either shape.
 *
 * A single-shot request carries them under `model_handoff`. A chunked APPEND
 * request carries one batch under `candidates`, so the whole slate never has
 * to be parsed into one worker's memory at once.
 */
function candidateRows(body: Row): Row[] {
  if (Array.isArray(body.candidates)) return body.candidates.map(asObj);
  const model = asObj(body.model_handoff);
  return [...asList(model.team_event_candidates), ...asList(model.prop_candidates)].map(asObj);
}

/**
 * Governance re-validation. Applies to every phase, not just the single-shot
 * path: a chunked upload must not be a way to smuggle a row past the gate.
 */
function governanceValid(body: Row): boolean {
  if (body.can_execute !== false) return false;
  for (const row of candidateRows(body)) {
    if (row.can_execute !== false) return false;
    if (row.probability != null || row.model_probability != null || row.calibrated_probability != null || row.calibrated_lower_bound != null) return false;
  }
  return true;
}

function runSnapshot(body: Row, sports: Record<string, number>): string {
  return JSON.stringify({
    source_status: body.status ?? null,
    sports,
    source_blockers: asList(body.source_blockers),
    source_revision: asObj(body.delivery).source_revision ?? null,
    prediction_authority: false,
    can_execute: false,
  });
}

type BatchCounts = {
  changed: number;
  quarantined: number;
  observations: number;
  sourceSnapshots: number;
  candidateSourceLinks: number;
};

// deno-lint-ignore no-explicit-any
async function persistCandidates(tx: any, runId: string, candidates: Row[]): Promise<BatchCounts> {
  const counts: BatchCounts = { changed: 0, quarantined: 0, observations: 0, sourceSnapshots: 0, candidateSourceLinks: 0 };

  for (const row of candidates) {
    const cid = await stableCandidateId(row);
    const sport = str(row.sport_key || "UNKNOWN");
    const team = scoutTeam(row);
    const route = str(row.controlling_specialist_route || row.controlling_specialist || row.route || "UNRESOLVED");
    const status = normalizeStatus(row);
    counts.quarantined += status === "QUARANTINED" ? 1 : 0;
    const score = priorityScore(row);
    const evidences = evidenceList(row);
    const evidenceObj = evidences[0] || {};
    const marketType = str(evidenceObj.market_key || (row.route === "LLP_TEAM_BETTING_ENGINE" ? "TEAM_EVENT" : "PROP"));
    const selection = nullableStr(evidenceObj.outcome_name || evidenceObj.description);
    const edgeClasses = asList(row.edge_classes).map(str);
    const redFlags = asList(row.red_team_flags).map(str);
    const contradictory = asList(row.contradictory_evidence).map(str);
    const thesis = nullableStr(row.research_thesis || row.thesis);

    const insertedRows = await tx`
      insert into wow_scout.candidates
        (candidate_id,sport_key,scout_team,market_type,selection,event_id,canonical_event_id,commence_time,home_team,away_team,
         controlling_specialist,research_status,research_priority_score,thesis,edge_classes,contradictory_evidence,red_team_flags,
         data_completeness,source_freshness_score,probability,can_execute,updated_at)
      values (${cid},${sport},${team},${marketType},${selection},${nullableStr(row.official_event_id)},${nullableStr(row.canonical_event_id)},${nullableStr(row.commence_time)},${nullableStr(row.home_team)},${nullableStr(row.away_team)},
         ${route},${status},${score},${thesis},${edgeClasses}::text[],${contradictory}::text[],${redFlags}::text[],
         ${row.data_completeness == null ? null : Number(row.data_completeness)},${row.source_freshness_score == null ? null : Number(row.source_freshness_score)},null,false,now())
      on conflict (candidate_id) do update set
         scout_team=excluded.scout_team, selection=excluded.selection, commence_time=excluded.commence_time,
         home_team=excluded.home_team, away_team=excluded.away_team, controlling_specialist=excluded.controlling_specialist,
         research_status=excluded.research_status, research_priority_score=excluded.research_priority_score,
         thesis=coalesce(excluded.thesis,wow_scout.candidates.thesis), edge_classes=excluded.edge_classes,
         contradictory_evidence=excluded.contradictory_evidence, red_team_flags=excluded.red_team_flags,
         data_completeness=excluded.data_completeness, source_freshness_score=excluded.source_freshness_score,
         probability=null, can_execute=false, updated_at=now()
      returning (xmax = 0) as inserted
    `;
    counts.changed += insertedRows[0]?.inserted ? 1 : 0;

    for (const e of evidences) {
      const text = JSON.stringify(e, Object.keys(e).sort());
      const checksum = await sha256(text);
      const sourceName = str(e.source_provider || e.bookmaker || "UNKNOWN");
      await tx`
        insert into wow_scout.observations
          (candidate_id,agent_id,stage,source_type,source_name,evidence_type,evidence_text,evidence_value,is_contradictory,is_stale,checksum)
        values (${cid},'MARKET_SCOUT',${stageFor(row)},'SPORTSBOOK',${sourceName},'MARKET_EVIDENCE',${text},${JSON.stringify(e)}::jsonb,false,false,${checksum})
        on conflict (candidate_id,agent_id,checksum) do nothing
      `;
      counts.observations += 1;

      const snapshot = await stableSourceSnapshot(row, e);
      const observedAt = nullableStr(e.market_last_update || e.bookmaker_last_update || e.captured_at) || new Date().toISOString();
      const sourceClass = str(e.source_class || e.source_tier || "SPORTSBOOK_FEED");
      const sourceStatus = str(row.market_evidence_status || "AVAILABLE");
      const sourceCode = nullableStr(e.source_code || e.primary_source_failure);
      const sourceHttpStatus = e.source_http_status == null ? null : Number(e.source_http_status);
      await tx`
        insert into wow_scout.source_snapshots
          (snapshot_id,provider,sport_key,capability,source_class,observed_at,source_status,source_code,source_http_status,payload,payload_hash,prediction_authority,can_execute)
        values (${snapshot.id},${snapshot.provider},${sport},'MARKET_EVIDENCE',${sourceClass},${observedAt},${sourceStatus},${sourceCode},${sourceHttpStatus},${snapshot.payload}::jsonb,${snapshot.hash},false,false)
        on conflict (snapshot_id) do update set
          observed_at=excluded.observed_at, source_status=excluded.source_status,
          source_code=excluded.source_code, source_http_status=excluded.source_http_status,
          payload=excluded.payload, payload_hash=excluded.payload_hash,
          prediction_authority=false, can_execute=false
      `;
      counts.sourceSnapshots += 1;

      const providerEntities = JSON.stringify({
        official_event_id: row.official_event_id ?? null,
        canonical_event_id: row.canonical_event_id ?? null,
        home_team: row.home_team ?? null,
        away_team: row.away_team ?? null,
        bookmaker: e.bookmaker ?? null,
        market_key: e.market_key ?? null,
      });
      await tx`
        insert into wow_scout.candidate_source_links
          (candidate_id,snapshot_id,link_status,link_reason,provider_entities,prediction_authority,can_execute)
        values (${cid},${snapshot.id},'LINKED','DIRECT_CANDIDATE_MARKET_EVIDENCE',${providerEntities}::jsonb,false,false)
        on conflict (candidate_id,snapshot_id) do update set
          link_status='LINKED', link_reason='DIRECT_CANDIDATE_MARKET_EVIDENCE', provider_entities=excluded.provider_entities,
          prediction_authority=false, can_execute=false, linked_at=now()
      `;
      counts.candidateSourceLinks += 1;
    }

    await tx`
      insert into wow_scout.candidate_history
        (candidate_id,research_run_id,research_status,research_priority_score,thesis,edge_classes,contradictory_evidence,red_team_flags,
         data_completeness,source_freshness_score,probability,snapshot)
      values (${cid},${runId},${status},${score},${thesis},${edgeClasses}::text[],${contradictory}::text[],${redFlags}::text[],
         ${row.data_completeness == null ? null : Number(row.data_completeness)},${row.source_freshness_score == null ? null : Number(row.source_freshness_score)},null,${JSON.stringify(row)}::jsonb)
    `;
  }

  return counts;
}

// deno-lint-ignore no-explicit-any
async function openRun(tx: any, runId: string, body: Row): Promise<void> {
  const firstBlocker = asObj(asList(body.source_blockers)[0]);
  await tx`
    insert into wow_scout.research_runs
      (research_run_id,sport_key,scout_team,stage,started_at,completed_at,status,source_snapshot,candidate_count,changed_candidate_count,quarantined_count,error_code,can_execute)
    values (${runId},'MULTI','WOW_CHIEF_SCOUT','MULTISPORT_REFRESH',now(),null,'PERSISTING',${runSnapshot(body, {})}::jsonb,0,0,0,${nullableStr(firstBlocker.reason_code)},false)
    on conflict (research_run_id) do update set
      status='PERSISTING', completed_at=null, started_at=now(), can_execute=false,
      source_snapshot=excluded.source_snapshot,
      candidate_count=0, changed_candidate_count=0, quarantined_count=0, error_code=excluded.error_code
  `;
}

/** Running tallies live on the row itself so no phase has to hold the whole slate. */
// deno-lint-ignore no-explicit-any
async function accumulate(tx: any, runId: string, batchSize: number, counts: BatchCounts): Promise<void> {
  await tx`
    update wow_scout.research_runs set
      candidate_count = candidate_count + ${batchSize},
      changed_candidate_count = changed_candidate_count + ${counts.changed},
      quarantined_count = quarantined_count + ${counts.quarantined},
      source_snapshot = coalesce(source_snapshot,'{}'::jsonb) || jsonb_build_object(
        'observations_seen', coalesce((source_snapshot->>'observations_seen')::int,0) + ${counts.observations},
        'source_snapshots_seen', coalesce((source_snapshot->>'source_snapshots_seen')::int,0) + ${counts.sourceSnapshots},
        'candidate_source_links_seen', coalesce((source_snapshot->>'candidate_source_links_seen')::int,0) + ${counts.candidateSourceLinks}
      ),
      can_execute = false
    where research_run_id = ${runId}
  `;
}

// deno-lint-ignore no-explicit-any
async function closeRun(tx: any, runId: string, body: Row): Promise<Row> {
  const sportRows = await tx`
    select c.sport_key as sport_key, count(*)::int as n
    from wow_scout.candidate_history h
    join wow_scout.candidates c on c.candidate_id = h.candidate_id
    where h.research_run_id = ${runId}
    group by c.sport_key
  `;
  const sports: Record<string, number> = {};
  for (const r of sportRows) sports[str(r.sport_key)] = Number(r.n);

  const updated = await tx`
    update wow_scout.research_runs set
      completed_at=now(), status='COMPLETE', can_execute=false,
      source_snapshot = coalesce(source_snapshot,'{}'::jsonb) || ${runSnapshot(body, sports)}::jsonb
        || jsonb_build_object(
          'observations_seen', coalesce((source_snapshot->>'observations_seen')::int,0),
          'source_snapshots_seen', coalesce((source_snapshot->>'source_snapshots_seen')::int,0),
          'candidate_source_links_seen', coalesce((source_snapshot->>'candidate_source_links_seen')::int,0)
        )
    where research_run_id=${runId}
    returning candidate_count, changed_candidate_count, quarantined_count, source_snapshot
  `;
  const rowOut = asObj(updated[0]);
  const snapshot = asObj(rowOut.source_snapshot);
  return {
    candidate_count: Number(rowOut.candidate_count ?? 0),
    changed_candidate_count: Number(rowOut.changed_candidate_count ?? 0),
    quarantined_count: Number(rowOut.quarantined_count ?? 0),
    observation_count: Number(snapshot.observations_seen ?? 0),
    source_snapshot_count: Number(snapshot.source_snapshots_seen ?? 0),
    candidate_source_link_count: Number(snapshot.candidate_source_links_seen ?? 0),
    sport_counts: sports,
  };
}

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") return response({ ok: false, code: "METHOD_NOT_ALLOWED", can_execute: false }, 405);
  try {
    await authorize(req);
  } catch (err) {
    return response({ ok: false, code: err instanceof Error ? err.message : "GITHUB_OIDC_INVALID", can_execute: false }, 401);
  }

  let body: Row;
  try {
    body = asObj(await req.json());
  } catch {
    return response({ ok: false, code: "HANDOFF_JSON_INVALID", can_execute: false }, 400);
  }

  const phase = str(body.persist_phase || "").toUpperCase();
  if (phase && !ALLOWED_PHASES.has(phase)) {
    return response({ ok: false, code: "PERSIST_PHASE_INVALID", can_execute: false }, 422);
  }
  if (!governanceValid(body)) return response({ ok: false, code: "HANDOFF_GOVERNANCE_INVALID", can_execute: false }, 422);

  const dbUrl = Deno.env.get("SUPABASE_DB_URL");
  if (!dbUrl) return response({ ok: false, code: "SUPABASE_DB_URL_UNAVAILABLE", can_execute: false }, 500);

  const runId = str(body.research_run_id || body.run_id);
  if (!runId) return response({ ok: false, code: "RESEARCH_RUN_ID_MISSING", can_execute: false }, 422);

  const sql = postgres(dbUrl, { max: 1, prepare: false });
  try {
    if (phase === "BEGIN") {
      await sql.begin(async (tx) => {
        await openRun(tx, runId, body);
      });
      return response({ ok: true, persist_phase: "BEGIN", research_run_id: runId, can_execute: false });
    }

    if (phase === "APPEND") {
      const candidates = candidateRows(body);
      let counts: BatchCounts = { changed: 0, quarantined: 0, observations: 0, sourceSnapshots: 0, candidateSourceLinks: 0 };
      await sql.begin(async (tx) => {
        counts = await persistCandidates(tx, runId, candidates);
        await accumulate(tx, runId, candidates.length, counts);
      });
      return response({
        ok: true,
        persist_phase: "APPEND",
        research_run_id: runId,
        batch_candidate_count: candidates.length,
        batch_changed_candidate_count: counts.changed,
        batch_quarantined_count: counts.quarantined,
        batch_observation_count: counts.observations,
        can_execute: false,
      });
    }

    if (phase === "FINALIZE") {
      let summary: Row = {};
      await sql.begin(async (tx) => {
        summary = await closeRun(tx, runId, body);
      });
      return response({
        ok: true,
        persist_phase: "FINALIZE",
        research_run_id: runId,
        ...summary,
        source_status: body.status ?? null,
        can_execute: false,
      });
    }

    // No phase: the original single-request path, preserved for small slates
    // and for any caller that has not been updated.
    const candidates = candidateRows(body);
    let counts: BatchCounts = { changed: 0, quarantined: 0, observations: 0, sourceSnapshots: 0, candidateSourceLinks: 0 };
    let summary: Row = {};
    await sql.begin(async (tx) => {
      await openRun(tx, runId, body);
      counts = await persistCandidates(tx, runId, candidates);
      await accumulate(tx, runId, candidates.length, counts);
      summary = await closeRun(tx, runId, body);
    });
    return response({
      ok: true,
      research_run_id: runId,
      ...summary,
      source_status: body.status ?? null,
      can_execute: false,
    });
  } catch (err) {
    return response({ ok: false, code: "SCOUT_BRAIN_PERSIST_FAILED", error_type: err instanceof Error ? err.name : "Error", can_execute: false }, 500);
  } finally {
    await sql.end({ timeout: 2 });
  }
});
