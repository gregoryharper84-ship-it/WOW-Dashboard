---
name: Runtime provenance governance
description: Fail-closed, server-authoritative run provenance for WOW — never trust caller-asserted host, capabilities, or verified flags.
---

# Runtime provenance governance

Rule: a run counts as production-verified only when three server-derived facts hold — (1) the request authenticated with the designated Custom-GPT Action credential (request-local principal, not request JSON), (2) every route-registry-required capability passes an in-process probe, (3) probability origin is not local/reconstructed. Anything else is a fallback run capped at MODEL_QUALIFIED_HOLD, downgrade-only, at every enforcement layer.

**Why:** two completion reviews rejected earlier versions for provenance forgery paths — first trusting the record's own verified boolean (fixable by HMAC attestation keyed on a server secret), then deriving host/required-capabilities from request JSON (a caller could omit caps or self-assert the preferred host), then treating *any* valid API key as the preferred host (general scoring-key holders would attest as the GPT). Each layer of caller input must be reduced to downgrade-only signals.

**How to apply:**
- Provenance for governed routes must come from the server-authoritative route builder (route capability registry ∪ caller extras, probes for evidence, credential principal for host); caller context is filtered to downgrade-only keys.
- Verified claims are only accepted after HMAC attestation + internal-consistency checks; a missing record is a blocker except at the row-level gate (SKIP, to protect legacy rows).
- The one run-level record must be propagated to the units that enforcement reads *before* enforcement runs (scan rows at scoring time, CC envelopes before gatekeeper verification) — response-only stamping leaves async/polled paths exposed; unverified async runs are rejected outright.
