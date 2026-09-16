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
const TEAM_EVENT_ROUTE = "LLP_TEAM_BETTING_ENGINE";

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
  const evidenceCount = evidenceTotal(row);
  const edgeCount = asList(row.edge_classes).length;
  const redCount = asList(row.required_red_team_checks).length;
  return Math.min(100, 35 + Math.min(evidenceCount, 20) * 2 + Math.min(edgeCount, 10) * 2 + Math.min(redCount, 10) * 0.5);
}

function isTeamEventRow(row: Row): boolean {
  return str(row.route || row.controlling_specialist_route || "") === TEAM_EVENT_ROUTE;
}

/**
 * Evidence rows carried by a request, and the candidate's true total.
 *
 * A sliced request holds only part of a candidate's evidence, so anything
 * candidate-level (priority, market type) must read the slice total rather
 * than the rows present in this request.
 */
function evidenceTotal(row: Row): number {
  const slice = asObj(row.evidence_slice);
  if (slice.total != null) return Number(slice.total);
  return evidenceList(row).length;
}

function isFinalSlice(row: Row): boolean {
  const slice = asObj(row.evidence_slice);
  return slice.final == null ? true : slice.final === true;
}

function isFirstSlice(row: Row): boolean {
  const slice = asObj(row.evidence_slice);
  return slice.index == null ? true : Number(slice.index) === 0;
}

