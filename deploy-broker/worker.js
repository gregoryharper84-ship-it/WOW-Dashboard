// WOW deploy broker — sole custodian of RENDER_API_KEY.
// Accepts only a GitHub Actions OIDC token minted by release-authority.yml,
// running on main, in the wow-release environment, via workflow_dispatch,
// for the SHA that is protected main right now.
//
// Required Worker secrets/vars:
//   RENDER_API_KEY (secret), RENDER_SERVICE_ID, DEPLOY_BROKER_AUDIENCE,
//   GITHUB_REPOSITORY_ID   (numeric id of gregoryharper84-ship-it/WOW-Dashboard)
//   GITHUB_OWNER_ID        (numeric id of gregoryharper84-ship-it)
//   GITHUB_READ_TOKEN      (optional, read-only fine-grained PAT for the main-HEAD recheck)
import { jwtVerify, createRemoteJWKSet } from 'jose';

const ISSUER = 'https://token.actions.githubusercontent.com';
const REPO = 'gregoryharper84-ship-it/WOW-Dashboard';
const WORKFLOW_REF = `${REPO}/.github/workflows/release-authority.yml@refs/heads/main`;
const SHA_RE = /^[0-9a-f]{40}$/;

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

// Dependencies are injectable so negative tests can run offline with a local key.
export function createHandler({ jwks, fetchImpl = (...a) => fetch(...a) }) {
  return async function handle(request, env) {
    if (request.method !== 'POST') return json({ error: 'METHOD_NOT_ALLOWED' }, 405);

    const token = (request.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
    if (!token) return json({ error: 'MISSING_BEARER_TOKEN' }, 401);
    if (!env.DEPLOY_BROKER_AUDIENCE || !env.GITHUB_REPOSITORY_ID || !env.GITHUB_OWNER_ID) {
      return json({ error: 'BROKER_MISCONFIGURED' }, 500); // fail closed, no defaults
    }

    let payload;
    try {
      ({ payload } = await jwtVerify(token, jwks, {
        issuer: ISSUER,
        audience: env.DEPLOY_BROKER_AUDIENCE,
        algorithms: ['RS256'],
        maxTokenAge: '5m',
      }));
    } catch (err) {
      return json({ error: 'TOKEN_VERIFICATION_FAILED' }, 401);
    }

    // Boundary assertions. IDs, not names, so a rename/transfer cannot spoof them.
    const checks = {
      repository: payload.repository === REPO,
      repository_id: String(payload.repository_id) === String(env.GITHUB_REPOSITORY_ID),
      owner_id: String(payload.repository_owner_id) === String(env.GITHUB_OWNER_ID),
      ref: payload.ref === 'refs/heads/main',
      environment: payload.environment === 'wow-release',
      workflow: payload.job_workflow_ref === WORKFLOW_REF,
      event: payload.event_name === 'workflow_dispatch',
    };
    if (Object.values(checks).includes(false)) {
      return json({ error: 'SECURITY_BOUNDARY_VIOLATION', details: checks }, 403);
    }

    let body;
    try { body = await request.json(); } catch { return json({ error: 'INVALID_BODY' }, 400); }
    if (!SHA_RE.test(body?.commit_sha || '') || body.commit_sha !== payload.sha) {
      return json({ error: 'SHA_MISMATCH' }, 400);
    }

    // Defense in depth: the SHA must still be protected main at dispatch time.
    if (env.GITHUB_READ_TOKEN) {
      const ref = await fetchImpl(`https://api.github.com/repos/${REPO}/git/ref/heads/main`, {
        headers: {
          Authorization: `Bearer ${env.GITHUB_READ_TOKEN}`,
          Accept: 'application/vnd.github+json',
          'User-Agent': 'wow-deploy-broker',
        },
      });
      const head = ref.ok ? (await ref.json())?.object?.sha : null;
      if (head !== payload.sha) return json({ error: 'STALE_MAIN', main: head }, 409);
    }

    const renderRes = await fetchImpl(
      `https://api.render.com/v1/services/${env.RENDER_SERVICE_ID}/deploys`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${env.RENDER_API_KEY}`,
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ clearCache: 'do_not_clear', commitId: payload.sha }),
      },
    );
    const renderData = await renderRes.json().catch(() => ({}));
    if (!renderRes.ok) {
      // Do not echo Render's body verbatim; it can include account details.
      return json({ error: 'RENDER_DEPLOY_FAILED', status: renderRes.status }, 502);
    }

    return json({
      status: 'DEPLOY_DISPATCHED',
      commit_sha: payload.sha,
      deploy_id: renderData.id,
      run_id: payload.run_id,
      actor: payload.actor,
      timestamp: new Date().toISOString(),
    });
  };
}

let defaultHandler;
export default {
  fetch(request, env) {
    defaultHandler ??= createHandler({
      jwks: createRemoteJWKSet(new URL(`${ISSUER}/.well-known/jwks`)),
    });
    return defaultHandler(request, env);
  },
};
