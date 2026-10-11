# wow-deploy-broker

Sole custodian of the Render deploy key. GitHub never holds `RENDER_API_KEY` for
production deploys; `.github/workflows/release-authority.yml` proves its identity
with a GitHub Actions OIDC token and the broker decides.

The broker accepts a request only if the token was minted by
`release-authority.yml@refs/heads/main`, via `workflow_dispatch`, in the
`wow-release` environment, for repository id 1240256887 / owner id 285088163,
and `commit_sha` equals the token's `sha` (and, with `GITHUB_READ_TOKEN` set,
the current head of `main`). Everything else fails closed.

## Deploy (owner, manual)

```bash
cd deploy-broker
npm install
npx wrangler login
npx wrangler secret put RENDER_API_KEY      # Render API key (move it here from GitHub)
npx wrangler secret put RENDER_SERVICE_ID   # srv-... of the production service
npx wrangler secret put GITHUB_READ_TOKEN   # optional: fine-grained PAT, Contents: read on WOW-Dashboard only
npx wrangler deploy
npm test                                     # 20 offline negative/positive tests
```

Then set environment variable `DEPLOY_BROKER_URL` (the `https://...workers.dev`
URL) in Settings → Environments → `wow-release`.

## Negative checks after deploy

```bash
curl -i -X GET  "$DEPLOY_BROKER_URL"                       # 405 METHOD_NOT_ALLOWED
curl -i -X POST "$DEPLOY_BROKER_URL"                       # 401 MISSING_BEARER_TOKEN
curl -i -X POST "$DEPLOY_BROKER_URL" -H "Authorization: Bearer x.y.z" \
  -H "Content-Type: application/json" -d '{"commit_sha":"'"$(printf 'a%.0s' {1..40})"'"}'
                                                           # 401 TOKEN_VERIFICATION_FAILED
```

A real 403 `SECURITY_BOUNDARY_VIOLATION` needs a genuine GitHub OIDC token from a
non-main branch or another workflow; the offline tests in `test/` cover those
claim checks with a locally signed key.
