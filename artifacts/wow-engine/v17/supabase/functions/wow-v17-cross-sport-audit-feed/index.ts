import { createRemoteJWKSet, jwtVerify } from "npm:jose@5.9.6";
import postgres from "npm:postgres@3.4.5";

const ISSUER = "https://token.actions.githubusercontent.com";
const JWKS = createRemoteJWKSet(new URL(`${ISSUER}/.well-known/jwks`));
const AUDIENCE = "wow-v17-cross-sport-audit";
const REPOSITORY = "gregoryharper84-ship-it/WOW-Dashboard";
const REPOSITORY_ID = "1240256887";
const REPOSITORY_OWNER_ID = "285088163";
const REF = "refs/heads/main";
const WORKFLOW_REF =
  `${REPOSITORY}/.github/workflows/wow-v17-cross-sport-geometry-audit.yml@${REF}`;
const ALLOWED_EVENTS = new Set(["push", "workflow_dispatch"]);
const MAX_PAGE_SIZE = 250;

type Row = Record<string, unknown>;

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {"content-type": "application/json; charset=utf-8"},
  });
}

function asObj(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Row
    : {};
}

function str(value: unknown): string {
  return value == null ? "" : String(value);
}

async function authorize(req: Request): Promise<void> {
  const auth = req.headers.get("authorization") || "";
  if (!auth.startsWith("Bearer ")) throw new Error("GITHUB_OIDC_TOKEN_MISSING");
  const token = auth.slice("Bearer ".length).trim();
  const {payload} = await jwtVerify(token, JWKS, {
    issuer: ISSUER,
    audience: AUDIENCE,
    algorithms: ["RS256"],
  });
  const exact: Record<string, string> = {
    repository: REPOSITORY,
    repository_id: REPOSITORY_ID,
    repository_owner_id: REPOSITORY_OWNER_ID,
    ref: REF,
    runner_environment: "github-hosted",
    workflow_ref: WORKFLOW_REF,
  };
  for (const [field, expected] of Object.entries(exact)) {
    if (String(payload[field] || "") !== expected) {
      throw new Error(`GITHUB_OIDC_${field.toUpperCase()}_MISMATCH`);
    }
  }
  if (!ALLOWED_EVENTS.has(String(payload.event_name || ""))) {
    throw new Error("GITHUB_OIDC_EVENT_NOT_ALLOWED");
  }
}

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") {
    return response({ok: false, code: "METHOD_NOT_ALLOWED", can_execute: false}, 405);
  }
  try {
    await authorize(req);
  } catch (err) {
    return response({
      ok: false,
      code: err instanceof Error ? err.message : "GITHUB_OIDC_INVALID",
      can_execute: false,
    }, 401);
  }

  let body: Row;
  try {
    body = asObj(await req.json());
  } catch {
    return response({ok: false, code: "REQUEST_JSON_INVALID", can_execute: false}, 400);
  }

  const dbUrl = Deno.env.get("SUPABASE_DB_URL");
  if (!dbUrl) {
    return response({ok: false, code: "SUPABASE_DB_URL_UNAVAILABLE", can_execute: false}, 500);
  }

  const action = str(body.action).toUpperCase();
  const sql = postgres(dbUrl, {max: 1, prepare: false});
  try {
    if (action === "LATEST_CANDIDATES") {
      let rows: Row[] = [];
      await sql.begin(async (tx) => {
        await tx`set transaction read only`;
        rows = await tx`
          select distinct on (sport,league)
            candidate_id,created_at,sport,league,market_family,model_family,
            model_artifact_version,feature_schema_version,training_dataset_hash,
            artifact_payload,calibrator_payload,validation_metrics,
            training_rows,calibration_rows,test_rows,research_screen_pass,
            source_review_status,lifecycle_state,promoted,active,
            probability_publishable,can_execute
          from public.wow_d1_candidate_artifacts
          order by sport,league,created_at desc
        `;
      });
      return response({
        ok: true,
        action,
        rows,
        probability_publishable: false,
        automatic_promotion: false,
        can_execute: false,
      });
    }

    if (action === "COHORT_PAGE") {
      const sport = str(body.sport).trim().toUpperCase();
      const league = str(body.league).trim().toUpperCase();
      const schema = str(body.feature_schema_version).trim();
      const candidateCreatedAt = str(body.candidate_created_at).trim();
      const offset = Number(body.offset ?? 0);
      const requestedLimit = Number(body.limit ?? MAX_PAGE_SIZE);
      const limit = Math.max(1, Math.min(MAX_PAGE_SIZE, Math.trunc(requestedLimit)));

      if (!sport || !league || !schema || !candidateCreatedAt) {
        return response({ok: false, code: "COHORT_IDENTITY_INCOMPLETE", can_execute: false}, 422);
      }
      if (!Number.isInteger(offset) || offset < 0) {
        return response({ok: false, code: "COHORT_OFFSET_INVALID", can_execute: false}, 422);
      }
      const parsed = Date.parse(candidateCreatedAt);
      if (!Number.isFinite(parsed)) {
        return response({ok: false, code: "CANDIDATE_CREATED_AT_INVALID", can_execute: false}, 422);
      }

      let rows: Row[] = [];
      await sql.begin(async (tx) => {
        await tx`set transaction read only`;
        rows = await tx`
          with ranked as (
            select t.*,
                   row_number() over (
                     partition by t.official_event_id
                     order by t.created_at desc
                   ) as rn
            from public.wow_d1_training_rows t
            where t.sport=${sport}
              and t.league=${league}
              and t.feature_schema_version=${schema}
              and t.created_at <= ${candidateCreatedAt}::timestamptz
          )
          select official_event_id,event_start_time,feature_as_of,features,
                 outcome_json,source_manifest_sha256,archived_pregame_snapshot,
                 historical_reconstruction,market_features_used,can_execute
          from ranked
          where rn=1
          order by event_start_time,official_event_id
          limit ${limit} offset ${offset}
        `;
      });
      return response({
        ok: true,
        action,
        rows,
        offset,
        limit,
        next_offset: rows.length === limit ? offset + rows.length : null,
        done: rows.length < limit,
        probability_publishable: false,
        automatic_promotion: false,
        can_execute: false,
      });
    }

    return response({ok: false, code: "ACTION_NOT_ALLOWED", can_execute: false}, 422);
  } catch (err) {
    return response({
      ok: false,
      code: "CROSS_SPORT_AUDIT_FEED_FAILED",
      error_type: err instanceof Error ? err.name : "Error",
      can_execute: false,
    }, 500);
  } finally {
    await sql.end({timeout: 2});
  }
});
