# WOW V17 — Gemini autonomous AMRT activation

**State:** native Sidecar installer source candidate. `can_execute=false`. Does not install anything to the owner's Mac until the owner executes the installer in the connected Antigravity project. It is **not** a Gemini API/cloud provider or authority to merge, release, approve model probabilities, or place wagers.

## Immediate autonomous engineering (interactive, no repetition of "Proceed")

In the connected WOW-Dashboard Antigravity App project, select a **New Worktree** conversation and **Auto (Preview)** permissions when available (never unrestricted Turbo for unattended engineering).

Paste:

```text
/goal Act as wow-amrt-recovery. Read GEMINI.md, the agent contract and WOW_AMRT_RECOVERY_TEAM.md. Investigate existing issue #1841 and draft PR #1842 at current exact SHA. Work autonomously in an isolated local worktree: reproduce the defect, prepare the smallest possible repair, run positive and negative regressions, and fix defects you introduce. Do not infer executed dynamic calibration from calibration-health fields. Do not push a shared branch without the verified existing Conductor/AMRT writer assignment and atomic lease. No duplicate PR, fake independent review, protected rule change, merge, deployment or wagering. can_execute=false. Continue until there is a tested patch plus truthful handoff or a precise external blocker.
```

Goal mode keeps working through the bounded task; it does not change permissions or release authority.

## Persistent autonomous daily session (Antigravity Sidecar)

The official Antigravity `/schedule` command is conversation-scoped. For work that continues after a conversation ends, the native `sidecar.json` schedule plus `agentapi new-conversation` is the supported solution.

The repository installer `scripts/wow_gemini_sidecar.py` uses Google's documented locations:
- `~/.gemini/config/sidecars/wow-amrt-daily-recovery/sidecar.json` — native builtin `schedule`.
- `~/.gemini/config/config.json` — enables precisely that sidecar and binds it to the genuine project ID.

**On the Mac, while Antigravity is open on WOW-Dashboard-Gemini**, ask the Antigravity agent to identify the **actual project ID** from its local project configuration (not the folder name). It should never invent the ID. Then run:

```bash
cd "$HOME/Projects/WOW-Dashboard-Gemini"
python3 scripts/wow_gemini_sidecar.py --project-id 'ACTUAL_PROJECT_ID'
python3 scripts/wow_gemini_sidecar.py --install --project-id 'ACTUAL_PROJECT_ID'
python3 scripts/wow_gemini_sidecar.py --status
```

The first invocation is **preview-only**. The second explicitly opts in and edits only the Sidecar manifest and its single enablement entry, preserving other JSON settings. The final command reports the saved configuration, not actual scheduled execution. **Replace ACTUAL_PROJECT_ID with the real value; the placeholder intentionally fails closed.**

Default schedule: once daily at 08:17 in the Mac's local timezone. Adjust the first installation with `--cron '17 8 * * 1-5'` for weekdays only. If the Sidecar already exists with a different schedule, the installer will stop rather than silently overwrite it.

After installation, **verify in Antigravity** that the Sidecar is enabled for the intended project, then inspect the native runtime directory after its next scheduled fire: `~/.gemini/antigravity/sidecar_data/wow-amrt-daily-recovery/` (logs and events). Require an actual new agent conversation and single-incident evidence handoff before claiming `RUNNING`.

Each recurring run may diagnose, edit an isolated local worktree, and test. It may only update an existing GitHub PR via the authenticated Engineering App writer process once the real current owner and writer lease are verified. No competing dispatcher, incident DB, PR, model authority, reviewer, or release system is permitted. Keep usage economical by starting once per day; observe actual quota and cost before increasing frequency.

## Unattended cloud engineering when the Mac is off

A local Google AI Pro sign-in does not activate a headless service and does not automatically pay for Gemini API usage. Genuine 24/7 execution requires a separately configured **supported Gemini API or Vertex** identity and a new optional provider in the **existing** `.github/workflows/wow-v17-engineering-provider-dispatcher.yml`, not another polling workflow. Preserve existing `auto/openai/anthropic` behavior, Conductor issue assignment and leases, single-writer ownership, exact-SHA CI and independent QA/Release. Add typed quota/auth/429 errors, bounded retries, secrets isolation and explicit budget controls *before* enabling. The local Sidecar integration does **not** do this.

## Acceptance gates

- Repository installer unit tests verify no writes on preview, idempotence, preservation of existing settings, malformed JSON rejection and cross-project protection.
- Protected GitHub checks are green at the new PR exact SHA.
- Mac sidecar installation is confirmed with `--status` **and a real native event/new conversation**.
- First autonomous task yields a reproducible repair and tested handoff, while `can_execute=false`.
- Do not claim production restoration without authenticated integrated SHA, live Render/Action run, production persisted readback, and independent evidence.

Official docs: https://antigravity.google/docs/commands/goal, https://antigravity.google/docs/sidecars, https://antigravity.google/docs/commands/schedule, https://geminicli.com/docs/get-started/authentication/
