# WOW V17 — independently authorized release break-glass recovery

**State:** Candidate, source-only. This package does not install a bypass actor, update a rule, approve a PR, deploy production, issue a sportsbook order or publish a model probability. `can_execute=false` remains invariant. The V17 terminal reducer remains the sole model publisher.

## Problem and ownership

Current normal policy uses five App-pinned required checks. The Release check itself is required to merge a repair to the QA/Release workflow that creates that check. This is a **bootstrap dependency**, not a valid reason to invent a Release PASS, reuse old QA receipts or remove controls indefinitely.

This package is an **out-of-band administrator CLI**, not a GitHub Actions job. It works even if GitHub's Release workflow does not. It can temporarily remove **only** the `WOW Release Authority exact-head` requirement (App 5245853) and preserve the three GitHub Actions App 15368 checks and QA App 5245832. It immediately restores the exact full ruleset snapshot after a successful or unsuccessful native GitHub merge. A separate witness on another machine/credential restores after a crashed operator reaches the signed deadline.

There is **no per-PR scope** for a GitHub ruleset change. The temporary four-check state changes eligibility across the whole repository. Neither a clean PR nor disabled auto-merge **proves exclusive access**. An authorized administrator must independently freeze other writers/merges during the short window. If that cannot be guaranteed, **do not execute**; use a separately approved alternative break-glass governance mechanism.

## Trust boundaries

1. **Owner offline key**: pin the existing owner release public-key fingerprint `SHA256:BGiNe5ggZrgozxmFBXsFEEyn8szHPHFmZSCN5nJ/+SM`. This is an existing public trust root only; never copy the private key to Actions, agents, CI or the repository. For this CLI the corresponding allow-signers record must use SSH namespace `wow-breakglass`, and an offline owner signature must cover the exact canonical JSON intent.
2. **Independent SIRT key**: a **different**, independently controlled key, registered outside the candidate PR; the owner-signed intent binds the exact authorized SIRT GitHub login. The reviewer also must submit a real GitHub `APPROVED` PR review on the exact SHA containing `SIRT_SECURITY_PASS`, the complete head SHA, ruleset ID `24907422` and the exact baseline ruleset digest. Comments, AI research, owner reviews and passing checks are not substitutes.
3. **Operator admin credential**: a repository administrator with genuine permission to read/update ruleset #24907422 and merge the protected PR, on an independent workstation; no GitHub App check-publication credential is used.
4. **Independent restoration witness**: separate administrator credential and host, separately authenticated signed intent and full snapshot, with continuous monitoring. It must report `WITNESS_ARMED` **before** any mutation. If the operator dies or the local `finally` fails, it restores at the bounded deadline. If **both** independent hosts or their network/credentials fail, automatic rollback is **not guaranteed**. This is a P0 incident requiring manual recovery from the snapshot. Do not claim a perfect atomic merge-window lock.
5. **Immutable baseline**: full ruleset snapshot (including protection clauses and bypass actors), SHA-256 digest bound into both offline signatures, frozen main/PR SHA, exact changed-file allowlist, exact App identities, and a 15–120-second maintenance interval. No write occurs during `inspect` or `prepare`.

## One-time #1932 repair walkthrough (no implicit approvals)

First verify current GitHub state, existing CI and a genuinely independent reviewer. **At the last pre-build inspection #1932 had no independent SIRT review; that means execution must fail closed.**

```bash
python3 artifacts/wow-engine/v17/breakglass/release_breakglass.py inspect --pr 1932

# Use an actual independent qualified GitHub reviewer's login, not the repo owner,
# Copilot author or a source-only bot. This command makes local files only.
python3 artifacts/wow-engine/v17/breakglass/release_breakglass.py prepare \
  --pr 1932 --incident 1930 --reviewer ACTUAL_SIRT_LOGIN \
  --allowed-file .github/workflows/wow-v17-independent-qa-check.yml \
  --allowed-file .github/scripts/tests/test_independent_source_review_receipt.py \
  --output "$HOME/wow1932/intent.json"
```

`prepare` writes `intent.json` and `intent.baseline.json` with 0600 permissions. Review the exact intent and baseline in a trusted terminal. The intent expires in 45 minutes and binds current main/head/ruleset; if state changes, discard it and start over. Keep the original snapshot off GitHub, and **transfer it to the separately controlled witness host before anything is changed**.

