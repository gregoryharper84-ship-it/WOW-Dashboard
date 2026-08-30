# WOW Closeout Autonomy Loop

Automates the *mechanics* of the Discovery → Implement → Certify loop.
Deliberately does **not** automate the judgment calls — those stay human
or §15-gated, matching WOW's "no self-certification" principle.

## The loop

```
1. wow-discovery.yml       (manual trigger, read-only)
   → Claude Code runs a packet spec with NO write tools
   → evidence posted as a GitHub issue + artifact
   → if §15 markers found: issue is flagged, nothing proceeds automatically

2. [HUMAN] reviews the discovery issue, decides whether/how to scope
   the next closeout package. This step is NOT automated.

3. wow-closeout-implement.yml   (manual trigger, full write access)
   → Claude Code implements an APPROVED packet
   → opens a PR labeled `wow-closeout`, `needs-human-review`, `do-not-merge`
   → if §15 markers found during implementation: PR is flagged, incomplete

4. wow-certify.yml         (automatic on any `wow-closeout` PR push)
   → calls the Anthropic API directly (not another agentic Claude Code
     run) to independently review the diff against the packet's
     acceptance criteria
   → posts verdict as a PR review (PASS = comment, else = request changes)
   → sets the `wow-certification` check, which branch protection can
     require before merge is even allowed

5. [HUMAN] merges. Always. A PASS verdict does not merge anything by
   itself — it only removes one blocker. Branch protection should
   require BOTH the certification check AND a human review approval.
```

## Setup

1. **Secrets** (repo settings → Secrets and variables → Actions):
   - `ANTHROPIC_API_KEY` — used by both the Claude Code action and
     `scripts/certify.py`.

2. **Branch protection on `main`**:
   - Require status checks to pass before merging: `wow-certification`
     (and your existing CI).
   - Require at least 1 human review approval — do this *in addition to*
     the certification check, not instead of it.
   - Do not allow the workflow's own bot commits to count as review
     approval.

3. **`anthropics/claude-code-action@v1`** — **verified 2026-08-30** against
   the action's own `action.yml`, `docs/usage.md`, and `docs/configuration.md`.
   The action name/version tag is correct as written. However, an earlier
   draft of the two Claude-Code-Action workflow steps used four inputs
   that **do not exist** on this action (`allowed_tools`, `disallowed_tools`,
   `max_turns`, `prompt_file`, `output_file`) — passing them would have
   caused an immediate "Unexpected input(s)" failure on the very first run.
   That has been fixed in `wow-discovery.yml` and `wow-closeout-implement.yml`:
   - Tool restriction and turn limit now go through `claude_args`, e.g.
     `claude_args: | --allowedTools "Read,Grep,..." --disallowedTools "Edit,Write,..." --max-turns 60`.
   - `prompt` takes prompt text directly (no `prompt_file`); the packet
     file's contents are loaded into a step output and passed via
     `prompt: ${{ steps.packet.outputs.content }}`.
   - There is no `output_file` input. `wow-closeout-implement.yml` instructs
     Claude (which has Write access there) to write `return-packet.md`
     itself as its last action. `wow-discovery.yml` (read-only, no Write
     tool) instead copies the action's own `execution_file` output.
     **`execution_file`'s exact internal format was not independently
     verified beyond "contains Claude's response text"** — spot-check the
     first real discovery run's uploaded artifact to confirm the STOP_15
     grep and issue body come out as expected, and adjust if the format
     turns out to need extraction (e.g. if it's a structured transcript
     rather than plain final-message text).
   - This still leaves the `allowedTools`/`disallowedTools` string syntax
     itself (`Bash(git log:*)` etc.) as the actual read-only enforcement —
     confirm it continues to behave as expected on first run; that part
     was not independently re-verified beyond matching the action's own
     documented examples.

4. **Model IDs** — `wow-closeout-implement.yml` uses whatever model the
   Claude Code action defaults to; `scripts/certify.py` defaults to
   `claude-opus-5` via `CERT_MODEL`, on the theory that the reviewer
   should be at least as capable as the implementer. `claude-opus-5` is
   confirmed current as of 2026-08-30.

## Why the human/§15 gates aren't automated

- **§15 stop conditions** (governance change, unclear authority,
  superseded/stale phase reference) are, by definition, situations an
  automated loop can't resolve — it can only detect and stop. Both
  workflows grep for a literal `STOP_15:` marker the model is instructed
  to emit, and hard-flag the issue/PR rather than continuing.
- **ChatGPT ratification** isn't wired in here at all. If a package
  requires strategy/patch ratification, that has to happen outside this
  loop before the packet is marked "approved" and sent to
  `wow-closeout-implement.yml`.
- **Merge to main** stays human even on a PASS verdict. The certification
  step produces a recommendation with cited evidence, not an authority
  to act on it.
- **Anything touching `can_execute`, staking, or live execution settings**
  isn't scoped into these packets at all — that's a design constraint on
  the packet content, not something these workflows enforce, so keep
  writing packets the way `V16-CLOSEOUT-DISCOVERY-00.md` is written:
  explicit non-goals, explicit governance preservation.

## What's still manual by design

- Writing new packet specs (discovery scope, closeout package scope).
- Reading discovery evidence and deciding what CLOSEOUT-NN packages to
  create from it.
- Any §15-flagged resolution.
- ChatGPT ratification of strategy/patches.
- The merge button.

## Files

- `governance/packets/V16-CLOSEOUT-DISCOVERY-00.md` — the first packet,
  ready to run as-is.
- `.github/workflows/wow-discovery.yml` — read-only reconnaissance runner.
- `.github/workflows/wow-closeout-implement.yml` — full-access implementer,
  manual-dispatch only.
- `.github/workflows/wow-certify.yml` — automatic independent certification
  on any `wow-closeout`-labeled PR.
- `scripts/certify.py` — the certification logic (direct API call, no
  agentic tools, fails closed on unparseable output).