async function stableCandidateId(row: Row): Promise<string> {
  if (isTeamEventRow(row)) {
    // Identity cannot depend on the first evidence row once evidence is
    // sliced across requests: each slice would key to a different candidate.
    // Keeping the original seven-field shape with the evidence fields empty
    // reproduces byte-for-byte the id the previous algorithm produced for an
    // evidence-free team/event row, so existing rows keep their identity
    // instead of being re-keyed. Only rows previously mis-keyed by their
    // first evidence row move, which is the defect being repaired.
    const raw = [row.sport_key, row.official_event_id, row.route, "", "", "", ""].map(str).join("|");
    return `scout_${(await sha256(raw)).slice(0, 24)}`;
  }
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

function candidateWithoutEvidence(row: Row): Row {
  const out: Row = {};
  for (const [k, v] of Object.entries(row)) {
    if (k !== "market_evidence" && k !== "evidence_slice") out[k] = v;
  }
  return out;
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
  candidatesFinalized: number;
  observations: number;
  sourceSnapshots: number;
  candidateSourceLinks: number;
};

/**
 * Persist one request's worth of candidate slices.
 *
 * Evidence is written set-wise: one statement each for observations, source
 * snapshots and candidate source links, over arrays unnested in the database,
 * instead of three separately awaited round trips per evidence row. The prior
 * shape issued thousands of sequential writes for a single large team/event
 * candidate.
 *
 * Every array is bound as text[] and cast per row inside the statement. With
 * prepare:false a `${...}::jsonb` parameter makes Postgres infer the parameter
 * as jsonb, and the driver then JSON-encodes the already-serialized string, so
 * the column receives a jsonb *string* rather than an object. Every existing
 * candidate_history.snapshot is a string for exactly that reason. Casting from
 * text parses the value instead.
 */
// deno-lint-ignore no-explicit-any
async function persistCandidates(tx: any, runId: string, candidates: Row[]): Promise<BatchCounts> {
  const counts: BatchCounts = {
    changed: 0, quarantined: 0, candidatesFinalized: 0,
    observations: 0, sourceSnapshots: 0, candidateSourceLinks: 0,
  };

  for (const row of candidates) {
    const cid = await stableCandidateId(row);
    const sport = str(row.sport_key || "UNKNOWN");
    const team = scoutTeam(row);
    const route = str(row.controlling_specialist_route || row.controlling_specialist || row.route || "UNRESOLVED");
    const status = normalizeStatus(row);
    const score = priorityScore(row);
    const evidences = evidenceList(row);
    const teamRow = isTeamEventRow(row);
    // A team/event row's market type and selection must not vary by slice, so
    // they come from the row's own identity rather than its first evidence.
    const marketType = teamRow ? "TEAM_EVENT" : str(asObj(evidences[0]).market_key || "PROP");
    const selection = teamRow ? null : nullableStr(asObj(evidences[0]).outcome_name || asObj(evidences[0]).description);
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
    // Counted once per candidate, on its first slice, not once per slice.
    if (isFirstSlice(row)) counts.changed += insertedRows[0]?.inserted ? 1 : 0;

    if (evidences.length) {
      const stage = stageFor(row);
      const sourceStatus = str(row.market_evidence_status || "AVAILABLE");
      const obsName: string[] = [], obsText: string[] = [], obsValue: string[] = [], obsChecksum: string[] = [];
      const snapId: string[] = [], snapProvider: string[] = [], snapClass: string[] = [], snapObserved: string[] = [];
      const snapCode: (string | null)[] = [], snapHttp: (number | null)[] = [], snapPayload: string[] = [], snapHash: string[] = [];
      const linkSnapId: string[] = [], linkEntities: string[] = [];

      for (const e of evidences) {
        const text = JSON.stringify(e, Object.keys(e).sort());
        obsName.push(str(e.source_provider || e.bookmaker || "UNKNOWN"));
        obsText.push(text);
        obsValue.push(JSON.stringify(e));
        obsChecksum.push(await sha256(text));

        const snapshot = await stableSourceSnapshot(row, e);
        snapId.push(snapshot.id);
        snapProvider.push(snapshot.provider);
        snapClass.push(str(e.source_class || e.source_tier || "SPORTSBOOK_FEED"));
        snapObserved.push(nullableStr(e.market_last_update || e.bookmaker_last_update || e.captured_at) || new Date().toISOString());
        snapCode.push(nullableStr(e.source_code || e.primary_source_failure));
        snapHttp.push(e.source_http_status == null ? null : Number(e.source_http_status));
        snapPayload.push(snapshot.payload);
        snapHash.push(snapshot.hash);

        linkSnapId.push(snapshot.id);
        linkEntities.push(JSON.stringify({
          official_event_id: row.official_event_id ?? null,
          canonical_event_id: row.canonical_event_id ?? null,
          home_team: row.home_team ?? null,
          away_team: row.away_team ?? null,
          bookmaker: e.bookmaker ?? null,
          market_key: e.market_key ?? null,
        }));
      }

      await tx`
        insert into wow_scout.observations
          (candidate_id,agent_id,stage,source_type,source_name,evidence_type,evidence_text,evidence_value,is_contradictory,is_stale,checksum)
        select ${cid},'MARKET_SCOUT',${stage},'SPORTSBOOK',n,'MARKET_EVIDENCE',t,v::jsonb,false,false,c
        from unnest(${obsName}::text[],${obsText}::text[],${obsValue}::text[],${obsChecksum}::text[]) as e(n,t,v,c)
        on conflict (candidate_id,agent_id,checksum) do nothing
      `;
      counts.observations += evidences.length;

      await tx`
        insert into wow_scout.source_snapshots
          (snapshot_id,provider,sport_key,capability,source_class,observed_at,source_status,source_code,source_http_status,payload,payload_hash,prediction_authority,can_execute)
        select id,provider,${sport},'MARKET_EVIDENCE',cls,observed::timestamptz,${sourceStatus},code,http,payload::jsonb,hash,false,false
        from unnest(${snapId}::text[],${snapProvider}::text[],${snapClass}::text[],${snapObserved}::text[],${snapCode}::text[],${snapHttp}::int[],${snapPayload}::text[],${snapHash}::text[])
             as s(id,provider,cls,observed,code,http,payload,hash)
        on conflict (snapshot_id) do update set
          observed_at=excluded.observed_at, source_status=excluded.source_status,
          source_code=excluded.source_code, source_http_status=excluded.source_http_status,
          payload=excluded.payload, payload_hash=excluded.payload_hash,
          prediction_authority=false, can_execute=false
      `;
      counts.sourceSnapshots += evidences.length;

      await tx`
        insert into wow_scout.candidate_source_links
          (candidate_id,snapshot_id,link_status,link_reason,provider_entities,prediction_authority,can_execute)
        select ${cid},id,'LINKED','DIRECT_CANDIDATE_MARKET_EVIDENCE',ent::jsonb,false,false
        from unnest(${linkSnapId}::text[],${linkEntities}::text[]) as l(id,ent)
        on conflict (candidate_id,snapshot_id) do update set
          link_status='LINKED', link_reason='DIRECT_CANDIDATE_MARKET_EVIDENCE', provider_entities=excluded.provider_entities,
          prediction_authority=false, can_execute=false, linked_at=now()
      `;
      counts.candidateSourceLinks += evidences.length;
    }

    if (!isFinalSlice(row)) continue;

    counts.candidatesFinalized += 1;
    counts.quarantined += status === "QUARANTINED" ? 1 : 0;

    // The evidence array is already durable in observations, source snapshots
    // and links. Re-sending a multi-megabyte candidate into history would just
    // move the payload problem from HTTP into this insert, so history keeps
    // the research-level snapshot plus evidence counts.
    const historySnapshot = JSON.stringify({
      ...candidateWithoutEvidence(row),
      evidence_row_count: evidenceTotal(row),
      evidence_reference: "wow_scout.observations,wow_scout.source_snapshots,wow_scout.candidate_source_links",
      can_execute: false,
    });

    // Guarded rather than constraint-backed: production already holds 302
    // duplicate (candidate_id, research_run_id) groups, so adding the unique
    // index here would fail against existing data. The constraint belongs in a
    // separate hardening migration after those duplicates are cleaned.
    await tx`
      insert into wow_scout.candidate_history
        (candidate_id,research_run_id,research_status,research_priority_score,thesis,edge_classes,contradictory_evidence,red_team_flags,
         data_completeness,source_freshness_score,probability,snapshot)
      select ${cid},${runId},${status},${score},${thesis},${edgeClasses}::text[],${contradictory}::text[],${redFlags}::text[],
         ${row.data_completeness == null ? null : Number(row.data_completeness)},${row.source_freshness_score == null ? null : Number(row.source_freshness_score)},null,${historySnapshot}::text::jsonb
      where not exists (
        select 1 from wow_scout.candidate_history
        where candidate_id = ${cid} and research_run_id = ${runId}
      )
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
    values (${runId},'MULTI','WOW_CHIEF_SCOUT','MULTISPORT_REFRESH',now(),null,'PERSISTING',${runSnapshot(body, {})}::text::jsonb,0,0,0,${nullableStr(firstBlocker.reason_code)},false)
    on conflict (research_run_id) do update set
      status='PERSISTING', completed_at=null, started_at=now(), can_execute=false,
      source_snapshot=excluded.source_snapshot,
      candidate_count=0, changed_candidate_count=0, quarantined_count=0, error_code=excluded.error_code
  `;
}

/** Running tallies live on the row itself so no phase has to hold the whole slate.
 *
 * The base is guarded on jsonb_typeof rather than coalesced. Every source
 * snapshot written before this change is a jsonb *string*, and `string ||
 * object` yields a jsonb array in Postgres, not an object, after which
 * `->>'observations_seen'` reads NULL and the tallies silently reset to zero
 * on every request. Guarding makes those rows self-heal on their next run.
 *
 * candidate_count advances by candidates finalized, never by request entries:
 * a sliced candidate appears in several requests but must be counted once. */
// deno-lint-ignore no-explicit-any
async function accumulate(tx: any, runId: string, counts: BatchCounts): Promise<void> {
  await tx`
    update wow_scout.research_runs set
      candidate_count = candidate_count + ${counts.candidatesFinalized},
      changed_candidate_count = changed_candidate_count + ${counts.changed},
      quarantined_count = quarantined_count + ${counts.quarantined},
      source_snapshot = (case when jsonb_typeof(source_snapshot)='object' then source_snapshot else '{}'::jsonb end) || jsonb_build_object(
        'observations_seen', coalesce(((case when jsonb_typeof(source_snapshot)='object' then source_snapshot else '{}'::jsonb end)->>'observations_seen')::int,0) + ${counts.observations},
        'source_snapshots_seen', coalesce(((case when jsonb_typeof(source_snapshot)='object' then source_snapshot else '{}'::jsonb end)->>'source_snapshots_seen')::int,0) + ${counts.sourceSnapshots},
        'candidate_source_links_seen', coalesce(((case when jsonb_typeof(source_snapshot)='object' then source_snapshot else '{}'::jsonb end)->>'candidate_source_links_seen')::int,0) + ${counts.candidateSourceLinks}
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
      source_snapshot = (case when jsonb_typeof(source_snapshot)='object' then source_snapshot else '{}'::jsonb end) || ${runSnapshot(body, sports)}::text::jsonb
        || jsonb_build_object(
          'observations_seen', coalesce(((case when jsonb_typeof(source_snapshot)='object' then source_snapshot else '{}'::jsonb end)->>'observations_seen')::int,0),
          'source_snapshots_seen', coalesce(((case when jsonb_typeof(source_snapshot)='object' then source_snapshot else '{}'::jsonb end)->>'source_snapshots_seen')::int,0),
          'candidate_source_links_seen', coalesce(((case when jsonb_typeof(source_snapshot)='object' then source_snapshot else '{}'::jsonb end)->>'candidate_source_links_seen')::int,0)
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
      let counts: BatchCounts = { changed: 0, quarantined: 0, candidatesFinalized: 0, observations: 0, sourceSnapshots: 0, candidateSourceLinks: 0 };
      await sql.begin(async (tx) => {
        counts = await persistCandidates(tx, runId, candidates);
        await accumulate(tx, runId, counts);
      });
      return response({
        ok: true,
        persist_phase: "APPEND",
        research_run_id: runId,
        batch_entry_count: candidates.length,
        batch_finalized_candidate_count: counts.candidatesFinalized,
        batch_evidence_row_count: counts.observations,
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
    let counts: BatchCounts = { changed: 0, quarantined: 0, candidatesFinalized: 0, observations: 0, sourceSnapshots: 0, candidateSourceLinks: 0 };
    let summary: Row = {};
    await sql.begin(async (tx) => {
      await openRun(tx, runId, body);
      counts = await persistCandidates(tx, runId, candidates);
      await accumulate(tx, runId, counts);
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
