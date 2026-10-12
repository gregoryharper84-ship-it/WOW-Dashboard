# WOW owner-signed release (replaces the temporary owner bridge)

## Why

Engineering agents (Claude, ChatGPT/Codex workers) act through the owner's own
GitHub account. Anything that identity can do, an agent can do: post
`/wow-owner-bridge`, dispatch a workflow, approve an environment, write a repo
secret. The temporary bridge (#1577) therefore had no real owner factor (#1585).

This path uses a factor the agents cannot produce: an **SSH signature made on
the owner's own computer** with a passphrase-protected (or hardware) key,
verified against `.github/release/owner_allowed_signers` on protected `main`.

## One-time owner bootstrap

1. On your own computer (not a folder connected to Claude):
   `ssh-keygen -t ed25519 -f ~/.ssh/wow_owner_release -C wow-owner-release`
   Set a passphrase. A hardware key (`-t ed25519-sk`) is better.
2. Add one line to `.github/release/owner_allowed_signers`:
   `wow-owner namespaces="wow-release" <contents of ~/.ssh/wow_owner_release.pub>`
   Commit it yourself and merge it yourself (trust-root change).
3. In repo Settings → Environments → `wow-release`, set deployment branches to
   `main` only.

Until step 2 is done, every release attempt fails closed with
`OWNER_SIGNER_NOT_BOOTSTRAPPED`.

## Releasing one PR

```bash
.github/release/sign_owner_release.sh <PR> <FULL_HEAD_SHA> <INCIDENT> [key] [hours_valid]
```

The script asks for your passphrase and prints a `gh workflow run …` command;
run it. The workflow:

1. **verify** (read-only, no secrets): signature, PR open/non-draft/base main,
   exact head, not behind main, open incident linked from the PR body, no
   `.github/**` or `.agents/**` changes, seven required CI workflows green.
2. **qa** (read-only token; model credential only on the agent step):
   independent PASS required; Class C is denied.
3. **merge** (write token, no agent, no model): re-verifies the signature, head
   SHA and main SHA, then merges that exact SHA. A receipt goes to the incident.

A signature binds repo, PR, exact head SHA, incident and an expiry of at most
24 hours. Any new push, rebase or stale main invalidates it. Merge only: never
deploy, production acceptance or wagering. `can_execute=false`.

## What this does not fix (owner settings)

Branch protection on `main` requires 0 approving reviews, and agents hold the
owner's credential, so an agent can still merge a green PR directly through the
API or change trust roots. Closing that gap needs **separate identities**, which
only the owner can configure:

- Give engineering agents their own GitHub App or machine account (write, no
  admin, no bypass), and stop agents using the owner's personal token (#1540/#1550).
- Then require 1 approving review on `main` from the owner (CODEOWNERS for
  `.github/**` and `.agents/**`), dismiss stale approvals, and require approval
  of the most recent push.

After that, the owner's GitHub approval is a real factor and this signed path
becomes defence in depth.
