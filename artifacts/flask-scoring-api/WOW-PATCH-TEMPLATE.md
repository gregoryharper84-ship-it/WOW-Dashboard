# WOW Patch Template

**How to use:**
Copy this file, fill in every section, then share it with the Replit agent (or any
planning thread) _before_ any code is written. A patch that cannot answer all five
sections clearly is not ready to build.

The live spec is `LLP_GROUND_TRUTH.md` in this directory. That file, not memory or
a stale project file, is the conflict-check target. Code in `app.py` is the
implementation truth; the ground-truth doc is a snapshot of it. If they disagree,
the code wins and the doc needs updating.

---

## Patch ID
`WOW-PATCH-XXX` — increment from the last patch in the log below.

## Author / date
_Who proposed this and when (YYYY-MM-DD)._

## Status
`DRAFT` | `READY` | `BUILDING` | `SHIPPED` | `REJECTED`

---

## 1. Problem statement

_One paragraph. What is wrong or missing today, and what user-visible or
model-visible behavior does this patch change? Be concrete — cite the actual
endpoint, field, gate flag, badge, or decision that is affected._

## 2. Affected spec sections

_List every section in `LLP_GROUND_TRUTH.md` that this patch touches or extends.
If it adds a new section, say so explicitly._

| Section | Change type | Description |
|---------|-------------|-------------|
| §N — Title | ADD / MODIFY / DELETE | What changes |

_If this patch does not touch LLP_GROUND_TRUTH.md at all, state why (e.g.
"pure endpoint addition with no badge/gate/field-contract changes")._

## 3. Exact delta

_The minimal, precise change. For spec fields: old value → new value. For new
gate flags: name, trigger condition, badge/verdict impact. For new endpoints:
route, method, required/optional body fields, response contract. For thresholds:
old number → new number with rationale._

```
Example:
  Section §4 badge ceiling:
  ADD: clv_beat is None → cap at WAIT  (was: not listed)

  Section §7 field contract:
  ADD top-level key: "platoon_splits" (object | null)
```

## 4. Test case

_A concrete pass/fail check that can be run by curl or pytest. At minimum:
input → expected output for the happy path, and at least one failure/edge case._

```bash
# Happy path
curl -X POST http://localhost:80/api/<endpoint> \
  -H "X-API-Key: $SCORING_API_KEY" \
  -H "Content-Type: application/json" \
  -d '<exact JSON body>'

# Expected response (key fields only):
# { "ok": true, "<field>": <expected_value>, ... }

# Edge / failure case
# <describe input> → expected: { "ok": false, "error": "..." }
```

## 5. Conflict check

_Answer each question explicitly. "N/A" is not acceptable — if it does not apply,
say why._

| Question | Answer |
|----------|--------|
| Does this change any existing badge/ceiling rule? | |
| Does this add, rename, or remove a top-level field from §7's field contract? | |
| Does this change the set of hard vs. advisory failure-path tags (§6)? | |
| Does this alter `_llp_decision` logic or its input thresholds (§3)? | |
| Does this change any Odds API market alias or sport-key mapping (§5, §8)? | |
| Does this affect the odds-snapshot cron, snapshot kinds, or CLV grading (§11)? | |
| Does this require a DB migration (new table, new column, new index)? | |
| Does this add a new route that the Express proxy in `scoring-proxy.ts` must forward? | |
| Could gunicorn's 2-worker setup cause a race condition on any shared state this adds? | |

## 6. Ground-truth doc update

_After the patch ships, list the exact lines that must be updated in
`LLP_GROUND_TRUTH.md`. Leave blank until status = SHIPPED._

---

## Patch log

| Patch ID | Date | Status | Summary |
|----------|------|--------|---------|
| _(none yet — first shipped patch goes here)_ | | | |
