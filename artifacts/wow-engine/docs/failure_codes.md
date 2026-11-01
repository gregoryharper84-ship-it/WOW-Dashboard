# WOW V17 Canonical Failure-Code Registry

This file is the repository source of truth for governed typed failure/status codes introduced or relied on by the V17 full-board stabilization work. New typed failure codes must be registered in the same change that introduces them.

`can_execute=false` remains invariant. Market/evidence failures never imply a sporting-model capability failure.

Historical/retired QA finding: `QA_TRUST_ROOT_OWNER_AUTHORED` remains preserved in pre-V3 receipts, but is no longer emitted by V3 QA. It is intentionally excluded from the active emitted-code equality table below.

| Code | Owning lane/stage | Meaning | Rank eligible? |
|---|---|---|---:|
| `BASKETBALL_CALIBRATION_HOLDOUT_OVERLAP` | NBA/WNBA shadow fitter | Candidate artifact training counts/date boundary fail to partition the raw feature corpus into disjoint calibration and untouched holdout sets. Reject the fit and retain nonpublishable state. | No |
| `BASKETBALL_CALIBRATED_HOLDOUT_INVALID` | NBA/WNBA shadow fitter | The reserved postcalibration holdout cannot produce valid finite numerical probabilities or binary outcomes. Preserve the scorer/data-integrity failure and do not certify. | No |
| `NCAAF_FEATURE_PAGINATION_INVALID` | NCAAF pregame training feature compiler | A stable primary-key-paginated training/evidence page contains a missing or duplicated source-row ID. Reject the entire partial compilation; do not fit on corrupted/incomplete evidence. This is an input-integrity failure, not `MODEL_UNAVAILABLE`. | No |
| `DISCOVERY_MAIN_SHA_INVALID_HOLD` | Engineering App source discovery | The caller supplied a malformed purported current protected-main SHA; reject before any GitHub source inventory or writer admission. | No |
| `SEMANTIC_FINDINGS_INVALID` | independent semantic reviewer / response validation | Untrusted verdict has missing/wrong findings array or more than 12 items (`:NOT_LIST`, `:TOO_MANY`); a finding is not an object, has the wrong exact key set, invalid severity/category, or malformed file/detail/evidence (`:NOT_OBJECT`, `:FIELD_SET`, `:SEVERITY`, `:CATEGORY`, and `:TEXT_{FILE,DETAIL,EVIDENCE}_{TYPE,EMPTY,TOO_LONG}`). Historical pre-patch receipts may retain `:TEXT`. The nine new suffixes identify the field and condition without emitting untrusted text. The 500-character bound remains unchanged. All are allowlisted reasons with no provider body or secrets. **EVIDENCE_PENDING**; preserve authenticated attempt identity, publish HOLD, inspect source/schema/provider preflight and produce a new reviewed source head if repair is required. A malformed response is never an automatic Gemini failover, retry, PASS, or review approval. | No |
| `CLAUDE_REVIEW_TRUNCATED` | independent semantic reviewer / Claude response | A single Anthropic request with a 6144-token output budget returned explicit `max_tokens` without the required structured-tool verdict. No replay of the untrusted diff or same-head reviewer shopping is permitted. `EVIDENCE_PENDING`; QA App publishes HOLD, reviewer operations investigates output length/model health, never fails over, never self-approves or reattempts solely for a friendlier decision. | No |
| `CLAUDE_REVIEW_REFUSED` | independent semantic reviewer / Claude response | Provider returned an explicit `refusal` stop reason rather than a typed verdict. `EVIDENCE_PENDING` policy HOLD (not a substantive security REJECT): no retry, no fallback and no PASS; escalate genuine provider refusal to an authenticated independent Security/Architect. No response text logged. | No |
| `CLAUDE_REVIEW_INCOMPLETE` | independent semantic reviewer / Claude response | Provider ended without required authenticated tool output for a non-truncation stop reason (including `end_turn`, `stop_sequence`, `pause_turn`, unknown). `EVIDENCE_PENDING`; QA App HOLD and security owner investigates allowlisted stop-reason metadata. Do not retry or fallback on such ambiguity. | No |
| `CLAUDE_RESPONSE_INVALID` | independent semantic reviewer / Claude tool transport | Provider message or content is not a valid dict/list container for exact-one `submit_review` tool payload. `REPAIRABLE_CODE` only after source/transport diagnosis, never fallback or PASS. | No |
| `CLAUDE_TOOL_USE_MISSING_OR_DUPLICATE` | independent semantic reviewer / Claude tool contract | `tool_use` stop reason but not exactly one named `submit_review` result. `EVIDENCE_PENDING`; preserve HOLD and inspect the code-level tool schema, not the raw response. No retry or alternate reviewer shopping. | No |
| `MODEL_UNAVAILABLE` | model capability | Exact required fitted specialist/artifact/adapter is absent for the route. Never use for data, market, scorer, repository, or editor failures. | No |
| `MODEL_INPUTS_INSUFFICIENT` | model readiness | Fitted capability exists but required candidate-specific inputs are missing/insufficient. Includes a missing/invalid required calibration artifact for an otherwise available multisport scorer. | No |
| `TEAM_EVENT_EVIDENCE_PREFLIGHT_FAILED` | LLP team/event official publication evidence preflight | Opted-in backend scorer did not prove current two-participant/field identity, event-effective personnel, feature consumption, required module execution or pregame freshness; preserve the completed sporting probability for diagnosis, but prohibit official ranking. Nested audit blockers identify the specific evidence failure. This is not `MODEL_UNAVAILABLE`. | No |
| `MODEL_SCORER_FAILED` | model invocation | Selected model was invoked but threw, timed out, transport-failed, or returned no valid completion. | No |
| `PROP_MODEL_REGISTRY_UNAVAILABLE` | prop exact-route artifact registry | The `wow_prop_certified_model_artifact` registry RPC could not be called (transport/auth/service failure). Infrastructure HOLD (`terminal_label=REGISTRY_INFRASTRUCTURE_BLOCKED`, `terminal_cause=INFRASTRUCTURE`) on `/score-pick-request` and HTTP 503 on `/score-prop`; never rewritten to `MODEL_UNAVAILABLE`, since it proves nothing about capability absence. Regression: `tests/test_v17_prop_registry_infra_typed_failure.py`. | No |
| `PROP_MODEL_REGISTRY_INVALID_RESPONSE` | prop exact-route artifact registry | The registry RPC returned an unusable response shape. Same handling as `PROP_MODEL_REGISTRY_UNAVAILABLE`. | No |
| `MODEL_OUTPUT_INVALID` | model validation | Selected model returned malformed, non-numeric, schema-invalid, impossible, or non-normalized output. | No |
| `MODEL_RESOLUTION_FAILED` | cross-sport model registry lookup | Registry resolver raised before a controlling specialist could be selected. Retain the row as `OTHER_GOVERNED_HOLD`; absence of a fitted model is not established. | No |
| `DISCOVERED_ROW_SCORER_EXCEPTION` | cross-sport row scoring | Untyped exception escaped the selected row scorer; row terminates as `MODEL_SCORER_FAILED` without aborting remaining events. | No |
| `DISCOVERED_ROW_SCORER_NO_COMPLETION` | cross-sport row scoring | Selected row scorer returned `None` or an empty mapping; model-stage completion absent, recorded as `MODEL_SCORER_FAILED`. | No |
| `DISCOVERED_ROW_SCORER_RESPONSE_NOT_MAPPING` | cross-sport model output validation | Selected row scorer returned a non-mapping object, recorded as `MODEL_OUTPUT_INVALID`. | No |
| `DAILY_ROW_DETAIL_PERSISTED_SET_MISMATCH` | Daily full-row persistence readback | Supabase upsert returned without exception but readback indices did not exactly match every expected `(run_id,row_index)`; detail availability is held. | No |
| `PERSISTENCE_RECONCILIATION_FAILED` | Daily full-row persistence readback | Terminal state of the full-row detail persistence adapter when exact-index readback is incomplete, duplicated, or mismatched. | No |
| `EVENT_ALREADY_STARTED` | slate/final refresh | Pregame candidate has started or completed and cannot remain on a pregame leaderboard. | No |
| `MARKET_DATA_UNOBTAINABLE` | market/evidence | Required market evidence could not be acquired. Does not erase an already-completed sporting probability. | Sporting probability may remain eligible; market/value lane blocked |
| `AUTH_FAILED` | market acquisition | Provider rejected the configured credential (for example HTTP 401/403). Independent from `model_status`. | Does not decide sporting rank |
| `CREDENTIAL_UNCONFIGURED` | market acquisition | No credential is configured for a provider that requires one. | Does not decide sporting rank |
| `RATE_LIMITED` | market acquisition | Provider refused the request due to rate/quota limits. | Does not decide sporting rank |
| `IDENTITY_UNRESOLVED` | event identity | Official canonical event identity has not been resolved. Provider IDs remain aliases only. | No |
| `OFFICIAL_EVENT_ID_REQUIRED_FOR_CANONICALIZATION` | event identity | Canonical event key cannot be constructed because authoritative official event identity is absent. | No |
| `RUN_INVALID_EVIDENCE_BINDING` | producer→consumer evidence handoff | A required field is demonstrably populated in the controlling sporting package/canonical evidence but downstream governance reports that same field as missing/not called. The run is invalid rather than an ordinary no-pick; the completed sporting package is preserved for diagnosis but cannot rank/publish. | No |
| `DISCOVERED_ROW_MISSING_TERMINAL_DISPOSITION` | row reconciliation | A discovered candidate reached publication without exactly one terminal disposition. The row must remain visible and the run is incomplete. | No |
| `FULL_BOARD_DISCOVERY_ROW_MISSING_CANDIDATE_ID` | row reconciliation | Discovery emitted a row without a stable candidate identity. | No |
| `FULL_BOARD_FINAL_ROW_MISSING_CANDIDATE_ID` | row reconciliation | Final-stage output cannot be mapped back to a discovered candidate. | No |
| `PROBABILITY_PACKAGE_NOT_VALID` | publication chain | Governing numeric probability package validation has not passed. | No |
| `DYNAMIC_CALIBRATION_NOT_COMPLETE` | publication chain | Governed dynamic calibration has not completed. | No |
| `PROBABILITY_AUDIT_NOT_COMPLETE` | publication chain | Probability audit has not passed after model/calibration completion. | No |
| `EVENT_GOVERNOR_NOT_COMPLETE` | publication chain | Event-governance/mutex stage has not passed. | No |
| `CALIBRATED_PROBABILITY_OR_LOWER_BOUND_MISSING` | publication chain | Required governed calibrated probability/bound fields are absent. | No |
| `FINAL_REFRESH_NOT_COMPLETE` | publication chain | Final pre-publication status/freshness refresh has not passed. | No |
| `TERMINAL_AUTHORITY_OR_EXECUTION_INVARIANT_NOT_PROVEN` | terminal reduction | V17 terminal authority or `can_execute=false` invariant is not proven. | No |
| `RANK_ELIGIBLE_OR_PROBABILITY_PUBLISHABLE_NOT_PROVEN` | publication chain | All diagnostic stages may be present, but final governed rank/publication proof is absent. | No |
| `CALIBRATION_ARTIFACT_INVALID_OR_UNAVAILABLE` | multisport calibration | WNBA/NHL/soccer/tennis/MMA raw sporting model exists, but its required fitted calibration artifact is missing, malformed, unhealthy, uncertified, wrong-sport, or wrong-model. Surfaced as `MODEL_INPUTS_INSUFFICIENT`. | No |
| `CALIBRATION_HISTORY_NOT_PROVEN` | multisport terminal calibration | A raw/provisional model package reached terminal governance without proof of historical calibration. | No |
| `CALIBRATION_ARTIFACT_NOT_CERTIFIED` | multisport terminal calibration | Calibration artifact has not proven the required certification status. | No |
| `CALIBRATION_HEALTH_NOT_PASS` | multisport terminal calibration | Fitted calibrator health is not PASS. | No |
| `CALIBRATION_STATUS_NOT_PASS` | multisport terminal calibration | Candidate calibration stage did not complete successfully. | No |
| `CALIBRATION_HISTORY_SAMPLE_INSUFFICIENT` | multisport terminal calibration | Historical calibration sample is below the lane minimum required by the artifact contract. | No |
| `CALIBRATION_ARTIFACT_FINGERPRINT_INVALID` | multisport terminal calibration | Immutable calibration artifact fingerprint is missing or invalid. | No |
| `CALIBRATION_FIT_END_INVALID_OR_FUTURE_LEAKAGE` | multisport terminal calibration | Calibration fit cutoff is invalid or occurs after the immutable model timestamp. | No |
| `CALIBRATION_MODEL_FAMILY_MISMATCH` | multisport calibration | Calibration artifact targets a different model family than the controlling specialist package. Surfaced as `MODEL_INPUTS_INSUFFICIENT`. | No |
| `CALIBRATION_MODEL_VERSION_MISMATCH` | multisport calibration | Calibration artifact targets a different model version than the controlling specialist package. Surfaced as `MODEL_INPUTS_INSUFFICIENT`. | No |
| `TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED` | fitted team/event certification | No current immutable certification receipt proves the exact sport/league fitted artifact. A registered/importable bridge or caller-supplied calibrator is not sufficient. | No |
| `TEAM_EVENT_SPECIALIST_CERTIFICATION_REVOKED` | fitted team/event certification | The latest immutable certification receipt explicitly revokes the prior sport/league specialist certification. | No |
| `TEAM_EVENT_CERTIFICATION_REGISTRY_UNAVAILABLE` | fitted team/event certification registry | The service-role certification-registry RPC could not be read. Infrastructure/registry failure; never rewrite to `MODEL_UNAVAILABLE`. | No |
| `TEAM_EVENT_CERTIFICATION_REGISTRY_INVALID_RESPONSE` | fitted team/event certification registry | The certification registry returned an unusable response shape. | No |
| `TEAM_EVENT_INDEPENDENT_VERIFICATION_NOT_PASS` | fitted team/event independent verification | The exact fitted artifact/calibration pair has not passed the required independent numerical verification. | No |
| `TEAM_EVENT_SPECIALIST_NOT_CERTIFIED` | fitted team/event certification | A proof record exists, but its state is not `CERTIFIED`. | No |
| `TEAM_EVENT_PROBABILITY_PACKAGE_INVALID` | common fitted-specialist package | The common sport-specialist probability envelope is not an object. | No |
| `TEAM_EVENT_PROBABILITY_PACKAGE_NOT_VALID` | common fitted-specialist package | The controlling specialist did not mark its governed package valid after sport-specific validation. | No |
| `TEAM_EVENT_CERTIFICATION_SPORT_MISMATCH` | common fitted-specialist package | Package sport does not match the immutable certification receipt. | No |
| `TEAM_EVENT_CERTIFICATION_SPECIALIST_MISMATCH` | common fitted-specialist package | Package controlling-specialist identity does not match certification. | No |
| `TEAM_EVENT_CERTIFICATION_MODEL_VERSION_MISMATCH` | common fitted-specialist package | Package model version differs from the certified model version. | No |
| `TEAM_EVENT_CERTIFICATION_ARTIFACT_MISMATCH` | common fitted-specialist package | Package artifact identity differs from the certified artifact. | No |
| `TEAM_EVENT_CERTIFICATION_CALIBRATION_MISMATCH` | common fitted-specialist package | Package calibration method differs from the certified calibration contract. | No |
| `RAW_PROBABILITY_INVALID` | common fitted-specialist package | Raw probability is missing, non-numeric, non-finite, or outside `[0,1]`. | No |
| `CALIBRATED_PROBABILITY_INVALID` | common fitted-specialist package | Calibrated probability is missing, non-numeric, non-finite, or outside `[0,1]`. | No |
| `CALIBRATED_LOWER_BOUND_INVALID` | common fitted-specialist package | Governed lower bound is missing, non-numeric, non-finite, or outside `[0,1]`. | No |
| `CALIBRATED_UPPER_BOUND_INVALID` | common fitted-specialist package | Governed upper bound is missing, non-numeric, non-finite, or outside `[0,1]`. | No |
| `CALIBRATED_BOUNDS_ORDER_INVALID` | common fitted-specialist package | Bounds violate `lower_bound <= calibrated_probability <= upper_bound`. | No |
| `MODEL_TIMESTAMP_INVALID` | common fitted-specialist freshness | Model timestamp is missing, malformed, or timezone-naive. | No |
| `LATEST_MATERIAL_UPDATE_TIMESTAMP_INVALID` | common fitted-specialist freshness | Latest-material-update timestamp is missing, malformed, or timezone-naive. | No |
| `MODEL_STALE_AFTER_MATERIAL_UPDATE` | common fitted-specialist freshness | `model_timestamp < latest_material_update_timestamp`; exact rerun required before publication. | No |
| `CAN_EXECUTE_MUST_BE_FALSE` | global safety | Certification/package attempted to set `can_execute` to anything other than false. | No |
| `RUNDOWN_SPORT_ID_UNRESOLVED` | TheRundown acquisition | Catalog access succeeded but the target sport could not be resolved to the provider sport ID. | Does not decide sporting rank |
| `CATALOG_ACCESS_REQUIRED_FIRST` | TheRundown health | Strict provider health did not attempt event access because catalog authentication/access failed first. | Does not decide sporting rank |
| `DATE_MUST_BE_YYYY_MM_DD` | diagnostic API | Invalid date supplied to a bounded health/discovery diagnostic route. | N/A |
| `INVALID_PAGINATION` | diagnostic API | Page/page-size request is invalid. | N/A |

