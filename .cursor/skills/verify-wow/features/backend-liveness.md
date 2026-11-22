# Backend liveness

## Sub-features

Read-only connection and HTTP status at the existing Render origin. This proves only public liveness.

## How to get to it (user POV)

The WOW ChatGPT Action depends on the Render API, which exposes GET /health/live as a preliminary support diagnostic.

## Driving it with doctor.py

From repository root:

~~~bash
python3 -m unittest discover -s .cursor/skills/verify-wow/tests -v
python3 .cursor/skills/verify-wow/scripts/doctor.py --output "/tmp/wow-health-$(date -u +%Y%m%dT%H%M%SZ)-$$.json"
~~~

Check exit 0, the new evidence file, status BACKEND_LIVENESS_PASS, HTTP status 200 and user_journey_verified false.

## Gotchas

An HTTP 200 does not prove the live ChatGPT user journey, model scoring, deployed SHA, persistence or Action invocation. Save evidence; no credentials, raw response bodies or writes. An unavailable endpoint is not equivalent to MODEL_UNAVAILABLE.
