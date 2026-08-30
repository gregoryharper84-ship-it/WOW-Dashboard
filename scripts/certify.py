#!/usr/bin/env python3
"""
WOW closeout certification script.

Calls the Anthropic API directly (NOT the Claude Code agent tool loop) to
play the "Claude Chat / independent reviewer" role: given a PR diff and its
return packet, it evaluates the implementation against acceptance criteria
and produces a structured verdict.

This is deliberately a separate, simpler API call rather than another
agentic Claude Code run — the reviewer should not have write tools, should
not be able to "fix" what it's reviewing, and should be cheap/fast enough
to run on every push to a closeout PR.

Verdict is written to certification-result.json and a human-readable
summary to certification-summary.md. Exit code is nonzero on REJECT or
NEEDS_CHANGES so the workflow step can fail the check — but merging still
requires a human, regardless of exit code. This script only ever produces
a recommendation, never a merge action.

Required env vars:
  ANTHROPIC_API_KEY      API key
  PR_DIFF_FILE           path to a file containing `git diff` output
  RETURN_PACKET_FILE     path to the implementation's return packet (md)
  PACKET_SPEC_FILE       path to the original packet spec that was sent
                         to the implementer (acceptance criteria source)

Optional:
  CERT_MODEL             model id, default "claude-opus-5"
                         (use a stronger/more careful model for review
                         than for implementation where practical)
"""

import json
import os
import sys
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("CERT_MODEL", "claude-opus-5")

SYSTEM_PROMPT = """You are the independent certification reviewer for WOW \
closeout packages. You did not implement this change. Your job is to \
determine whether the implementation actually satisfies the acceptance \
criteria in the original packet spec -- not whether the return packet's \
own claims sound convincing.

Apply these rules strictly, matching WOW's existing governance principles:

1. NO SELF-CERTIFICATION. A claim of "PASSED" in the return packet is not \
evidence. Only the actual diff, test output, and reconnaissance evidence \
in front of you count as evidence.
2. FAIL CLOSED. If evidence for a claim is missing, ambiguous, or you \
cannot verify it from what's provided, treat that specific claim as \
UNVERIFIED, not as passing by default.
3. Check for the specific failure modes WOW has hit before: narrative \
claims used as substitutes for real evidence, a helper-function-only test \
standing in for an end-to-end/route-level test, a "PASS" that only covers \
part of the stated scope, silently weakening a gate/threshold instead of \
fixing the underlying gap, and duplicate/legacy code paths left reachable \
alongside a new canonical one.
4. Check for §15-shaped issues: did the implementation invent new \
governance semantics, reorder approved label priority, or change a \
qualification threshold, when the packet only authorized implementing \
*existing* approved semantics? If so, this is a REJECT regardless of test \
results, and you should say exactly what exceeded the authorized scope.
5. Distinguish PRE_EXISTING_FAILURE from NEW_REGRESSION if test failures \
are present. Do not let "some tests already failed before this change" \
excuse a real regression, and do not penalize the PR for pre-existing \
issues outside its scope.

Return your verdict as a single JSON object matching this schema exactly, \
with no text before or after it:

{
  "verdict": "PASS" | "NEEDS_CHANGES" | "REJECT",
  "acceptance_criteria_checked": [
    {"criterion": "...", "status": "PASS" | "FAIL" | "UNVERIFIED", "evidence": "..."}
  ],
  "scope_violations": ["..."],
  "false_or_unsupported_claims": ["..."],
  "missing_evidence": ["..."],
  "regression_risk": "NONE" | "LOW" | "MEDIUM" | "HIGH",
  "summary": "2-4 sentences, plain language, for a human who has not read the diff",
  "required_before_merge": ["..."]
}

verdict rules:
- PASS only if every stated acceptance criterion has verifiable PASS \
evidence, no scope violations, and no unresolved false claims.
- NEEDS_CHANGES if the direction is right but specific gaps remain -- \
list them concretely in required_before_merge.
- REJECT if there's a scope violation (especially §15-shaped), a false \
claim of passing tests that didn't run, or evidence that a gate was \
weakened rather than satisfied.
"""


def read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def call_anthropic(system: str, user_content: str) -> str:
    api_key = os.environ["ANTHROPIC_API_KEY"]
    payload = {
        "model": MODEL,
        "max_tokens": 4000,
        "system": system,
        "messages": [{"role": "user", "content": user_content}],
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    return "\n".join(text_blocks)


def main() -> int:
    packet_spec = read_file(os.environ["PACKET_SPEC_FILE"])
    return_packet = read_file(os.environ["RETURN_PACKET_FILE"])
    diff = read_file(os.environ["PR_DIFF_FILE"])

    # Defensive truncation -- keep the review focused and bounded.
    diff = diff[:150_000]

    user_content = f"""## Original packet spec (acceptance criteria source)

{packet_spec}

## Implementer's return packet

{return_packet}

## Actual diff

```diff
{diff}
```

Evaluate and return the JSON verdict now."""

    raw = call_anthropic(SYSTEM_PROMPT, user_content)

    try:
        # Model is instructed to return bare JSON; strip fences defensively.
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        verdict = json.loads(cleaned)
    except (json.JSONDecodeError, IndexError) as e:
        # Fail closed: an unparseable review is NOT a pass.
        verdict = {
            "verdict": "NEEDS_CHANGES",
            "acceptance_criteria_checked": [],
            "scope_violations": [],
            "false_or_unsupported_claims": [],
            "missing_evidence": [f"Certification response was not valid JSON: {e}"],
            "regression_risk": "MEDIUM",
            "summary": "Certification could not be parsed. Treating as NEEDS_CHANGES "
                       "pending manual review. Raw model output saved separately.",
            "required_before_merge": ["Manual review required -- automated certification failed to parse."],
        }
        with open("certification-raw-output.txt", "w", encoding="utf-8") as f:
            f.write(raw)

    with open("certification-result.json", "w", encoding="utf-8") as f:
        json.dump(verdict, f, indent=2)

    with open("certification-summary.md", "w", encoding="utf-8") as f:
        f.write(f"## WOW Closeout Certification: {verdict['verdict']}\n\n")
        f.write(f"{verdict['summary']}\n\n")
        if verdict.get("scope_violations"):
            f.write("### Scope violations\n")
            for v in verdict["scope_violations"]:
                f.write(f"- {v}\n")
            f.write("\n")
        if verdict.get("acceptance_criteria_checked"):
            f.write("### Acceptance criteria\n")
            for c in verdict["acceptance_criteria_checked"]:
                f.write(f"- **{c['status']}** — {c['criterion']}: {c.get('evidence', '')}\n")
            f.write("\n")
        if verdict.get("required_before_merge"):
            f.write("### Required before merge\n")
            for r in verdict["required_before_merge"]:
                f.write(f"- {r}\n")
        f.write("\n---\n**Reminder: this verdict is a recommendation. "
                "A human must still review and approve the merge.**\n")

    print(f"Verdict: {verdict['verdict']}")
    if verdict["verdict"] != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