## Multisport calibration artifact detail blockers

The following detail blockers remain underneath the registered `MODEL_INPUTS_INSUFFICIENT` / `CALIBRATION_ARTIFACT_INVALID_OR_UNAVAILABLE` class and must never be rewritten to `MODEL_UNAVAILABLE`: `CALIBRATION_ARTIFACT_MISSING`, `CALIBRATION_ARTIFACT_SPORT_MISMATCH`, `CALIBRATION_CERTIFICATION_NOT_PASS`, `CALIBRATION_TRAINING_N_INSUFFICIENT`, `CALIBRATION_METHOD_MISSING`, `CALIBRATION_VERSION_MISSING`, `CALIBRATION_SOURCE_DATA_HASH_INVALID`, `CALIBRATION_SPLIT_HASH_INVALID`, `CALIBRATION_FIT_END_INVALID`, `CALIBRATION_BRIER_INVALID`, `CALIBRATION_ERROR_INVALID`, `BINARY_CALIBRATION_ARTIFACT_TYPE_INVALID`, `MULTICLASS_CALIBRATION_ARTIFACT_TYPE_INVALID`, `PLATT_COEFFICIENTS_INVALID`, `CALIBRATION_RESIDUAL_QUANTILE_INVALID`, and the outcome-specific soccer calibration record/coefficient/residual blockers.

## Fitted team/event certification detail blockers

The following proof-validation details remain underneath `TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED`, `TEAM_EVENT_INDEPENDENT_VERIFICATION_NOT_PASS`, or the registered common package class and must not be rewritten to `MODEL_UNAVAILABLE`: `TEAM_EVENT_CERTIFICATION_INVALID`, `TEAM_EVENT_CERTIFICATION_FIELD_MISSING`, `TEAM_EVENT_CERTIFICATION_HASH_INVALID`, `TEAM_EVENT_CERTIFICATION_TIMESTAMP_INVALID`, `TEAM_EVENT_CERTIFICATION_CAN_EXECUTE_FORBIDDEN`, `TEAM_EVENT_CERTIFICATION_SPORT_UNSUPPORTED`.

## Existing provider/discovery statuses preserved by V17

The following existing statuses remain authoritative in their owning modules and are intentionally not collapsed into the generic codes above: `NO_CONFIGURED_DISCOVERY_FEED`, `PROVIDER_REQUEST_FAILED`, `PROVIDER_RATE_LIMITED`, `PROVIDER_SCHEMA_FAILURE`, `DISCOVERY_BUDGET_EXHAUSTED`, `EVENT_WRONG_DATE`, `EVENT_STARTED_OR_FINAL`, `EVENT_CANCELLED_OR_POSTPONED`, and `EVENT_IDENTITY_UNRESOLVED`.

