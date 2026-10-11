import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SignJWT, generateKeyPair, exportJWK, createLocalJWKSet } from 'jose';
import { createHandler } from '../worker.js';

const SHA = 'a'.repeat(40);
const ENV = {
  DEPLOY_BROKER_AUDIENCE: 'https://wow-deploy-broker.internal',
  GITHUB_REPOSITORY_ID: '1240256887',
  GITHUB_OWNER_ID: '285088163',
  RENDER_API_KEY: 'test-key',
  RENDER_SERVICE_ID: 'srv-test',
  GITHUB_READ_TOKEN: 'test-read',
};
const GOOD = {
  repository: 'gregoryharper84-ship-it/WOW-Dashboard',
  repository_id: '1240256887',
  repository_owner_id: '285088163',
  ref: 'refs/heads/main',
  environment: 'wow-release',
  job_workflow_ref:
    'gregoryharper84-ship-it/WOW-Dashboard/.github/workflows/release-authority.yml@refs/heads/main',
  event_name: 'workflow_dispatch',
  sha: SHA,
  run_id: '1',
  actor: 'gregoryharper84-ship-it',
};

const { publicKey, privateKey } = await generateKeyPair('RS256');
const jwk = { ...(await exportJWK(publicKey)), kid: 'k1', alg: 'RS256' };
const jwks = createLocalJWKSet({ keys: [jwk] });
const other = await generateKeyPair('RS256');

async function mint(claims = {}, opts = {}) {
  return new SignJWT({ ...GOOD, ...claims })
    .setProtectedHeader({ alg: 'RS256', kid: 'k1' })
    .setIssuer(opts.iss ?? 'https://token.actions.githubusercontent.com')
    .setAudience(opts.aud ?? ENV.DEPLOY_BROKER_AUDIENCE)
    .setIssuedAt(opts.iat ?? Math.floor(Date.now() / 1000))
    .setExpirationTime(opts.exp ?? '5m')
    .sign(opts.key ?? privateKey);
}

function harness({ mainSha = SHA, renderOk = true } = {}) {
  const calls = [];
  const fetchImpl = async (url) => {
    calls.push(String(url));
    if (String(url).includes('api.github.com')) {
      return new Response(JSON.stringify({ object: { sha: mainSha } }), { status: 200 });
    }
    return new Response(JSON.stringify({ id: 'dep-1' }), { status: renderOk ? 201 : 500 });
  };
  return { handle: createHandler({ jwks, fetchImpl }), calls };
}

const req = (token, body = { commit_sha: SHA }, method = 'POST') =>
  new Request('https://broker.test/', {
    method,
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), 'Content-Type': 'application/json' },
    body: method === 'POST' ? JSON.stringify(body) : undefined,
  });

const renderCalled = (calls) => calls.some((u) => u.includes('api.render.com'));

test('happy path dispatches exactly the token sha', async () => {
  const { handle, calls } = harness();
  const res = await handle(req(await mint()), ENV);
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.status, 'DEPLOY_DISPATCHED');
  assert.equal(body.commit_sha, SHA);
  assert.ok(renderCalled(calls));
});

test('rejects non-POST and missing token', async () => {
  const { handle, calls } = harness();
  assert.equal((await handle(req(null, null, 'GET'), ENV)).status, 405);
  assert.equal((await handle(req(null), ENV)).status, 401);
  assert.ok(!renderCalled(calls));
});

test('fails closed when broker config is missing', async () => {
  const { handle, calls } = harness();
  const res = await handle(req(await mint()), { ...ENV, GITHUB_REPOSITORY_ID: '' });
  assert.equal(res.status, 500);
  assert.ok(!renderCalled(calls));
});

for (const [name, opts] of [
  ['wrong signing key', { key: other.privateKey }],
  ['wrong audience', { aud: 'https://evil.example' }],
  ['wrong issuer', { iss: 'https://evil.example' }],
  ['expired token', { iat: Math.floor(Date.now() / 1000) - 3600, exp: Math.floor(Date.now() / 1000) - 1800 }],
]) {
  test(`401 on ${name}`, async () => {
    const { handle, calls } = harness();
    const res = await handle(req(await mint({}, opts)), ENV);
    assert.equal(res.status, 401);
    assert.ok(!renderCalled(calls));
  });
}

for (const [name, claims] of [
  ['feature branch ref', { ref: 'refs/heads/security/x' }],
  ['pull request ref', { ref: 'refs/pull/1667/merge' }],
  ['other environment', { environment: 'wow-qa' }],
  ['no environment', { environment: undefined }],
  ['other workflow on main', {
    job_workflow_ref: 'gregoryharper84-ship-it/WOW-Dashboard/.github/workflows/wow-v17-release-authority-check.yml@refs/heads/main' }],
  ['workflow from a branch', {
    job_workflow_ref: 'gregoryharper84-ship-it/WOW-Dashboard/.github/workflows/release-authority.yml@refs/heads/evil' }],
  ['non-dispatch event', { event_name: 'workflow_run' }],
  ['renamed/other repo id', { repository_id: '1' }],
  ['other owner id', { repository_owner_id: '2' }],
  ['fork repo name', { repository: 'attacker/WOW-Dashboard' }],
]) {
  test(`403 SECURITY_BOUNDARY_VIOLATION on ${name}`, async () => {
    const { handle, calls } = harness();
    const res = await handle(req(await mint(claims)), ENV);
    assert.equal(res.status, 403);
    assert.equal((await res.json()).error, 'SECURITY_BOUNDARY_VIOLATION');
    assert.ok(!renderCalled(calls));
  });
}

test('400 when body sha differs from token sha', async () => {
  const { handle, calls } = harness();
  const res = await handle(req(await mint(), { commit_sha: 'b'.repeat(40) }), ENV);
  assert.equal(res.status, 400);
  assert.ok(!renderCalled(calls));
});

test('409 when main moved since the token was minted', async () => {
  const { handle, calls } = harness({ mainSha: 'c'.repeat(40) });
  const res = await handle(req(await mint()), ENV);
  assert.equal(res.status, 409);
  assert.ok(!renderCalled(calls));
});

test('502 without echoing Render body on Render failure', async () => {
  const { handle } = harness({ renderOk: false });
  const res = await handle(req(await mint()), ENV);
  assert.equal(res.status, 502);
  assert.deepEqual(Object.keys(await res.json()).sort(), ['error', 'status']);
});
