# WOW V17 cryptographic pregame receipt — reference adoption V1

**Status:** ADDITIVE REFERENCE ONLY; never terminal-authoritative or model-certifying. Existing V17 pregame row locks, exact receipt lookup, model certification, QA, and V17_TERMINAL_REDUCER remain authoritative. This does **not** create a live issuer, qualified specialist, or authentication of an official game/quote/model. `can_execute=false` always.

## Threat model and chain of custody

1. The source system **independently verifies** canonical event identity, pregame quote origin/freshness, fitted model artifact, calibration provenance, and the actual persisted frozen prediction. Never build a supposed authoritative receipt from untrusted client-supplied claims.
2. Build a canonical UTF-8 JSON frozen prediction with fixed decimal **strings**; `build_statement` binds the **exact frozen prediction byte SHA-256** under an in-toto Statement v1 subject. The predicate also carries its fields and linked SHA-256 digests for feature snapshot, model binary and quote receipt. Every digest must be reconciled to real bytes by the issuing/verification workflow; a string-shaped hash is not proof a source exists.
3. Cosign keyless-sign the **exact statement file bytes**. Verification must pin approved certificate identity **and** OIDC issuer; verify using an approved Sigstore trust root and transparency proof. Do not infer authorization from any arbitrary valid identity.
4. This implementation requires a **separate independently verified RFC3161 TSA response over the exact statement bytes**, with a reviewed CA chain. The verified TSA generation time must satisfy `frozen_at <= tsa_time < kickoff`. The program calls `openssl ts -verify -data <statement> -in <response> -CAfile <roots>` and extracts time **only after** successful signature/imprint validation. Sigstore/Rekor v1 `integratedTime` alone does not serve as this cutoff. A postgame timestamp cannot prove a pregame receipt.
5. OpenTimestamps `ots stamp statement.json` may be stored as **supplementary eventual anchoring**, but does not establish a pregame cutoff unless its independently verified Bitcoin anchor itself predates kickoff. A pending proof or anchor first confirmed after kickoff is not a cutoff witness.
6. Keep sensitive prediction details and input data in private governed storage. Public Sigstore disclosure/log behavior must be reviewed before enabling production signing.
7. The CLI reports `PREGAME_INTEGRITY_VERIFIED` only for exact-byte, signer, and trusted-time verification. It **always** reports `model_certification_verified=false`, `probability_publishable=false`, `card_admission_eligible=false`. A signed `CERTIFIED_FITTED` *claim* is not independently certified. Do not use this output to set terminal publishability flags.

## Files
- `v17/pregame_attestation.py` — Python standard library reference builder, adversarial schema validation and read-only crypto proof runner (Cosign and OpenSSL external binaries for verification).
- `docs/wow/governance/V17_PREGAME_ATTESTATION_V1.schema.json` — versioned interoperable JSON Schema for the in-toto statement.
- `scripts/v17_sign_pregame_attestation.sh` — optional operator workflow: build, sign, and optionally submit OpenTimestamps proof.
- `tests/test_v17_pregame_attestation.py` — no-network synthetic negative tests, mocked crypto CLI boundaries.

## Local usage (not a production issuer)

```bash
export PYTHONPATH=artifacts/wow-engine
python3 -m v17.pregame_attestation build \
  --prediction pregame-input.json --frozen-out frozen-prediction.json \
  --statement-out prediction.intoto.json

# Keyless signing; first review identity, data sensitivity and public disclosure.
cosign sign-blob --yes --bundle prediction.sigstore.json prediction.intoto.json

# Optional research anchor; does not immediately establish a pregame cutoff:
ots stamp prediction.intoto.json

# AFTER obtaining a trusted RFC3161 response over prediction.intoto.json
# and pinning the reviewed TSA root, verify the exact statement:
python3 -m v17.pregame_attestation verify \
  --frozen frozen-prediction.json \
  --statement prediction.intoto.json \
  --bundle prediction.sigstore.json \
  --signer 'APPROVED_EXACT_CERTIFICATE_IDENTITY' \
  --issuer 'APPROVED_OIDC_ISSUER' \
  --tsa-response prediction.tsr --tsa-roots approved-tsa-roots.pem
```

Generate the RFC3161 request with `openssl ts -query -data prediction.intoto.json -sha256 -cert -out prediction.tsq`. Deliver it only to an **approved, rights-permitted and accessible** TSA, preserve its response and chain, and verify the response against the exact statement. This repository does not select, purchase, stand up or trust a TSA by default. No networking or timestamp submission is automatically performed by the verifier.

The first `build` output is **UNSIGNED**, the `sign-blob` output proves signature only, and the `verify` output is **HELD** without all required third-party proof. A model-certification certificate/registry record remains separately required, as do independent forward calibration and governed release.

## Explicit deferrals
- No new production routes, DB migration, model registry promotion, authenticated issuer, automated OIDC permissions, public publication, wagers or orders.
- No extra CI workflows or mandated new paid services; use existing independent QA and Release checks on the PR.
- No general RFC8785 claim: exact byte encoding is the explicitly defined Python `json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False, allow_nan=False) + "\n"` profile, and all predictive decimals are strings. A future cross-language writer needs conformance tests before issuing receipts.
- Do not mistake local/claimed timestamps or ordinary append-only database locks for an independently attested UTC cutoff.

## Acceptance milestones
(1) All focused adversarial tests pass; (2) independent QA of schema/signature/time bypasses; (3) authorized operator keyless canary + independent TSA proof from a **real** pregame event and externally rehashed model/quote/feature bytes; (4) only after separate calibration+certification approval may downstream publication be wired under V17_TERMINAL_REDUCER.