## Engineering worker repair delivery codes (incidents #1021, #1527)

Emitted by the protected Claude engineering worker (`wow-v17-claude-engineering-worker.yml`, step *Enforce actionable repair delivery* and *Append incident delivery receipt*) only when the Lead chose `REPAIR`. Legitimate `NO_ACTION` and `VERIFY_RELEASE` never emit them. Every code fails the run (non-success), is persisted to the #1138 heartbeat, the dispatch receipt JSON and an append-only receipt comment on the incident issue, and is classified by `v17/engineering_provider_failover.py` from the emitted `##[error]` line. None affects sporting probability, rank or `can_execute=false`; none grants QA, merge or deploy authority.

| Code | Owning lane/stage | Exact condition | Disposition |
|---|---|---|---|
| `ACTIONABLE_REPAIR_POLICY_BOUNDARY` | engineering governance | Triage risk is `R2-repair-policy` or `R3`; implementation lease denied. Never a provider/implementation failure; never retried or failed over. Re-entry only by protected reviewer/owner authorization change. | `BLOCKED_WITH_EXACT_REASON` |
| `ACTIONABLE_REPAIR_TRIAGE_FAILED` | engineering triage | Triage normalization did not succeed. | `UNRESOLVED_TYPED_FAILURE` |
| `ACTIONABLE_REPAIR_TRIAGE_NOT_REPAIRABLE` | engineering triage | Triage returned `repairable` other than `true`. | `UNRESOLVED_TYPED_FAILURE` |
| `ACTIONABLE_REPAIR_HYPOTHESIS_UNCONFIRMED` | engineering specialist | Specialist hypothesis is neither `CONFIRMED` nor `NARROWED`. | `UNRESOLVED_TYPED_FAILURE` |
| `ACTIONABLE_REPAIR_RISK_UNRECOGNIZED` | engineering triage | Risk class is not `R0`, `R1` or `R2-restorative` and not a policy boundary. | `UNRESOLVED_TYPED_FAILURE` |
| `ACTIONABLE_REPAIR_MUTATION_DENIED` | engineering lease | Mutation safety gate (lease fence, epoch, SAFE_HOLD) did not succeed. | `UNRESOLVED_TYPED_FAILURE` |
| `ACTIONABLE_REPAIR_IMPLEMENTATION_FAILED` | engineering implementation | Implementation agent or its normalization did not succeed. A provider outage is preserved as `provider_signal`. | `UNRESOLVED_TYPED_FAILURE` |
| `ACTIONABLE_REPAIR_NO_DELIVERABLE` | engineering implementation | Implementation ran cleanly but reported no change or no committed branch. | `UNRESOLVED_TYPED_FAILURE` |
| `ACTIONABLE_REPAIR_HEAD_INVALID` | engineering delivery | Implementation head SHA missing or not 40 lowercase hex characters. | `UNRESOLVED_TYPED_FAILURE` |
| `ACTIONABLE_REPAIR_BRANCH_INVALID` | engineering delivery | Implementation branch does not match `claude/engineering/<run>-<attempt>`. | `UNRESOLVED_TYPED_FAILURE` |
| `ACTIONABLE_REPAIR_PR_LOOKUP_FAILED` | engineering delivery | GitHub PR lookup failed (API/outage); delivery could not be verified. | `UNRESOLVED_TYPED_FAILURE` |
| `ACTIONABLE_REPAIR_PR_MISSING` | engineering delivery | No open PR to `main` exists for the implementation branch. | `UNRESOLVED_TYPED_FAILURE` |
| `ACTIONABLE_REPAIR_PR_AMBIGUOUS` | engineering delivery | More than one open PR matches the implementation branch. | `UNRESOLVED_TYPED_FAILURE` |
| `ACTIONABLE_REPAIR_PR_HEAD_MISMATCH` | engineering delivery | The PR head differs from the implementation head SHA. | `UNRESOLVED_TYPED_FAILURE` |
| `ACTIONABLE_REPAIR_WRONG_PR` | engineering delivery | Bound same-branch repair resolved a different open PR number than the original Engine-App target. No substitute PR is authorized. | `UNRESOLVED_TYPED_FAILURE` |
| `ACTIONABLE_REPAIR_DELIVERY_UNVERIFIED` | engineering persistence | `REPAIR` run whose delivery gate produced no code (cancelled/skipped); suffixed with the gate outcome. | `UNRESOLVED_TYPED_FAILURE` |
| `ACTIONABLE_REPAIR_RECEIPT_INCIDENT_INVALID` | engineering persistence | Incident identity is not a numeric issue number; receipt not written. | `UNRESOLVED_TYPED_FAILURE` |
| `ACTIONABLE_REPAIR_RECEIPT_PERSIST_FAILED` | engineering persistence | Incident issue lookup or append-only receipt write failed. | `UNRESOLVED_TYPED_FAILURE` |

Delivery statuses (not failures): `PR_READY` (exact-head open PR, disposition `PR_CREATED`) and `DRAFT_PR_GATES_FAILED` (exact-head draft PR; pre-PR gates failed; disposition `PR_CREATED`, does not authorize merge or deploy).

Heartbeat lease states (not failures): `HELD` (run succeeded; claimed `lease_expires_at` kept) and `RELEASED_ON_FAILURE` (run ended with job status other than `success`; heartbeat reports `worker_mode: SAFE_HOLD`, `lease_expires_at` is set to the time the run ended, and the original claim is kept as `lease_claimed_expires_at` in the receipt).
## Agent identity and protection policy codes (incident #1550, parent #1540)

Emitted by `artifacts/wow-engine/v17/agent_identity_policy.py evaluate`, which joins an owner inventory, per-runtime AI principal evidence and owner-observed live allow/deny probes. Any finding makes the verdict `HOLD` with disposition `BLOCKED_WITH_EXACT_REASON`. `PASS` is configuration evidence only: no work-item closure, merge, release or probability authority. None affects sporting probability, rank or `can_execute=false`.

| Code | Owning lane/stage | Exact condition | Rank eligible? |
|---|---|---|---:|
| `IDENTITY_SNAPSHOT_INCOMPLETE` | engineering governance | An owner-inventory input could not be read or was malformed/truncated (e.g. `RULESETS_UNREADABLE:403`, `CODEOWNERS_UNREADABLE:<loc>:<status>`, `INSTALLATION_REPOSITORIES_TRUNCATED:<app>`, `CODEOWNERS_UNCONFIRMED`); suffixed with the input. Never treated as absent; the dependent finding is suppressed. | N/A |
| `IDENTITY_SNAPSHOT_NOT_OWNER_COLLECTED` | engineering governance | Owner inventory was collected by a login other than the repository owner. | N/A |
| `IDENTITY_APP_UNBOUND` | engineering governance | No App ID bound to the role (`WOW_<ROLE>_APP_ID` variable unset, variables readable); suffixed `:<role>`. | N/A |
| `IDENTITY_APP_NOT_INSTALLED` | engineering governance | Bound App ID has no installation visible to the owner (installations readable); suffixed `:<role>`. | N/A |
| `IDENTITY_APPS_NOT_DISTINCT` | engineering governance | Two roles are bound to the same App; QA/Release independence is impossible. | N/A |
| `IDENTITY_PERMISSION_FORBIDDEN` | engineering governance | An agent App holds a never-grantable permission (administration, secrets, environments, …); suffixed `:<role>:<perm>:<level>`. | N/A |
| `IDENTITY_PERMISSION_EXCESS` | engineering governance | An agent App holds a permission or level beyond its exact policy set; suffixed `:<role>:<perm>:<level>`. | N/A |
| `IDENTITY_PERMISSION_MISSING` | engineering governance | An agent App lacks a permission its role requires; suffixed `:<role>:<perm>:<level>`. | N/A |
| `IDENTITY_INSTALLATION_NOT_REPO_SCOPED` | engineering governance | App installed on all repositories instead of only selected ones; suffixed `:<role>`. | N/A |
| `IDENTITY_INSTALLATION_REPO_NOT_INCLUDED` | engineering governance | The App's selected-repository list does not contain this repository's ID; suffixed `:<role>`. | N/A |
| `IDENTITY_AI_PRINCIPAL_UNMEASURED` | engineering governance | No principal evidence collected from inside a required AI runtime; suffixed `:<runtime>`. | N/A |
| `IDENTITY_AI_PRINCIPAL_EVIDENCE_AMBIGUOUS` | engineering governance | More than one principal record for the same AI runtime; suffixed `:<runtime>`. | N/A |
| `IDENTITY_AI_PRINCIPAL_EVIDENCE_INVALID` | engineering governance | Principal record malformed (wrong kind, missing login or timestamps); suffixed `:<runtime>`. | N/A |
| `IDENTITY_AI_PRINCIPAL_NONCE_MISMATCH` | engineering governance | Principal record not bound to this owner inventory's nonce; suffixed `:<runtime>`. | N/A |
| `IDENTITY_AI_PRINCIPAL_REPO_MISMATCH` | engineering governance | Principal record measured a different repository ID; suffixed `:<runtime>`. | N/A |
| `IDENTITY_AI_PRINCIPAL_EVIDENCE_STALE` | engineering governance | Principal record expired, TTL above 24h, or collected in the future; suffixed `:<runtime>`. | N/A |
| `IDENTITY_AI_CREDENTIAL_IS_OWNER_USER` | engineering governance | The AI runtime, measured from inside it, still acts as the owner's user account; suffixed `:<runtime>`. | N/A |
| `IDENTITY_AI_CREDENTIAL_CAN_MERGE` | engineering governance | The AI runtime's bounded all-zero-SHA merge attempt reached GitHub's SHA guard (409 *Head branch was modified*), i.e. authorization was not denied; suffixed `:<runtime>`. | N/A |
| `IDENTITY_AI_MERGE_PROBE_INCONCLUSIVE` | engineering governance | The AI runtime's merge probe neither proved denial (403/404) nor reached the SHA guard (conflict, 405, 422, 5xx, skipped, absent); suffixed `:<runtime>`. | N/A |
| `PROTECTION_CODEOWNERS_MISSING` | engineering governance | CODEOWNERS confirmed absent: all three GitHub locations returned 404. | N/A |
| `PROTECTION_CODEOWNERS_UNCOVERED` | engineering governance | A trust-root path, or a NEW workflow/action/script probe, is not owned by the owner under last-match-wins semantics; suffixed `:<path>`. | N/A |
| `PROTECTION_RULESET_MISSING` | engineering governance | Rulesets readable, but none is an active branch ruleset targeting the default branch. | N/A |
| `PROTECTION_PULL_REQUEST_RULE_MISSING` | engineering governance | Active ruleset lacks a pull-request rule. | N/A |
| `PROTECTION_CODE_OWNER_REVIEW_NOT_REQUIRED` | engineering governance | Code-owner review not required, so trust-root changes need no owner approval. | N/A |
| `PROTECTION_LAST_PUSH_APPROVAL_NOT_REQUIRED` | engineering governance | Approval of the most recent push not required; a post-approval push could slip through. | N/A |
| `PROTECTION_STALE_REVIEWS_NOT_DISMISSED` | engineering governance | Approvals survive new pushes. | N/A |
| `PROTECTION_FORCE_PUSH_ALLOWED` | engineering governance | No non-fast-forward rule on the default branch. | N/A |
| `PROTECTION_DELETION_ALLOWED` | engineering governance | No deletion rule on the default branch. | N/A |
| `PROTECTION_CHECKS_NOT_STRICT` | engineering governance | Required checks are not strict (head need not be up to date with base). | N/A |
| `PROTECTION_CHECK_MISSING` | engineering governance | A required regression, QA or Release check is absent; suffixed `:<check>`. | N/A |
| `PROTECTION_CHECK_NOT_SOURCE_PINNED` | engineering governance | QA/Release check is not pinned to its own App's integration_id, so another principal could satisfy it; suffixed `:<check>`. | N/A |
| `PROTECTION_BYPASS_PRESENT` | engineering governance | Ruleset has any bypass actor; while AI acts as the owner user, a bypass is an AI bypass; suffixed `:<type>:<id>`. | N/A |
| `PROTECTION_LIVE_PROBE_MISSING` | engineering governance | A required owner-observed allow/deny probe against the enforced ruleset has no record; suffixed `:<probe>`. | N/A |
| `PROTECTION_LIVE_PROBE_FAILED` | engineering governance | A live probe observed the opposite of the required outcome (e.g. ordinary PR blocked, trust-root PR allowed); suffixed `:<probe>`. | N/A |
| `PROTECTION_LIVE_PROBE_INVALID` | engineering governance | A live probe record is malformed or unbound (expected outcome, PR number, 40-hex head SHA, timestamp, nonce); suffixed `:<probe>`. | N/A |

