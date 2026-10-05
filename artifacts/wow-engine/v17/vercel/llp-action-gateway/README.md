# LLP V17 Vercel Action Ingress Challenger

Transport-only Class B challenger for GPT Action ingress.

It preserves the current canonical LLP Action path set under
`/functions/v1/wow-llp-action-gateway`, forwards the incoming Bearer unchanged to the governed Render
runtime, never produces sporting probability, and keeps `can_execute=false`.

This exists to isolate provider-front-door 403 failures observed before both
Render and Supabase application ingress. It must not replace the production
Action origin until a fresh editor canary proves the Vercel hostname is reachable
from GPT Actions and the backend receipts remain governed.

Deployment root: this directory.

No secrets are stored here. The existing GPT Action Bearer credential is supplied
by ChatGPT and forwarded only to the governed backend.
