# Ordinary PR lane

Ordinary PRs can merge themselves through the existing Morning-Green coordinator, `wow-v17-morning-green-continuation` (added in #2753). There is no separate controller and no GitHub native auto-merge.

## Opt in

Put these lines in the PR body, each on its own line:

```
WOW-Ordinary-Auto-Merge: true
WOW-Change-Class: A
Incident: #<issue number>
```

- Use `WOW-Change-Class: B` for probability-adjacent infrastructure.
- Class C, trust-root and owner-approval PRs never use this lane.
- The `Incident:` line is what the QA check reads.

## What must hold at the exact head SHA

1. A diff guard (`v17/ordinary_merge_guard.py`, run from protected `main`) finds nothing that contradicts the declared class. It denies a PR that contains any of the following:
   - trust-root paths: `.github/**`, `render.yaml`, `CLAUDE.md`;
   - paths in `.github/morning-green-protected-paths.txt`;
   - file names about release, review, merge, deploy, auth, model, calibration, specialist, terminal, threshold, challenger or migration;
   - any change-impact gate beyond base regression;
   - a deleted test;
   - 300 or more files.
2. These five checks pass:
   - WOW required-three regression
   - WOW governed probability backend
   - WOW additional required regression
   - WOW Independent QA exact-head
   - WOW Release Authority exact-head
3. The branch is current: `mergeable_state` is `clean`.

The coordinator merges with `--match-head-commit`, then re-dispatches `main` CI so the required-checks deployer ships the result.

## What the lane decides

The guard can only deny. An ordinary PR it denies can still be merged by an agent under V27.

The lane trusts the author's declared class, the same way a manual agent merge does. It catches only the contradictions it can detect.

`can_execute=false`.