## Independent QA and Release Authority check codes (#1550 Phase C)

Emitted by `artifacts/wow-engine/v17/independent_release_checks.py` and the `wow-v17-independent-qa-check` / `wow-v17-release-authority-check` workflows. Any finding publishes a `failure` check (or no check, for credential/target/publish errors), which blocks merge once the ruleset requires the check. Release re-derives every QA condition and reports it prefixed `RELEASE_` (e.g. `RELEASE_QA_HEAD_STALE`). A `success` check is evidence for the ruleset only: never merge, deployment or probability authority. None affects sporting probability, rank or `can_execute=false`.

| Code | Owning lane/stage | Exact condition | Rank eligible? |
|---|---|---|---:|
| `QA_INPUT_HEAD_SHA_INVALID` | QA manual dispatch | Optional requested head SHA is malformed; no QA decision is published. | N/A |
| `QA_INPUT_HEAD_SHA_STALE` | QA manual dispatch | Requested head SHA does not equal the current PR head; dispatch aborts. | N/A |
| `QA_REVIEW_MODES_CONFLICT` | QA mode selection | Zero-credit and API-backed adversarial review flags are both true; neither mode is silently substituted. | N/A |
| `QA_ZERO_CREDIT_CANARY_CONFLICT` | QA mode selection | Zero-credit mode and independent-review canary were requested simultaneously. | N/A |
| `QA_ZERO_CREDIT_POLICY_INVALID` | QA and Release mode selection | The zero-credit activation variable has a value other than empty, false, or true. | N/A |
| `QA_ZERO_CREDIT_POLICY_NOT_ENABLED` | QA evidence | Zero-credit receipt is presented without the explicitly activated reduced-assurance policy. | N/A |
| `QA_ZERO_CREDIT_POLICY_CONFLICT` | QA evidence | Zero-credit receipt conflicts with active adversarial-review requirement or review-active evidence. | N/A |
| `QA_ZERO_CREDIT_SOURCE_REVIEW_UNEXPECTED` | QA evidence | Zero-credit procedure carries a purported independent source-review artifact; zero-credit cannot claim one. | N/A |
| `QA_ZERO_CREDIT_POLICY_TARGET_MISMATCH` | QA evidence | Zero-credit policy does not bind the canonical PR, exact head SHA and head ref. | N/A |
| `RELEASE_INPUT_HEAD_SHA_INVALID` | Release manual dispatch | Optional Release target head SHA is malformed. | N/A |
| `RELEASE_INPUT_HEAD_SHA_STALE` | Release manual dispatch | Release requested head SHA does not match authenticated producing QA receipt head. | N/A |
| `QA_CANONICAL_INCIDENT_MISSING_OR_AMBIGUOUS` | required independent QA and Release | Live PR description lacks exactly one standalone canonical `Incident: #positive-integer` field, or contains duplicate/conflicting fields. Required QA fails before merge; Release independently re-derives the prefixed finding. | N/A |
| `QA_APP_CREDENTIAL_MISSING` | independent QA | `WOW_QA_APP_ID` is not numeric or `WOW_QA_APP_PRIVATE_KEY` is absent; no QA check is published (the required check stays missing, so merges block). | N/A |
| `QA_REVIEW_PROVIDER_CREDENTIAL_MISSING` | independent QA | Independent-review activation is enabled but the separate `WOW_QA_REVIEW_OPENAI_API_KEY` is absent; no QA decision/check is published. | N/A |
| `QA_REVIEW_CANARY_NOT_AUTHORIZED_FOR_EXACT_HEAD` | independent QA canary | Dispatch requested a canary but its PR number and head SHA do not exactly match the owner-configured single-PR/SHA admission. | N/A |
| `QA_REVIEW_CANARY_REQUIRES_DISPATCH` | independent QA canary | Canary policy was requested from an event other than an explicit protected workflow dispatch. | N/A |
| `QA_TARGETS_UNRESOLVED` | independent QA | QA could not resolve any valid (PR, exact head) target; details in the annotation. | N/A |
| `QA_TARGET_COUNT_INVALID` | independent QA | A protected QA invocation resolved anything other than one exact (PR, head) target; no source review or check is published. | N/A |
| `QA_REVIEW_POLICY_INVALID` | independent QA / release | Independent-review activation is not one of the accepted empty, `false`, or `true` states; no decision/check is published. | N/A |
| `QA_REVIEW_POLICY_MODE_INVALID` | independent QA | The authenticated producing QA run has no supported immutable review mode. | N/A |
| `QA_REVIEW_POLICY_MODE_CONFLICT` | independent QA | The evidence mode and review requirement disagree. | N/A |
| `QA_REVIEW_POLICY_RUN_ID_INVALID` | independent QA | The authenticated QA policy receipt has no valid producing run identity. | N/A |
| `QA_REVIEW_POLICY_TARGET_MISMATCH` | independent QA | Policy receipt PR, head SHA, or head ref differs from the evidence target. | N/A |
| `QA_REVIEW_POLICY_LEGACY_AFTER_ACTIVATION` | independent QA / release | A legacy-mode QA receipt is presented after global independent-review activation; it cannot satisfy the active policy. | N/A |
| `QA_REVIEW_CANARY_SCOPE_MISMATCH` | independent QA canary | Canary receipt does not bind the exact admitted PR number and head SHA. | N/A |
| `QA_REVIEW_CANARY_OWNER_AUTHORED` | independent QA canary | Owner-authored PR is not eligible for the ordinary inert canary path. | N/A |
| `QA_REVIEW_CANARY_TRUST_ROOT` | independent QA canary | Trust-root changes are not eligible for the ordinary inert canary path. | N/A |
| `QA_DECISION_INVARIANT_VIOLATED` | independent QA | A QA decision file claimed merge/release or execution authority; nothing published. | N/A |
| `QA_CHECK_PUBLISH_FAILED` | independent QA | Creating the QA check run with the QA App token failed. | N/A |
| `QA_REVIEW_EXECUTION_FAILED` | independent QA | Review capture, provider, receipt, or decision execution failed after an exact target was resolved; the QA App publishes a failure and a run-scoped outcome artifact is retained. Suffixed with the failed step. | N/A |
| `QA_EVIDENCE_INCOMPLETE` | independent QA | An evidence input was unreadable, truncated, or malformed (repo, PR, files, check runs, reviews); suffixed with the input. For changed files, both rename destination and `previous_filename` (source) are evaluated; a missing/invalid rename origin emits `QA_EVIDENCE_INCOMPLETE:FILES_RENAME_ORIGIN_MISSING`, invalid names emit `...:FILES_PATH_INVALID`, and rename origins cannot mask `...:FILES_TRUNCATED`. | N/A |
| `QA_HEAD_INVALID` | independent QA | Expected or current head is not a 40-hex SHA. | N/A |
| `QA_HEAD_STALE` | independent QA | The PR head moved after the trigger; the check is published only on the superseded SHA. | N/A |
| `QA_INDEPENDENT_SOURCE_REVIEW_MISSING` | independent QA | No current, authenticated, exact-head source-review artifact is available; CI or a trusted-governance receipt cannot substitute. | N/A |
| `QA_INDEPENDENT_SOURCE_REVIEW_INVALID` | independent QA | The source-review artifact, workflow provenance, PR author, reviewed file inventory, or exact-head binding is invalid. Release prefixes the finding when independently re-deriving QA. | N/A |
| `QA_INDEPENDENT_SOURCE_REVIEW_REJECTED` | independent QA | The authenticated exact-head adversarial review explicitly rejected the source; Release independently preserves the rejection. | N/A |
| `QA_PR_NOT_OPEN` | independent QA | PR is closed or merged. | N/A |
| `QA_PR_DRAFT` | independent QA | PR is a draft. | N/A |
| `QA_PR_BASE_NOT_MAIN` | independent QA | PR does not target `main`. | N/A |
| `QA_PR_HEAD_REPOSITORY_MISMATCH` | independent QA | PR head is in another repository (fork). | N/A |
| `QA_FILES_EMPTY` | independent QA | No changed files could be listed. | N/A |
| `QA_EVIDENCE_MISSING` | independent QA | A required exact-head check from `github-actions` (App 15368) has no completed run at the head; same-named checks from other Apps are ignored. Suffixed `:<check>`. | N/A |
| `QA_EVIDENCE_FAILED` | independent QA | The latest exact-head run of a required check is not `success`; suffixed `:<check>`. | N/A |
| `QA_TRUST_ROOT_OWNER_APPROVAL_MISSING` | independent QA | The PR changes a trust root (`.github/`, `.agents/`, or a file a governance workflow executes or reads) and the owner's latest review is not APPROVED on this exact head. | N/A |
| `RELEASE_APP_CREDENTIAL_MISSING` | release authority | `WOW_RELEASE_APP_ID` is not numeric or `WOW_RELEASE_APP_PRIVATE_KEY` is absent; no Release check is published. | N/A |
| `RELEASE_PR_NUMBER_INVALID` | release authority | Manual Release re-entry did not provide a valid PR number to bind to its QA receipt. | N/A |
| `RELEASE_QA_RUN_IDENTITY_INVALID` | Release read-only admission | Producing QA run ID or attempt is missing or malformed before assigning a PR-scoped writer. | N/A |
| `RELEASE_QA_APPLICABILITY_INVALID` | Release read-only admission | Authenticated upstream QA classifier returned neither neutral nor applicable; no writer allocated. | N/A |
| `RELEASE_EVENT_NOT_SUPPORTED` | Release read-only admission | An event other than an authenticated QA completion or explicit authorized dispatch attempted Release admission. | N/A |
| `RELEASE_TARGET_PR_INVALID` | Release manual read-only admission | Requested PR is not a positive integer; no Release writer starts. | N/A |
| `RELEASE_POLICY_TARGET_INVALID` | Release read-only QA receipt | Authenticated QA policy resolved a missing, malformed or contradictory exact PR/SHA target. | N/A |
| `RELEASE_POLICY_TARGET_STALE` | Release manual read-only QA receipt | Requested exact SHA differs from the producing QA policy's bound head. | N/A |
| `RELEASE_QA_REVIEW_POLICY_MISSING` | release authority | Release has no authenticated policy receipt from the exact producing QA workflow run. | N/A |
| `RELEASE_QA_REVIEW_POLICY_INVALID` | release authority | The producing QA receipt has an unsupported review mode. | N/A |
| `RELEASE_DECISION_INVARIANT_VIOLATED` | release authority | A Release decision file claimed authority; nothing published. | N/A |
| `RELEASE_CHECK_PUBLISH_FAILED` | release authority | Creating the Release check run with the Release App token failed. | N/A |
| `RELEASE_APP_IDENTITY_UNBOUND` | release authority | QA or Release App ID is not configured as a number. | N/A |
| `RELEASE_IDENTITY_NOT_DISTINCT` | release authority | QA and Release are bound to the same App. | N/A |
| `RELEASE_QA_MISSING` | release authority | No completed `WOW Independent QA exact-head` run from the QA App at this head. | N/A |
| `RELEASE_QA_FAILED` | release authority | The latest QA App check at this head is not `success`. | N/A |
| `RELEASE_QA_FOREIGN_SOURCE` | release authority | A `WOW Independent QA exact-head` run exists at this head but from a principal other than the QA App (spoof or misconfiguration). | N/A |
| `RELEASE_QA_EVIDENCE_PENDING` | release authority | A required regression check is queued/running at this open PR head, proven either by its official check run or by the exact current PR's active `wow-verify` run and named required job (maximum pending age: two hours); no Release check is published, and workflow completion triggers fresh evaluation. | N/A |
| `RELEASE_READINESS_DISPOSITION_INVALID` | release authority | The release readiness classifier returned neither `pending` nor `ready`; no Release check is published. | N/A |
| `RELEASE_UPSTREAM_QA_APPLICABLE` | release authority | The authenticated upstream QA run completed and its `qa` job concluded `success` or `failure`. Policy resolution still runs; this is not a Release pass. | N/A |
| `RELEASE_UPSTREAM_QA_DISPOSITION_INVALID` | release authority | Upstream classification returned a disposition other than `neutral` or `applicable`. No Release check is published. | N/A |
| `RELEASE_UPSTREAM_QA_JOB_INCOMPLETE` | release authority | The upstream `qa` job is not a completed success, failure, or skip. No Release check is published. | N/A |
| `RELEASE_UPSTREAM_QA_NO_TARGET` | release authority | The upstream QA workflow completed but its `qa` job was skipped, so there is no exact-head target. Neutral: no policy lookup and no Release check. | N/A |
| `RELEASE_UPSTREAM_QA_SKIPPED` | release authority | The upstream QA workflow conclusion is `skipped`. Neutral: no policy lookup and no Release check. | N/A |
| `RELEASE_UPSTREAM_QA_UNREADABLE` | release authority | The upstream QA run or its job list could not be read as a complete inventory. No Release check is published. | N/A |
| `RELEASE_UPSTREAM_QA_UNTRUSTED` | release authority | The upstream run is not the protected-main QA workflow at the claimed attempt, or its repository, event, or job identity does not match. Cancelled runs are untrusted. No Release check is published. | N/A |
| `TARGET_HEAD_INVALID` | check targeting | A workflow_run pull request entry has no valid 40-hex head SHA; suffixed `:<pr>`. | N/A |
| `TARGET_PR_INVALID` | check targeting | Dispatched PR number is not numeric. | N/A |
| `TARGET_PR_UNREADABLE` | check targeting | Dispatched PR could not be read or has no head SHA; suffixed with HTTP status. | N/A |
| `TARGET_PR_IDENTITY_MISMATCH` | check targeting | The fresh canonical PR does not match the workflow_run PR number/head, canonical head repository, or main base repository/ref. | N/A |
| `TARGET_SWEEP_UNREADABLE` | check targeting | Open-PR sweep listing could not be read; suffixed with HTTP status. | N/A |

