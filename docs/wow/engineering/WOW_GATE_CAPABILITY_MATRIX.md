# WOW gate-capability matrix

Generated from `artifacts/wow-engine/v17/gate_capability_matrix.py`. Do not edit
this file by hand: `test_v17_gate_capability_matrix.py` fails when it drifts from
the module.

Answer one question before spending CI or dispatching a reviewer: **can this gate
ever pass for this change?** When it cannot, the row names the typed reason and the
authorized route that resolves it. A structurally impossible gate is
`POLICY_UNREACHABLE` with a named actor, never a retry. Nothing here merges,
dispatches or bypasses: `can_execute=false`.

Only `ruleset_live` checks are tagged as pinned CI. This static source cannot
revalidate live GitHub settings on each invocation or prove release authority.
Protected semantic changes still require independent security evidence under policy.
`merge_blocked=false` is never merge authorization, and Engineering App copying
source bytes does not establish independent authorship or independent review.
Proposed checks appear in `ruleset-main.proposed.json`; other checks
are advisory unless separately established as policy obligations.

| Gate | Published check | Requirement | Impossible for | Typed reason | Recovery class | Authorized route |
| --- | --- | --- | --- | --- | --- | --- |
| `required_three_regression` | WOW required-three regression | ruleset_live | - | - | - | no structural impossibility declared |
| `governed_probability_backend` | WOW governed probability backend | ruleset_live | - | - | - | no structural impossibility declared |
| `additional_required_regression` | WOW additional required regression | ruleset_live | - | - | - | no structural impossibility declared |
| `independent_qa` | WOW Independent QA exact-head | ruleset_proposed | - | - | - | no structural impossibility declared |
| `release_authority` | WOW Release Authority exact-head | ruleset_proposed | - | - | - | no structural impossibility declared |
| `semantic_review` | WOW Independent Security Review | advisory | `provider_unavailable` | `PROVIDER_MODEL_UNAVAILABLE` | `AUTH_OR_CONFIGURATION` | REPAIR_PROVIDER_CREDENTIAL_OR_ROUTE (`OWNER`) |
| `semantic_review` | WOW Independent Security Review | advisory | `unchanged_head_after_reject` | `PREVIOUS_SEMANTIC_REJECT_SAME_SHA` | `PROVEN_REJECT` | NEW_HEAD_OR_NEW_EVIDENCE_REQUIRED (`OWNER`) |
| `owner_fallback` | (third stage; publishes no check) | advisory | `owner_authored_trust_root` | `OWNER_IS_SOURCE_AUTHOR_OR_IDENTITY_UNVERIFIED` | `POLICY_UNREACHABLE` | INDEPENDENT_SOURCE_ADOPTION_WITH_VERIFIED_WRITER_LEASE (`INDEPENDENT_ENGINEERING_AUTHOR`) |
| `existing_pr_governance` | Existing PR trusted governance certification | advisory | `owner_authored_trust_root` | `QA_TRUST_ROOT_OWNER_AUTHORED` | `POLICY_UNREACHABLE` | INDEPENDENT_SOURCE_ADOPTION_WITH_VERIFIED_WRITER_LEASE (`INDEPENDENT_ENGINEERING_AUTHOR`) |

## Reachability by change class

| Change class | Owner-authored trust root | Merge blocked by a structural impasse |
| --- | --- | --- |
| `A` | no | no |
| `A` | yes | no |
| `D` | no | no |
| `D` | yes | no |
| `S` | no | no |
| `S` | yes | no |
| `C` | no | no |
| `C` | yes | no |
