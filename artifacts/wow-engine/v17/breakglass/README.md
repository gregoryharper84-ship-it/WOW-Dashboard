# WOW V17 — Owner-signed emergency Release recovery (one human)

## Status and controlling policy

This is a **new, explicit owner-only emergency policy candidate**, requested by the repository owner on 2026-10-22. It **replaces the former requirement for a second human/SIRT reviewer for this narrow release-compatibility recovery**. Do not describe an owner approval as independent SIRT approval. The normal five GitHub App-pinned required checks remain enforced outside an authorized maintenance interval.

Use **`owner_release_recovery.py`**. Its companion **`owner_release_watchdog.py`** is launched automatically as a separate OS process; no second human, GitHub reviewer account, SIRT signature or independently held key is needed. The older `release_breakglass.py` and `release_breakglass_witness.py` are legacy two-principal implementation helpers and are **not** the owner's chosen runbook.

The new owner-only path is **not a routine merge bypass**, standing GitHub ruleset exception, independent QA/Release App publisher, betting action or deployment. It affects *only* ruleset #24907422, temporarily drops *only* Release App 5245853 while keeping the three GitHub Actions App 15368 checks and QA App 5245832 unchanged, performs **one exact-SHA native merge**, and immediately restores the complete original policy. The existing V17 scoring/governance terminal reducer is unchanged; `can_execute=false` for wagers.

## Key safety trade-offs

- **One human, one offline authorization**: The repository owner's existing offline ED25519 key is pinned to fingerprint `SHA256:BGiNe5ggZrgozxmFBXsFEEyn8szHPHFmZSCN5nJ/+SM`. `ssh-keygen -Y sign` signs the exact canonical `intent.json` with namespace `wow-breakglass`. Signer identity cannot be replaced by an arbitrary local `allowed_signers` key.
- **One-time, narrow scope**: The signed intent freezes repository, PR, head and main 40-character SHAs, exact changed-file inventory, incident ID, full ruleset SHA256, bounded deadline, Release check identity and explicit `OWNER_AUTHORIZES_REPOSITORY_WIDE_TEMPORARY_RELEASE_EXCEPTION`. It expires in 30 minutes. Stale evidence, missing owner exact-head APPROVED, wrong-App/latest failed CI/QA, incomplete PR files, drift, armed auto-merges or changed rules are refused.
- **Crash recovery without another person**: After persisting a private original snapshot, the operator starts a separate watchdog process which verifies the baseline, signals ready and attempts to restore the five checks after the configured deadline if the operator dies. The operator also restores in `finally` after any merge success, failure or ambiguous PUT, and independently re-reads GitHub.
- **Residual risk is real**: Both operator and watchdog use the same machine and authenticated administrator capability. If the whole machine, GitHub API or credentials fail simultaneously, **automatic restoration is not guaranteed**. Manual recovery from the signed original backup is mandatory when service returns. A separate managed host may be added for machine-level resilience; **no second human reviewer is required**.
- **Repository-wide exposure**: GitHub required-check rules do not provide an atomic per-PR exception. The `--exclusive-window-verified` flag records the owner's operational attestation, **not a technical lock**. The owner must freeze/constrain other merges and writers, check queued automation and commit identity and maintain the short interval. If the exclusive window cannot be established, do not execute.

## Mac CLI — install and test

Prerequisites: `gh` authenticated as `gregoryharper84-ship-it` with repository admin, `ssh-keygen`, Python 3.10+, local offline owner private key (do not upload it), and a trusted checkout of this reviewed PR. The source PR itself is a **Draft candidate** and must pass tests and owner approval before it is treated as operationally qualified.

```bash
REPO="gregoryharper84-ship-it/WOW-Dashboard"
cd "$HOME/wow-v17-recovery"
gh pr checkout 1961

python3 -m venv .venv
source .venv/bin/activate
python -m pip install pytest

python -m pytest -q artifacts/wow-engine/v17/breakglass/test_owner_release_recovery.py

SCRIPT="artifacts/wow-engine/v17/breakglass/owner_release_recovery.py"
python "$SCRIPT" inspect --pr 1932
```

