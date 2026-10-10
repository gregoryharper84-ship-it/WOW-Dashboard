#!/usr/bin/env bash
# Owner-only: sign a WOW release authorization on YOUR OWN computer.
#
# Usage: sign_owner_release.sh <pr> <head_sha> <incident> [private_key] [hours_valid]
#
# Use a passphrase-protected or hardware (ed25519-sk) key that no engineering
# agent can reach. Never put the private key in the repo, a secret, or a
# connected folder. Prints the exact `gh workflow run` command to dispatch.
set -euo pipefail
[ "$#" -ge 3 ] || { echo "usage: $0 <pr> <head_sha> <incident> [private_key] [hours_valid]" >&2; exit 2; }
repo="gregoryharper84-ship-it/WOW-Dashboard"
pr="$1" head="$2" incident="$3" key="${4:-$HOME/.ssh/wow_owner_release}" hours="${5:-6}"
[[ "$head" =~ ^[0-9a-f]{40}$ ]] || { echo "head must be the full 40-char SHA" >&2; exit 2; }
[[ "$hours" =~ ^[0-9]+$ ]] && [ "$hours" -ge 1 ] && [ "$hours" -le 23 ] || { echo "hours_valid must be 1-23" >&2; exit 2; }
if date -u -d "+1 hour" +%s >/dev/null 2>&1; then
  expires=$(date -u -d "+${hours} hours" +%Y-%m-%dT%H:%M:%SZ)
else
  expires=$(date -u -v+"${hours}"H +%Y-%m-%dT%H:%M:%SZ)  # macOS/BSD date
fi
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
printf 'WOW_OWNER_RELEASE_V1\nrepo=%s\npr=%s\nhead=%s\nincident=%s\nexpires=%s\n' \
  "$repo" "$pr" "$head" "$incident" "$expires" > "$work/message"
ssh-keygen -Y sign -f "$key" -n wow-release "$work/message" >/dev/null
sig=$(base64 < "$work/message.sig" | tr -d '\n')
cat <<OUT
Signed PR #$pr at $head for incident #$incident (valid until $expires).
Run:

gh workflow run wow-v17-owner-signed-release.yml --repo $repo --ref main \\
  -f pr_number=$pr -f expected_head_sha=$head -f incident_id=$incident \\
  -f expires_utc=$expires -f owner_signature=$sig
OUT