## Exact-target bootstrap and Claude structured-output codes (PR #1531)

Engineering control plane only. None affects sporting probability, rank or `can_execute=false`, and none is a provider outage eligible for failover except where the provider classifier independently says so.

| Code | Owning lane/stage | Exact condition | Rank eligible? |
|---|---|---|---:|
| `CLAUDE_STRUCTURED_OUTPUT_MISSING` | Claude agent action (`.github/actions/wow-claude-agent`) | Neither the API-key nor the OAuth attempt returned structured output (a provider exit 0 without structured output is not success). The agent step fails; in the Claude worker this surfaces as the cause-specific `ACTIONABLE_REPAIR_*` code of the step that needed it. | N/A |
| `CLAUDE_STRUCTURED_OUTPUT_INVALID_JSON` | Claude agent action | Structured output was present but not a JSON object. The agent step fails; downstream role gates never read it. | N/A |
| `TARGET_INCIDENT_INVALID` | provider dispatcher | A targeted dispatch's incident identity is not a numeric issue number. Dispatch refused. | N/A |
| `P0_DOMAIN_LEASE_MISSING` | engineering worker target selection | An exact P0 RAPID target was dispatched without a non-GLOBAL domain lease group in the dispatch manifest. Suffixed `:<incident>`. | N/A |
| `STANDARD_TARGET_MUST_USE_GLOBAL_LEASE` | engineering worker target selection | An exact P1 STANDARD target was dispatched with a domain lease group instead of the GLOBAL writer lease. Suffixed `:<incident>`. | N/A |
| `TARGET_INCIDENT_NOT_SUPPORTED` | engineering worker target selection | Exact target is neither P0 RAPID nor P1 STANDARD. Suffixed `:<incident>:<severity>:<lane>`. | N/A |

## NFL governed final-refresh proof (October 2026)

These are evidence/freshness publication holds, **not** fitted-model unavailability. Each withholds rank eligibility without erasing completed sporting probabilities. Regression coverage: `tests/test_v17_nfl_final_refresh_proof.py`.

| Code | Owning lane/stage | Exact condition | Rank eligible? |
|---|---|---|---:|
| `FINAL_REFRESH_EVENT_NOT_PREGAME` | NFL team-event / final refresh | Event start is at or before the verification instant, or a valid timezone-aware kickoff is absent. | No |
| `FINAL_REFRESH_SCORED_SOURCE_PROVENANCE_MISSING` | NFL team-event / final refresh | Scored canonical schedule snapshot ID, content hash or capture timestamp is absent. | No |
| `FINAL_REFRESH_MODEL_TIMESTAMP_NOT_CURRENT` | NFL team-event / final refresh | Model timestamp is missing/invalid or precedes the scored material-update timestamp. | No |
| `FINAL_REFRESH_SOURCE_UNAVAILABLE` | NFL team-event / final refresh | Re-reading the latest canonical NFL schedule raises or cannot be completed. | No |
| `FINAL_REFRESH_CANONICAL_EVENT_UNRESOLVED` | NFL team-event / final refresh | The canonical source requery cannot resolve a single matching eligible event. | No |
| `FINAL_REFRESH_EVENT_IDENTITY_CHANGED` | NFL team-event / final refresh | Canonical event/team identity no longer matches the fitted-model event. | No |
| `FINAL_REFRESH_MATERIAL_SOURCE_CHANGED` | NFL team-event / final refresh | Canonical NFL schedule content hash differs from that used for the scored prediction; rerun required. | No |
| `FINAL_REFRESH_SOURCE_STALE_OR_INVALID` | NFL team-event / final refresh | Latest canonical schedule capture lacks a valid UTC timestamp, is future-dated, or exceeds the 24-hour publication freshness window. | No |