### Owner-only intent preparation (READ ONLY)

```bash
REC="$HOME/wow1932-owner-recovery"
mkdir -p "$REC" && chmod 700 "$REC"
umask 077

python "$SCRIPT" prepare --pr 1932 --incident 1930 \
  --allowed-file .github/workflows/wow-v17-independent-qa-check.yml \
  --allowed-file .github/scripts/tests/test_independent_source_review_receipt.py \
  --output "$REC/intent.json"

# IMPORTANT: preparation must have succeeded and the intent must exist:
test -s "$REC/intent.json" || { echo "NO SIGNABLE INTENT"; exit 1; }
```

Expected `OWNER_INTENT_PREPARED_NO_GITHUB_MUTATION`. Do not sign on a failed `prepare`.

### Owner offline signature (NO SECOND PERSON)

```bash
sed 's/namespaces="wow-release"/namespaces="wow-breakglass"/' \
  .github/release/owner_allowed_signers > "$REC/owner_allowed_signers"
ssh-keygen -lf "$HOME/.ssh/wow_owner_release.pub" -E sha256
# Must be SHA256:BGiNe5ggZrgozxmFBXsFEEyn8szHPHFmZSCN5nJ/+SM

ssh-keygen -Y sign -f "$HOME/.ssh/wow_owner_release" \
  -n wow-breakglass "$REC/intent.json"

# The signature is created at "$REC/intent.json.sig".
# Type the SSH key passphrase only in the Mac terminal, never in chat.
```

### EXECUTE — only after a real exclusive writer/merge freeze

If any state changes, discard the old intent, make a new one, and re-sign. Confirm no queued automation or other merge writer can land during the temporary repo-wide ruleset change.

```bash
python "$SCRIPT" execute \
  --intent "$REC/intent.json" \
  --owner-signature "$REC/intent.json.sig" \
  --owner-signers "$REC/owner_allowed_signers" \
  --checkpoint "$REC/journal" \
  --exclusive-window-verified
```

The command first performs another preflight, snapshots the ruleset to `journal/baseline.json`, starts the independent OS watchdog and verifies readiness before PUT. It makes exactly one conditional merge attempt. An uncertain merge outcome **is not retried**. A successful source workflow is not equivalent to a successful Release App check.

### Emergency five-check restoration (signed original snapshot)

If the operator or watchdog reports a P0 restore failure and the GitHub API is reachable, use the last verified signed baseline. The owner intent can be expired for restoration only.

```bash
python "$SCRIPT" restore \
  --intent "$REC/intent.json" \
  --owner-signature "$REC/intent.json.sig" \
  --owner-signers "$REC/owner_allowed_signers" \
  --baseline "$REC/intent.baseline.json"
```

## Acceptance — do not declare closure prematurely

1. The exact signed #1932 PR head is merged by GitHub's native merge API, not by direct branch rewrite.
2. A fresh read of ruleset #24907422 matches the original **full** SHA256 and all five expected App identities; no bypass actors, altered branch scope, deleted protections or broader exemptions.
3. New protected-main QA workflow runs use static `run-name: wow-v17-independent-qa-check`; no old QA run receipt is reused.
4. Against an **eligible open PR at its current exact SHA**, a fresh QA App 5245832 PASS is followed by a real Release App 5245853 PASS, with correct run ID/attempt/provenance. The original merged #1932 cannot be re-scored as an open PR.
5. GitHub audit log, checkpoint and watchdog log are inspected. Current Render SHA and model production capability are assessed separately.

## Regression suite

`test_owner_release_recovery.py` includes owner-only intent positive/negative cases, unauthorized file/ruleset/check removal rejection, real SSH signature tamper verification, missing/non-pinned signer rejection, latest authentic App-check failure overriding earlier green, latest owner disapproval, branch drift, armed auto-merge, failed merge restoration, ambiguous PUT restoration, watchdog recovery after operator death and refusal to arm on a mismatched baseline.

The **prior two-principal suite remains as legacy source tests**, not a prerequisite or authorization for the owner-only emergency policy. Never claim an independent SIRT signoff, and never remove regular QA/Release required contexts permanently.