On each independently controlled machine, sign **the unchanged bytes** of `intent.json` using `ssh-keygen -Y sign -n wow-breakglass -f /path/to/offline-private-key intent.json`. This writes `intent.json.sig`; collect the two separate signatures without ever transferring private keys. Configure the authorized allow-signers files from their *previously verified public keys*, with principals exactly `wow-owner` and `wow-sirt-ACTUAL_SIRT_LOGIN` and `namespaces="wow-breakglass"`. It is not sufficient to invent a public key or use two aliases for the same private key.

Have the distinct SIRT reviewer use GitHub's native **Approve** action for #1932 at the frozen head, after independently inspecting the diff and snapshot, merge exposure, writer freeze, witness readiness, crash tests and re-entry. Its review body must include the four tokens noted above. Do not self-approve with the owner GitHub identity.

**Independent witness machine (first):**

```bash
python3 release_breakglass_witness.py \
  --intent intent.json --baseline intent.baseline.json \
  --owner-signature owner-intent.sig --sirt-signature sirt-intent.sig \
  --owner-signers owner_allowed_signers --sirt-signers sirt_allowed_signers
# Keep running until FULL_FIVE_CHECK_RESTORATION or deadline recovery receipt.
```

Only after checking witness readiness and a genuinely exclusive repository-wide merge window, use a distinct authenticated administrator workstation to run:

```bash
python3 release_breakglass.py execute \
  --intent intent.json --owner-signature owner-intent.sig \
  --sirt-signature sirt-intent.sig \
  --owner-signers owner_allowed_signers --sirt-signers sirt_allowed_signers \
  --checkpoint "$HOME/wow1932/recovery-journal" \
  --independent-witness-live --exclusive-window-verified
```

**Important:** The two boolean command flags are *operator attestations*, not cryptographic proof that a witness is still alive or that other humans cannot merge. Run them only after actual verification. Source-only tests and this README are not a SIRT decision, a live witness, an administrator authorization or a merge.

The CLI reads evidence twice; refuses missing/revoked independent review, wrong-App/failing latest required checks, PR/main/ruleset drift, foreign branch, renamed protected path, armed GitHub auto-merge and snapshot mismatch; persists the original ruleset with fsync before mutation; makes exactly one native exact-head merge attempt; restores the original five-check ruleset in `finally` and independently reads it back. No automatic retry after an ambiguous merge response.

If the operator loses network or crashes, the witness independently attempts restoration at its signed deadline. To manually restore from a verified on-disk snapshot:

```bash
python3 release_breakglass.py restore --baseline intent.baseline.json
```

`restore` deliberately does not require a completed PR merge, current main SHA or still-valid intent: restoring full five-check protection is the emergency priority.

## Acceptance before returning to normal flow

* GitHub native PR #1932 merge shows **the exact signed head** and the current main contains the static `run-name: wow-v17-independent-qa-check`.
* Ruleset #24907422 is read back as the **exact original five-check policy**; no bypass actor was installed, deletion and non-fast-forward protections remain intact, and the independent witness confirms restoration. Review the recovery journal and GitHub audit log.
* Run fresh protected-main QA **after the merge**; its actual workflow run `.name` must be the static pinned string. A fresh Release App 5245853 check on the exact resulting PR head must genuinely publish `success`. Neither a GitHub Actions workflow `success` nor a legacy QA PASS proves Release acceptance.
* Only then process waiting model PRs through independent certified production acceptance and current Render/Action receipts. Never conflate model capability with Release repo governance, runtime status or GPT editor synchronization.

## Preventing recurrence

- Keep a source-based producer/verifier contract test to assert exact `run-name`, workflow path, event types, attempt identity and negative mismatches. #1932 already adds focused tests; do not duplicate them here.
- Make the independent maintenance CLI/witness independently testable with synthetic GitHub API fixtures; this package's negative cases cover stale PRs, revoked reviews, mistaken App IDs, missing checks, policy drift, incomplete file inventory, failure after mutation and operator process loss.
- Before adding/changing a mandatory GitHub required check, stage and rehearse an independently verifiable recovery using protected-main truth and an authenticated witness. Do not require the broken check to certify its own recovery.
- This tool does **not** become an auto-merge bot, a persistent four-check bypass, a second global terminal authority or a producer of GitHub App QA/Release results.