## Registry rule

A new code requires, in the same change: code name, owning lane/stage, exact condition, whether it affects sporting probability/rank, and a regression test. Provider-specific detail codes may be preserved underneath a registered class; they must never be rewritten into `MODEL_UNAVAILABLE` unless the fitted model capability itself is truly absent.

## NCAAF result/form research forward-feature diagnostics (P0 #665)

These are **research feature preparation** failures, not certified model inference failures. The feature builder never produces a probability, ranking, value result, or wager, regardless of diagnostic success. Both `probability_publishable` and `can_execute` remain false.

| Code | Owning lane/stage | Exact condition | Rank eligible? |
|---|---|---|---:|
| `NCAAF_FORWARD_EVENT_TIME_INVALID` | NCAAF research forward feature builder | Current event or relevant settled-result timestamp missing, invalid or timezone-naive. | No |
| `NCAAF_FORWARD_CANONICAL_IDENTITY_NOT_PROVEN` | NCAAF research forward feature builder | Upstream CFBD canonical event identity has not been proven for this event. | No |
| `NCAAF_FORWARD_CANONICAL_PROOF_INVALID` | NCAAF research forward feature builder | Claimed CFBD event-resolution object is missing, does not match official event identity/participants, or contains contradictory provider/market/authority fields. | No |
| `NCAAF_FORWARD_CANONICAL_START_MISMATCH` | NCAAF research forward feature builder | Caller kickoff differs from CFBD resolver kickoff beyond the canonical identity tolerance. | No |
| `NCAAF_FORWARD_EVENT_IDENTITY_INVALID` | NCAAF research forward feature builder | Empty or contradictory canonical event ID/team participants. | No |
| `NCAAF_FORWARD_NEUTRAL_SITE_INVALID` | NCAAF research forward feature builder | Neutral-site indicator is not a concrete boolean. | No |
| `NCAAF_FORWARD_NEUTRAL_SITE_SOURCE_MISSING` | NCAAF research forward feature builder | CFBD canonical event resolution has no concrete boolean neutral-site observation. Do not impute home field. | No |
| `NCAAF_FORWARD_NEUTRAL_SITE_SOURCE_CONTRADICTION` | NCAAF research forward feature builder | Caller-provided neutral site disagrees with the CFBD event's neutral-site observation. | No |
| `NCAAF_FORWARD_TARGET_IN_HISTORY` | NCAAF research forward feature builder | Target event appears in the settled-results input, creating a potential target/outcome leakage path. | No |
| `NCAAF_FORWARD_PRIOR_EVENT_ID_MISSING` | NCAAF research forward feature builder | A relevant settled prior result lacks an immutable official event ID. | No |
| `NCAAF_FORWARD_PRIOR_EVENT_DUPLICATE` | NCAAF research forward feature builder | A relevant settled prior event identity occurs more than once in accepted evidence. | No |
| `NCAAF_FORWARD_PRIOR_RESULT_INVALID` | NCAAF research forward feature builder | Relevant prior-game score/status cannot be reconciled to a valid settled winner. | No |
| `NCAAF_FORWARD_PRIOR_RESULT_CONTRADICTION` | NCAAF research forward feature builder | Source home_won disagrees with settled score. | No |
| `NCAAF_FORWARD_PRIOR_EVIDENCE_TIME_CONTRADICTION` | NCAAF research forward feature builder | Settled-result acquisition timestamp is at or before its game's scheduled start, so outcome evidence could not yet exist. | No |
| `NCAAF_FORWARD_PRIOR_FORM_INSUFFICIENT` | NCAAF research forward feature builder | One or both teams lack the model's minimum three source-eligible settled prior games; no baseline imputation. | No |
| `NCAAF_FORWARD_FEATURE_SCHEMA_INVALID` | NCAAF research forward feature builder | Computed numeric features do not exactly match the candidate model schema or finite-number contract. | No |
| `NCAAF_FORWARD_FEATURE_NOT_PREGAME` | NCAAF research forward feature builder | Latest evidence acquisition timestamp is not strictly before the target event start. | No |

## NBA POINTS source-snapshot bridge diagnostics (P0 #1988)

The optional `v17/nba_points_snapshot_bridge.py` is a **read-only, source-supplied input validator**. It does not authenticate an upstream NBA feed, write a DB row, activate a route, score/calibrate/certify a model, rank or execute. It must be invoked only after source acquisition; `SOURCE_VALIDATED_SNAPSHOT_NOT_PERSISTED` is not a certified hydrated model prediction. Every failure below is a bridge-stage `NBAPointsSourceError`, never `MODEL_UNAVAILABLE`, scorer completion failure, or global terminal label.

| Code or family | Owning lane/stage | Exact condition | Affects sporting probability/rank? |
|---|---|---|---|
| `NBA_POINTS_*_MISSING`, `NBA_POINTS_*_INVALID`, `NBA_POINTS_*_NAIVE` | NBA POINTS pregame evidence bridge | A required explicit identifier/time/number is blank, malformed, nonfinite, or timezone-naive | No; no prediction made |
| `NBA_POINTS_EVENT_NOT_PREGAME`, `NBA_POINTS_CAPTURE_NOT_PREGAME`, `NBA_POINTS_ROSTER_TIMESTAMP_INVALID`, `NBA_POINTS_ROLE_TIMESTAMP_INVALID`, `NBA_POINTS_SOURCE_TIMESTAMP_INVALID`, `NBA_POINTS_LINE_TIMESTAMP_INVALID` | NBA POINTS point-in-time source gating | Event already started or capture/roster/role/game/source/line evidence occurs after allowed pregame cutoff | No; evidence rejected |
| `NBA_POINTS_TEAM_IDENTITY_CONFLICT`, `NBA_POINTS_CANONICAL_EVENT_UNRESOLVED`, `NBA_POINTS_CANONICAL_EVENT_UNVERIFIED`, `NBA_POINTS_CANONICAL_TEAM_MISMATCH`, `NBA_POINTS_ROSTER_PLAYER_MISMATCH` | NBA POINTS canonical event/player/roster identity | Schedule identity is absent, alias-only or contradicts exactly roster team/player or target event | No |
| `NBA_POINTS_ADJACENT_LINE_NOT_EXACT`, `NBA_POINTS_EXACT_LINE_MISMATCH`, `NBA_POINTS_LINE_UNSUPPORTED`, `NBA_POINTS_DIRECTION_INVALID`, `NBA_POINTS_PERIOD_UNSUPPORTED` | NBA POINTS exact stat-line/side identity | Source line is adjacent/contradictory, invalid line, unsupported period or non-MORE/LESS side | No |
| `NBA_POINTS_PLAYER_AVAILABILITY_HOLD`, `NBA_POINTS_ROLE_EVIDENCE_MISSING` | NBA POINTS source role check | Player not affirmatively listed available or missing role evidence | No |
| `NBA_POINTS_L10_EVIDENCE_INCOMPLETE`, `NBA_POINTS_PRIOR_GAME_INVALID`, `NBA_POINTS_PRIOR_GAME_DUPLICATE_OR_TARGET`, `NBA_POINTS_PRIOR_GAME_PLAYER_MISMATCH`, `NBA_POINTS_PRIOR_GAME_FUTURE_LEAKAGE`, `NBA_POINTS_GAME_BOX_SCORE_MISMATCH`, `NBA_POINTS_SOURCE_TIMESTAMPS_MISSING`, `NBA_POINTS_SOURCE_IDENTITY_INVALID` | NBA POINTS historical-game evidence | Fewer than 10 source stat rows; duplicate/wrong/future game or player; conflict between points and box-score observations; missing provenance | No |
| `NBA_POINTS_CANONICAL_EVIDENCE_REJECTED`, `NBA_POINTS_SNAPSHOT_IDENTITY_MISMATCH`, `NBA_POINTS_SNAPSHOT_LINE_MISMATCH` | NBA POINTS canonical V17 validator/snapshot handoff | Existing V17 scorer evidence normalizer fails or the immutable snapshot changed event/player/stat/exact line | No |

Regression: `artifacts/wow-engine/tests/test_v17_nba_points_snapshot_bridge.py`, including exact source-line and historical-identity counterexamples. Once a real provider path is integrated, its own source-authenticity/identity checks remain required. `can_execute=false`, `probability_publishable=false` throughout.

### NBA official history cross-source diagnostics (#1988)

`v17/nba_points_official_history.py` is read-only research acquisition. The NBA Stats gamelog and NBA CDN settled boxscore must agree on game ID, player ID, final status, game start and points. It does not claim active-role/roster verification, exact market line, calibrated probability, publication or certification.

| Code or family | Owning stage | Exact condition | Sporting probability/rank changed? |
|---|---|---|---|
| `NBA_HISTORY_PLAYER_ID_INVALID`, `NBA_HISTORY_SEASON_SCOPE_INVALID` | NBA POINTS source request | Player identifier is non-numeric/unsafe or season fanout exceeds two explicit seasons | No |
| `NBA_HISTORY_TIME_INVALID`, `NBA_HISTORY_TIME_NAIVE`, `NBA_HISTORY_EVENT_NOT_PREGAME`, `NBA_HISTORY_GAME_DATE_INVALID` | NBA POINTS temporal source check | Missing/invalid/naive timestamp or target has started | No |
| `NBA_HISTORY_SOURCE_HTTP_FAILURE`, `NBA_HISTORY_SOURCE_UNAVAILABLE`, `NBA_HISTORY_SOURCE_INVALID`, `NBA_HISTORY_GAMELOG_SCHEMA_INVALID` | NBA Stats/CDN HTTP+schema | Provider HTTP failure, invalid response or missing unique required result set/columns | No |
| `NBA_HISTORY_PLAYER_ID_MISMATCH`, `NBA_HISTORY_GAME_ID_INVALID`, `NBA_HISTORY_POINTS_INVALID`, `NBA_HISTORY_DUPLICATE_GAME_CONTRADICTION`, `NBA_HISTORY_L10_INSUFFICIENT` | NBA playergamelog history preparation | Historical row player/game/stat invalid, duplicated contradictorily, or fewer than ten eligible prior rows | No |
| `NBA_HISTORY_BOX_GAME_ID_MISMATCH`, `NBA_HISTORY_GAME_NOT_FINAL`, `NBA_HISTORY_BOX_TIME_CONTRADICTION`, `NBA_HISTORY_BOX_SCORE_SCHEMA_INVALID`, `NBA_HISTORY_BOX_PLAYER_ID_MISSING_OR_DUPLICATE`, `NBA_HISTORY_POINTS_DISAGREE_WITH_BOX` | NBA CDN settled-game reconciliation | Missing exact official game/player, non-final game, impossible kickoff/date, invalid boxscore or disagreed points | No |

