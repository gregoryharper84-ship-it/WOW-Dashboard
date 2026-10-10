#!/usr/bin/env bash
# Verify an owner-signed WOW release authorization (fail closed).
#
# Usage: verify_owner_release.sh <allowed_signers> <repo> <pr> <head_sha> <incident> <expires_utc> <signature_b64>
#
# The owner signs, with an SSH key that only the owner holds, the exact text
# produced by render_message below (namespace "wow-release"). Engineering
# agents share the owner's GitHub identity, so GitHub actor checks, comments,
# environment approvals and repository secrets cannot prove owner intent; a
# signature from an offline key can. Exit 0 only for a valid, unexpired
# signature by the "wow-owner" principal over exactly these values.
set -euo pipefail

fail() { echo "OWNER_RELEASE_SIGNATURE_DENIED:$1" >&2; exit 1; }

[ "$#" -eq 7 ] || fail ARGUMENT_COUNT
signers="$1" repo="$2" pr="$3" head="$4" incident="$5" expires="$6" sig_b64="$7"

[[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || fail REPO_INVALID
[[ "$pr" =~ ^[1-9][0-9]{0,6}$ ]] || fail PR_NUMBER_INVALID
[[ "$head" =~ ^[0-9a-f]{40}$ ]] || fail HEAD_SHA_INVALID
[[ "$incident" =~ ^[1-9][0-9]{0,6}$ ]] || fail INCIDENT_INVALID
[[ "$expires" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] || fail EXPIRY_FORMAT
[[ "$sig_b64" =~ ^[A-Za-z0-9+/=]{100,8192}$ ]] || fail SIGNATURE_FORMAT

[ -f "$signers" ] || fail SIGNERS_FILE_MISSING
grep -Eq '^wow-owner[[:space:]]+(namespaces="wow-release"[[:space:]]+)?(ssh-ed25519|sk-ssh-ed25519@openssh.com|ecdsa-sha2-nistp256|sk-ecdsa-sha2-nistp256@openssh.com)[[:space:]]+[A-Za-z0-9+/=]+' "$signers" \
  || fail OWNER_SIGNER_NOT_BOOTSTRAPPED

now=$(date -u +%s)
exp=$(date -u -d "$expires" +%s 2>/dev/null) || fail EXPIRY_UNPARSEABLE
[ "$exp" -gt "$now" ] || fail EXPIRED
# Bound pre-signed approvals: no authorization may live longer than 24h.
[ "$exp" -le $((now + 86400)) ] || fail EXPIRY_TOO_FAR

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
printf 'WOW_OWNER_RELEASE_V1\nrepo=%s\npr=%s\nhead=%s\nincident=%s\nexpires=%s\n' \
  "$repo" "$pr" "$head" "$incident" "$expires" > "$work/message"
printf '%s' "$sig_b64" | base64 -d > "$work/message.sig" 2>/dev/null || fail SIGNATURE_DECODE
grep -q -- '-----BEGIN SSH SIGNATURE-----' "$work/message.sig" || fail SIGNATURE_NOT_SSHSIG

ssh-keygen -Y verify -f "$signers" -I wow-owner -n wow-release -s "$work/message.sig" \
  < "$work/message" > "$work/verify.out" 2>&1 || fail SIGNATURE_INVALID

echo "OWNER_RELEASE_SIGNATURE_VALID pr=$pr head=$head incident=$incident expires=$expires"
