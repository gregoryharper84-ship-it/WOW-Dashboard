---
name: Autoscale cold-start keep-alive
description: Autoscale kills the production server after 15 min idle; a self-ping daemon in gunicorn_conf.py prevents this.
---

# Autoscale cold-start keep-alive

## The problem
Replit autoscale issues SIGTERM after ~15 minutes of no incoming traffic. The flask-scoring-api cold-start takes ~14 seconds (gunicorn --preload with 2 workers). Any GPT session that starts during that 14-second window receives ClientResponseError on every endpoint, triggering WOW's fail-closed NO_PLAY.

## The fix
`artifacts/flask-scoring-api/gunicorn_conf.py` — `post_fork` hook, worker.age==1 only:
- Spawns a daemon thread (`autoscale-keepalive`) after a 90-second initial delay
- Pings `$REPLIT_APP_URL/wow/engine/health` every 600 seconds (10 min < 15-min threshold)
- Uses stdlib `urllib.request` — no extra deps
- Skipped silently when `REPLIT_APP_URL` is unset (dev environment)

`REPLIT_APP_URL` is set as a shared env var = `https://create-app-gregoryharper84.replit.app`

**Why:**
The autoscale idle timer resets on every incoming HTTP request. A self-ping every 10 minutes ensures the timer never reaches 15 minutes while the server is running. If the server is killed anyway (e.g. redeploy), the next cold-start is still ~14 seconds — GPT retry logic with a 20-second delay is the complementary defense for that edge case.

## Verification after each deploy
Deployment logs should show within ~90 seconds of startup:
- `[post_fork] worker N: autoscale keep-alive started (interval=600s, url=...)`
- `[keepalive] worker N: pinged health → 200` (appears ~90s after start)

## Related
- `deploymentType` is still `autoscale` (VM switch requires destructive unpublish — not worth it)
- Task #56 tracks production verification of the keep-alive firing
