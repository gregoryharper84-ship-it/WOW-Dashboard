#!/usr/bin/env bash
# Research-only signing example. Does not grant V17 model certification or publishability.
set -euo pipefail
: "${PREDICTION_JSON:?Set PREDICTION_JSON to verified input JSON file}"
: "${FROZEN_FILE:?Set FROZEN_FILE to new immutable frozen output path}"
: "${STATEMENT_FILE:?Set STATEMENT_FILE to new in-toto statement path}"
: "${SIGSTORE_BUNDLE:?Set SIGSTORE_BUNDLE to new Cosign bundle path}"
: "${WOW_SIGNING_IDENTITY_APPROVED:?Must be yes after reviewing signing identity + public log visibility}"
if [[ "${WOW_SIGNING_IDENTITY_APPROVED}" != "yes" ]]; then
  echo "Identity/public disclosure not approved; no signature emitted." >&2
  exit 2
fi
python3 -m v17.pregame_attestation build \
  --prediction "$PREDICTION_JSON" --frozen-out "$FROZEN_FILE" \
  --statement-out "$STATEMENT_FILE"
# Produces a bundle; issuance identity/issuer must be pinned at verification time.
cosign sign-blob --yes --bundle "$SIGSTORE_BUNDLE" "$STATEMENT_FILE"
if [[ "${WOW_OTS_RESEARCH_ANCHOR:-no}" == "yes" ]]; then
  ots stamp "$STATEMENT_FILE"
fi
echo "SIGNED BUT NOT CUTOFF-VERIFIED OR MODEL-CERTIFIED. Obtain and verify RFC3161 pregame proof."