Regression: `artifacts/wow-engine/tests/test_v17_nba_points_official_history.py`. MAX 10 CDN game calls plus 1-2 supplied NBA playergamelog seasons; no guessed timestamps, role status, board line, probabilities or wager functions. A successful history retrieval is **not** a complete pregame evidence snapshot.

### NBA POINTS exact quote/roster event binding (#1990)

A numerically matching prop quote is **not** authority for a different event, player, period, stat or direction. The bridge now requires a source-supplied, canonical event-ID-bound current roster and exact board quote; unknown or missing identifiers hold at evidence stage. This does not certify the board source's authenticity or produce a model probability.

| Code | Owning stage | Fail-closed condition |
|---|---|---|
| `NBA_POINTS_BOARD_EVENT_MISMATCH` | Exact quote identity | Board quote canonical event ID differs from target |
| `NBA_POINTS_BOARD_PLAYER_MISMATCH` | Exact quote identity | Quote canonical player ID differs from target |
| `NBA_POINTS_BOARD_STAT_MISMATCH` | Exact quote identity | Quote stat is not NBA POINTS |
| `NBA_POINTS_BOARD_DIRECTION_MISMATCH` | Exact quote identity | Quote MORE/LESS side differs from target |
| `NBA_POINTS_BOARD_PERIOD_MISMATCH` | Exact quote identity | Quote period is not FULL_GAME |
| `NBA_POINTS_BOARD_START_MISMATCH` | Exact quote identity | Quote event kickoff timestamp differs from target |
| `NBA_POINTS_ROSTER_EVENT_MISMATCH` | Current roster identity | Source roster event ID differs from the exact canonical target |
| `NBA_POINTS_*_MISSING` | Source structure | Missing quote/roster identity is held rather than inferred |

Both directions require their own correctly typed exact quote and preserve the already-observed market line. These checks are additional to event cutoff, official history, calibration and terminal governance; `can_execute=false`.

### Cursor Cloud Agent engineering handoff (#2008)

Codes below belong solely to the engineering GitHub-to-Cursor control plane. None grants model qualification, wager execution, merge, or deployment authority. A queued Cloud Agent is not production acceptance.

| Code | Owning stage | Trigger / fail-closed meaning |
|---|---|---|
| `CURSOR_HANDOFF_DISABLED` | provider-dispatcher feature gate | Owner opt-in variable is not true; no API call |
| `CURSOR_DISPATCH_INPUTS_MISSING` | provider-dispatcher admission | Required PR number, expected SHA, or scoped task is empty |
| `CURSOR_HANDOFF_COMMAND_INVALID` | PR-comment bridge | First line is not exactly `/wow-cursor` |
| `CURSOR_HANDOFF_TASK_INVALID`, `CURSOR_TASK_INVALID` | PR-comment bridge / adapter input | Task missing, empty or longer than bounded input contract |
| `CURSOR_PR_IDENTITY_NOT_ELIGIBLE` | PR-comment bridge | PR is not an open same-repository branch targeting main |
| `CURSOR_IDENTITY_INPUT_INVALID`, `CURSOR_PR_NUMBER_INVALID` | adapter admission | Malformed repository, exact SHA, or PR number |
| `CURSOR_PR_NOT_OPEN_OR_MISMATCH` | GitHub PR readback | Requested PR is not open or has changed identity |
| `CURSOR_STALE_PR_HEAD` | GitHub PR head lock | PR head no longer matches caller's immutable expected SHA |
| `CURSOR_PR_INCIDENT_MISMATCH`, `CURSOR_INCIDENT_ID_INVALID` | canonical PR/incident binding | PR body does not bind exactly one matching open incident, or incident ID is malformed |
| `CURSOR_INCIDENT_NOT_OPEN_OR_MISMATCH` | GitHub incident readback | Incident is closed, mismatched, or is not an issue in the target repository |
| `CURSOR_OWNER_NOT_AUTHORIZED` | adapter authorization | Dispatch actor is neither the repository owner nor an explicitly authorized GitHub automation identity |
| `CURSOR_SHARED_WRITER_LEASE_ACTIVE`, `CURSOR_SHARED_WRITER_LEASE_INVALID`, `CURSOR_SHARED_WRITER_LEASE_UNAVAILABLE` | shared #1138 writer lease | Existing remote/local writer, malformed reservation, or unavailable lease ledger; no Cursor create |
| `CURSOR_SHARED_WRITER_LEASE_INVENTORY_UNVERIFIED`, `CURSOR_SHARED_WRITER_LEASE_INVENTORY_TRUNCATED` | shared #1138 writer lease | Ledger pagination or response is incomplete; admission fails closed |
| `CURSOR_SHARED_WRITER_LEASE_RECEIPT_UNVERIFIED` | shared #1138 writer lease | Cursor run receipt could not be bound to the persisted hold; hold remains active |
| `CURSOR_FORK_HEAD_UNSUPPORTED` | GitHub PR head ownership | Head branch resides outside canonical repository |
| `CURSOR_UNSAFE_TARGET_BRANCH`, `CURSOR_UNEXPECTED_BASE_BRANCH` | PR branch / base guard | Attempt would write main/master/empty head or operate against non-main base |
| `CURSOR_GITHUB_AUTH_MISSING`, `CURSOR_GITHUB_PR_HTTP_<status>` | GitHub PR evidence | Missing GitHub credential or PR REST error; no authoritative head validation |
| `CURSOR_GITHUB_PR_RESPONSE_INVALID`, `CURSOR_GITHUB_INCIDENT_RESPONSE_INVALID`, `CURSOR_CURRENT_MAIN_UNVERIFIED` | GitHub identity evidence | Required same-repository PR, incident, or current-main response is malformed |
| `CURSOR_API_KEY_MISSING`, `CURSOR_AUTH_OR_ACCESS_DENIED` | Cursor credential gate | No Cursor API key or failed provider authentication/permissions |
| `CURSOR_BILLING_OR_RATE_LIMIT_BLOCKED` | Cursor spending/rate control | Cursor rejects with HTTP 402/429; no claim of started agent |
| `CURSOR_AUTOMATIC_INCIDENT_ID_MISSING`, `CURSOR_AUTOMATIC_INCIDENT_NOT_OPEN` | trusted PR-label admission | No canonical linked incident or linked GitHub incident is not open |
| `CURSOR_GLOBAL_AGENT_INVENTORY_UNVERIFIED`, `CURSOR_GLOBAL_ACTIVE_AGENT_EXISTS` | account-wide cost/concurrency gate | Cursor account inventory is incomplete or another agent is active |
| `CURSOR_AGENT_INVENTORY_UNVERIFIED` | Cursor active-agent inspection | Missing, invalid or truncated PR-scoped agent list |
| `CURSOR_ACTIVE_PR_AGENT_EXISTS` | Cursor single-PR admission | An active Cloud Agent is already associated with that PR |
| `CURSOR_DUPLICATE_DISPATCH_SUPPRESSED` | Cursor idempotent create | Server reports `agent_id_conflict` on deterministic agent ID |
| `CURSOR_API_CONFLICT_UNRESOLVED`, `CURSOR_API_HTTP_<status>` | Cursor create / API | Unclassified 409 or other HTTP error; fail closed |
| `CURSOR_BAD_REQUEST_OR_REPOSITORY_ACCESS` | Cursor API preflight | Provider rejects request, target repository or configuration |
| `CURSOR_NETWORK_OR_RESPONSE_UNVERIFIED`, `CURSOR_API_RESPONSE_INVALID` | Remote evidence | Network, timeout, unparsable or non-object response |
| `CURSOR_DISPATCH_RECEIPT_UNVERIFIED` | Cursor enqueue confirmation | Create did not return matching agent ID and run ID |
| `CURSOR_CREATE_FAILED_NO_AGENT_PROVEN` | failed-launch reconciliation | Authenticated read-only probe (`GET /v1/agents/{id}` HTTP 404 plus complete archived global and PR inventories) proves no Cursor agent was created after a failed create attempt (catalog version `2026-10-31.2`; consumer parsers updated in lock-step in `engineering_remote_writer_release.py` and `cursor_terminal_status_receipt.py`) |
| `CURSOR_AGENT_STILL_ACTIVE`, `CURSOR_AGENT_EXISTS_REQUIRES_RUN_RECONCILIATION`, `CURSOR_AGENT_INVENTORY_CONTRADICTION` | failed-launch negative probe | Direct agent lookup or archived inventory shows the deterministic agent ID exists or is active; negative-launch release is refused and the writer hold remains active (catalog version `2026-10-31.2`) |
| `CURSOR_FAILED_LAUNCH_REQUIRES_NEGATIVE_PROOF_AND_RELEASE` | shared #1138 writer reconciliation | Held `CURSOR_LAUNCH_RESERVED` record without a `cursor_run_id` requires authenticated negative-launch proof and serialized release receipt before writer admission (catalog version `2026-10-31.2`; 0-second bypass tolerance; hold persists indefinitely until verified release, and resident supervisor recovery window is bounded to 900 seconds post-release) |

Positive research-only state: `CURSOR_AGENT_ENQUEUED` means Cursor accepted a create request and returned a valid agent/run receipt, **not** started code execution, merged changes, or independently verified production. Receipts keep `TASK_DISPATCHED`, `CURSOR_AGENT_STARTED`, `CODE_VERIFIED`, and `PRODUCTION_VERIFIED` as separate evidence states. These statuses do not override `V17_TERMINAL_REDUCER`. `can_execute=false` remains invariant.

