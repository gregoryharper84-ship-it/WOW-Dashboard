---
name: Open-Meteo transient 503s
description: api.open-meteo.com occasionally returns a 503 even when the service is fully reachable; do not treat a single FAILED as a confirmed outage.
---

`GET https://api.open-meteo.com/v1/forecast` returned a transient `503` on first probe during health-check verification, then succeeded immediately on retry (both via the app's own health route and an independent direct shell curl to the same URL).

**Why:** Open-Meteo is a free, unauthenticated public API with no SLA; brief 503s appear to be normal load-shedding, not a real host/connectivity problem.

**How to apply:** when validating or debugging `/wow/open-meteo/health` (or anything else hitting this API), retry once before concluding the source is down. The health endpoint itself correctly reports `FAILED` on a single failed call by design — that is correct honest-failure behavior, not a bug — but a human/agent reading one `FAILED` result should re-check before escalating.
