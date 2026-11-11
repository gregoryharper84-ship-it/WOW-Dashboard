# Pregame participation contract corpus (v1)

These files reproduce the **documented response shapes** of the official sources
the V17 hydration providers read. They exist so availability semantics can be
exercised deterministically, without a network call and without inventing
provider behaviour.

**Provenance: reconstructed, not captured.** No network capture was possible
when this corpus was assembled (2026-11-09): MLB and WNBA were between playing
seasons, and no archived official responses exist in this repository. Every
file is therefore a faithful reproduction of the documented envelope and field
names for its endpoint — verified against the parsing code that consumes it —
not a byte-for-byte recording of a provider response.

Consequences, stated plainly:

- These tests validate **parsing and contract behaviour**, not that the provider
  returned these values on a given day.
- A historical replay over this corpus must never be reported as a live pregame
  verification.
- When the next eligible slate runs, replace each fixture with a captured
  response and update `manifest.json` digests; the assertions should hold
  unchanged if the documented shape is accurate.

`manifest.json` pins the SHA-256 of every fixture so a silent edit is visible.
`v17/test_real_source_participation_contracts.py` verifies those digests and
fails if a fixture is added or removed without updating the manifest.