Every Cursor create first persists a held reservation to the shared #1138 writer ledger while holding the existing implementation-single-writer Actions concurrency group. Provider workers recheck that ledger after acquiring the same group. The hold remains until remote termination and independent review are verified; ambiguous dispatches do not release it.


### Universal Intelligence paired research replay statuses — research only (#2059)

These local codes belong to `v17/universal_intelligence_paired_replay.py`. They describe caller-supplied, **unattested** champion/challenger/settlement research and are not V17 terminal-reducer, sportsbook, fitted-scorer, or production publication codes. Even successful descriptive metrics are not proof of a fitted challenger advantage.

| Code | Owning lane/stage | Meaning | Rank eligible? |
|---|---|---|---|
| `PAIR_REPLAY_INPUT_INVALID` | Intelligence shadow replay | Trial input is not a bounded list/tuple | No |
| `PAIR_REPLAY_MINIMUM_N_INVALID` | Intelligence shadow replay | A caller tries to weaken the minimum descriptive sample | No |
| `REPLAY_RECORD_INVALID` | Intelligence shadow replay | Nonrecord trial cannot be reconciled | No |
| `REPLAY_EXACT_IDENTITY_MISMATCH` | Intelligence shadow replay | Sport, event, market, period, selection, exact line or direction differs | No |
| `REPLAY_DUPLICATE_THESIS` | Intelligence shadow replay | Same exact event/market thesis repeated in one replay | No |
| `REPLAY_TIME_LEAKAGE_OR_MISMATCH` | Intelligence shadow replay | Forecast/feature timing is post-start, settlement before start, or event start differs | No |
| `REPLAY_NOT_HOLDOUT` | Intelligence shadow replay | Any input is not labeled HOLDOUT; label itself is not independent proof | No |
| `REPLAY_MISSING_IMMUTABLE_RECEIPT` | Intelligence shadow replay | A forecast claims no preevent prediction ID | No |
| `REPLAY_FORECAST_RECEIPTS_NOT_INDEPENDENT` | Intelligence shadow replay | Champion and challenger use the same purported receipt | No |
| `REPLAY_MODEL_PROVENANCE_MISSING` | Intelligence shadow replay | Missing claimed model-artifact identity | No |
| `REPLAY_MODEL_ARTIFACT_NOT_DISTINCT` | Intelligence shadow replay | Champion and challenger claim the same artifact | No |
| `REPLAY_FITTED_CHECKSUM_MISSING_OR_INVALID` | Intelligence shadow replay | Either alleged fitted artifact lacks an exact 64-character lowercase SHA-256 payload checksum; registry labels alone cannot establish independent model bytes | No |
| `REPLAY_FITTED_ARTIFACT_BYTES_NOT_DISTINCT` | Intelligence shadow replay | Champion and challenger claim the same immutable fitted payload checksum despite distinct artifact IDs, model versions or training-source hashes | No |
| `REPLAY_TRAINING_CUTOFF_UNVERIFIED` | Intelligence shadow replay | Training cutoff is missing/invalid or later than the forecast timestamp; descriptive historical pairing cannot be accepted | No |
| `REPLAY_PROBABILITY_PACKAGE_INVALID` | Intelligence shadow replay | Invalid scalar probability/lower-bound ordering | No |
| `REPLAY_SETTLEMENT_INVALID` | Intelligence shadow replay | Settled result is not a binary WIN/LOSS for the exact thesis | No |
| `REPLAY_UNSETTLED_OR_VOID` | Intelligence shadow replay | Outcome not final or explicitly void/push | No |
| `REPLAY_CANDIDATE_UNVERIFIED` | Intelligence shadow replay | Matched descriptive trial, still unauthenticated input | No |
| `NOT_ENOUGH_EVIDENCE` | Intelligence shadow replay | Too few eligible descriptive pairs; no model-improvement conclusion | No |
| `DESCRIPTIVE_SHADOW_METRICS_NOT_ATTESTED` | Intelligence shadow replay | Brier/log loss can be described for supplied rows, not independently validated or promotable | No |

Global invariants remain `V17_TERMINAL_REDUCER` sole publisher, `probability_publishable=false`, `can_execute=false`; no source grant, model artifact, or immutable prediction authenticity can be inferred from synthetic inputs.

## PropsMadness authenticated NFL browser-view research statuses

These failures belong only to the offline `propsmadness_nfl_account_view_adapter`. They do not grant account-data extraction, persistence, model-training, market-data, or probability authority.

| Code | Owning lane | Trigger condition | Model unavailable? |
| --- | --- | --- | --- |
| `PM_ACCOUNT_VIEW_TIMESTAMP_INVALID` | NFL account-view research | Capture timestamp missing, malformed, or timezone-naive | No |
| `PM_ACCOUNT_VIEW_SOURCE_INVALID` | NFL account-view research | Non-PropsMadness NFL URL, credential-bearing, malformed or unexpected query fields/aliases | No |
| `PM_ACCOUNT_VIEW_MARKET_CONFLICT` | NFL account-view research | Unsupported stat or conflicting selected market | No |
| `PM_ACCOUNT_VIEW_OPERATOR_CONFIRMATION_MISSING` | NFL account-view research | Operator has not asserted that the visible page was a signed-in account view | No |
| `PM_ACCOUNT_VIEW_INPUT_INVALID` | NFL account-view research | Empty, non-text, or oversized rendered browser view | No |
| `PM_ACCOUNT_VIEW_IDENTITY_UNRESOLVED` | NFL account-view research | Selected player label absent or malformed | No |
| `PM_ACCOUNT_VIEW_SECTION_MISSING` | NFL account-view research | No selected NFL player breakdown section | No |
| `PM_ACCOUNT_VIEW_SELECTED_MARKET_UNRESOLVED` | NFL account-view research | The selected player's visible detail block does not show the declared NFL statistic | No |
| `PM_ACCOUNT_VIEW_HISTORY_TABLE_INVALID` | NFL account-view research | Missing, ambiguous, malformed or oversized dated outcome table | No |
| `PM_ACCOUNT_VIEW_ROW_INVALID` | NFL account-view research | Missing fields, invalid dates/results/threshold, mismatched display direction, or result dated after capture | No |
| `PM_ACCOUNT_VIEW_DUPLICATE_GAME` | NFL account-view research | Same observed date/opponent appears twice in one selected player history | No |

This family is research-only; each failure preserves `probability_publishable=false` and `can_execute=false`. The chart comparison threshold is never a historical closing-line authority.

## PropsMadness NBA, MLB, WNBA account-view research statuses

These typed failures are emitted only by `propsmadness_other_sports_account_view_adapter`, never by governed fitted scorers. The same account-view timestamp validation code `PM_ACCOUNT_VIEW_TIMESTAMP_INVALID` from the NFL parser is reused.

| Code | Owner | Exact cause | Model unavailable? |
| --- | --- | --- | --- |
| `PM_MULTISPORT_SPORT_UNSUPPORTED` | Account-view research | Sport is not NBA, MLB or WNBA | No |
| `PM_MULTISPORT_MARKET_CONFLICT` | Account-view research | Unsupported stat or market URL contradicts requested stat | No |
| `PM_MULTISPORT_OPERATOR_CONFIRMATION_MISSING` | Account-view research | User-controlled signed-in view confirmation absent | No |
| `PM_MULTISPORT_INPUT_INVALID` | Account-view research | Missing or oversized rendered-view text | No |
| `PM_MULTISPORT_PLAYER_UNRESOLVED` | Account-view research | Player label missing or cannot be found near selected chart | No |
| `PM_MULTISPORT_SOURCE_INVALID` | Account-view research | Unallowlisted URL, duplicate query key, credential component or nonnumeric provider aliases | No |
| `PM_MULTISPORT_SECTION_MISSING` | Account-view research | Exact selected-sport breakdown section absent | No |
| `PM_MULTISPORT_HISTORY_TABLE_INVALID` | Account-view research | Chart table missing, duplicate, wrong header, empty or over limit | No |
| `PM_MULTISPORT_SELECTED_MARKET_UNRESOLVED` | Account-view research | Declared statistic does not match player detail label | No |
| `PM_MULTISPORT_ROW_INVALID` | Account-view research | Bad date/opponent/value/line, future completed result, or displayed comparison-side mismatch | No |
| `PM_MULTISPORT_DUPLICATE_GAME` | Account-view research | Duplicate date/opponent row within one player's chart | No |

These are synthetic-layout research contracts until verified against distinct real authenticated NBA, MLB and WNBA pages; none permits storage, training, market claims, probability publication or execution.

## NHL official boxscore player-game history research

These typed errors belong to the offline `nhl_player_box_outcome_research` adapter. They do not imply model capability or replace governing specialist failures.

| Code | Exact research condition | Model unavailable? |
| --- | --- | --- |
| `NHL_PLAYER_BOX_SCHEMA_INVALID` | Required official boxscore/side/player array missing | No |
| `NHL_PLAYER_BOX_IDENTITY_MISMATCH` | Official event ID, team identity or supplied schedule start conflicts | No |
| `NHL_PLAYER_BOX_NOT_FINAL_REGULAR_SEASON` | No final regular-season game / incorrect NHL season | No |
| `NHL_PLAYER_BOX_TIMESTAMP_INVALID` | Missing, invalid or timezone-naive source/capture time | No |
| `NHL_PLAYER_BOX_NOT_POSTGAME` | Source capture precedes claimed game start | No |
| `NHL_PLAYER_BOX_PLAYER_ID_INVALID` | Official player ID missing or nonpositive | No |
| `NHL_PLAYER_BOX_DUPLICATE_PLAYER` | A player occurs in more than one boxscore position/side | No |
| `NHL_PLAYER_BOX_STAT_INVALID` | Missing, negative, non-integer or excessive stat count | No |
| `NHL_PLAYER_BOX_STAT_CONTRADICTION` | Points differ from goals plus assists or saves exceed shots against | No |
| `NHL_PLAYER_BOX_NO_PLAYERS` | Final boxscore has no typed players | No |

This new path is **research-only**; `NHL_PUBLIC_API` remains `LICENSE_REVIEW_REQUIRED` for governed historical training. `can_execute=false`; no sportsbook lines or model probabilities are generated.
