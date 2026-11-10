# WOW V17 Canonical Failure-Code Registry

This file is the repository source of truth for governed typed failure/status codes introduced or relied on by the V17 full-board stabilization work. New typed failure codes must be registered in the same change that introduces them.

`can_execute=false` remains invariant. Market/evidence failures never imply a sporting-model capability failure.

Successful and informational NHL dispositions are catalogued separately in
`docs/nhl_status_registry.md`; they are not failure codes and grant no authority.

Historical/retired QA finding: `QA_TRUST_ROOT_OWNER_AUTHORED` remains preserved in pre-V3 receipts, but is no longer emitted by V3 QA. It is intentionally excluded from the active emitted-code equality table below.

| Code | Owning lane/stage | Meaning | Rank eligible? |
|---|---|---|---:|
| `PROP_LATEST_SNAPSHOT_AMBIGUOUS` | governed prop evidence latest-snapshot read | The two newest exact-identity observations have equal capture instants (including equivalent timezone offsets), or the maximum timestamp cannot be verified. Source UUID ordering is not evidence chronology. Reject any attempt to select an arbitrary clean snapshot, keep the row nonpublishable, and investigate authoritative source ordering/dedupe. **Recovery class:** SOURCE_AUTHORITY_CONFLICT, no retry without new official evidence or corrected immutable lineage. | No |
| `NFL_FORWARD_PAGINATION_INVALID` | NFL prospective forward grading and calibration health | PostgREST page reads lacked stable primary-key order or contained missing/duplicate prediction, settled-game or existing-grade identities. Fail the entire shadow-grading run before appending misleading calibration results; not a fitted-model capability error. | No |
| `BASKETBALL_CALIBRATION_HOLDOUT_OVERLAP` | NBA/WNBA shadow fitter | Candidate artifact training counts/date boundary fail to partition the raw feature corpus into disjoint calibration and untouched holdout sets. Reject the fit and retain nonpublishable state. | No |
| `BASKETBALL_CALIBRATED_HOLDOUT_INVALID` | NBA/WNBA shadow fitter | The reserved postcalibration holdout cannot produce valid finite numerical probabilities or binary outcomes. Preserve the scorer/data-integrity failure and do not certify. | No |
| `NCAAF_FEATURE_PAGINATION_INVALID` | NCAAF pregame training feature compiler | A stable primary-key-paginated training/evidence page contains a missing or duplicated source-row ID. Reject the entire partial compilation; do not fit on corrupted/incomplete evidence. This is an input-integrity failure, not `MODEL_UNAVAILABLE`. | No |
| `DISCOVERY_MAIN_SHA_INVALID_HOLD` | Engineering App source discovery | The caller supplied a malformed purported current protected-main SHA; reject before any GitHub source inventory or writer admission. | No |
| `SCOUT_DISCOVERY_ARTIFACT_UNOBTAINABLE` | Scout persistence / cancelled-writer source reconciliation | Non-expired discovery artifact is absent for a source referenced by a cancelled persistence writer. Allowed bounded context: numeric `source_run_id` and authenticated discovery-job status only, no payload bytes. **Recovery class:** `NOT_APPLICABLE` only when GitHub proves the unique discovery job was completed/skipped; continue oldest-first without falsely claiming a persisted receipt. Otherwise `EVIDENCE_PENDING`: fail closed and investigate source artifacts/jobs before any replay, never fabricate source evidence. **Next action:** verify the source run's immutable jobs and discovery artifact inventory; repair upstream collection if discovery actually ran. | No |
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
| `RELEASE_QA_DRAFT_TARGET_NOT_RELEASABLE` | release authority / workflow_run admission | Opt-in only (`--neutral-if-draft-target`). The authenticated policy resolves to a same-repository, main-based PR that is still an open draft at the exact policy head. GitHub cannot merge a draft, so no Release check is published and the unrelated protected-main commit is not failed. A non-draft, moved, closed, foreign or invalid target keeps the strict path. | No |
| `RELEASE_QA_SOURCE_PR_SUPERSEDED` | release authority / workflow_run admission | The complete authenticated QA run/policy identifies a same-repository, main-based source PR that was subsequently closed or advanced to a different valid head. **Neutral only for the automatic historical workflow_run**: no release check is published and no PASS is asserted. A current PR head, invalid/foreign identity, stale artifact, or manual release stays fail-closed. Prevention: superseded-policy negative tests and exact-source required checks. | No |
| `QA_SOURCE_PR_HEAD_SUPERSEDED` | independent QA / workflow_run admission | Authenticated upstream event refers to a single main-based same-repository PR whose verified live head has changed or whose PR has closed. The obsolete event exits neutral **before** QA policy/decision/check creation, leaving any real current-head QA gate untouched. Foreign/malformed/ambiguous targets and explicit manual requests still fail closed. | No |
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

| Code | Exact research condition | Emitting module | Also emitted by | Model unavailable? |
|---|---|---|---|---|
| `NHL_PLAYER_BOX_SCHEMA_INVALID` | Required official boxscore/side/player array missing | `v17/nhl_player_box_outcome_research.py` | — | No |
| `NHL_PLAYER_BOX_IDENTITY_MISMATCH` | Official event ID, team identity or supplied schedule start conflicts | `v17/nhl_player_box_outcome_research.py` | — | No |
| `NHL_PLAYER_BOX_NOT_FINAL_REGULAR_SEASON` | No final regular-season game / incorrect NHL season | `v17/nhl_player_box_outcome_research.py` | — | No |
| `NHL_PLAYER_BOX_TIMESTAMP_INVALID` | Missing, invalid or timezone-naive source/capture time | `v17/nhl_player_box_outcome_research.py` | — | No |
| `NHL_PLAYER_BOX_NOT_POSTGAME` | Source capture precedes claimed game start | `v17/nhl_player_box_outcome_research.py` | — | No |
| `NHL_PLAYER_BOX_PLAYER_ID_INVALID` | Official player ID missing or nonpositive | `v17/nhl_player_box_outcome_research.py` | — | No |
| `NHL_PLAYER_BOX_DUPLICATE_PLAYER` | A player occurs in more than one boxscore position/side | `v17/nhl_player_box_outcome_research.py` | — | No |
| `NHL_PLAYER_BOX_STAT_INVALID` | Missing, negative, non-integer or excessive stat count | `v17/nhl_player_box_outcome_research.py` | — | No |
| `NHL_PLAYER_BOX_STAT_CONTRADICTION` | Points differ from goals plus assists or saves exceed shots against | `v17/nhl_player_box_outcome_research.py` | — | No |
| `NHL_PLAYER_BOX_NO_PLAYERS` | Final boxscore has no typed players | `v17/nhl_player_box_outcome_research.py` | — | No |

This new path is **research-only**; `NHL_PUBLIC_API` remains `LICENSE_REVIEW_REQUIRED` for governed historical training. `can_execute=false`; no sportsbook lines or model probabilities are generated.

## V17 candidate-only recalibration research input failures

These are typed research-input rejections from `v17/refit_scheduler.py`, never model capability or Action failures. All preserve `can_execute=false`, `probability_publishable=false`; no champion/production mutation follows. The retrospective diagnostic is **not** an independent promotion holdout.

| Code | Owning lane | Condition | Rank eligible? |
|---|---|---|---:|
| `REFIT_PROPOSAL_TYPE_UNSUPPORTED` | V17 advisory refit scheduler | Proposal is not an exact RECALIBRATION_CHALLENGER. | No |
| `REFIT_PROPOSAL_SAFETY_INVALID` | V17 advisory refit scheduler | Proposal lacks mandatory nonpromotion or nonexecution safety flags. | No |
| `REFIT_PROPOSAL_IDENTITY_REQUIRED` | V17 advisory refit scheduler | Proposal ID or exact cohort key is missing. | No |
| `REFIT_EVIDENCE_FLOOR_INVALID` | V17 advisory refit scheduler | Requested fit/diagnostic event floor is below the candidate-only evidence minimum. | No |
| `REFIT_EVENT_OR_PREDICTION_ID_MISSING` | V17 advisory refit scheduler | Immutable prediction ID or official event ID is missing. | No |
| `REFIT_DUPLICATE_PREDICTION` | V17 advisory refit scheduler | One prediction ID occurs multiple times in the hydrated cohort. | No |
| `REFIT_COHORT_MISMATCH` | V17 advisory refit scheduler | Source forecast does not match the exact proposal cohort. | No |
| `REFIT_NOT_CALIBRATED_FORECAST` | V17 advisory refit scheduler | Frozen input probability is not attributed to calibrated_probability. | No |
| `REFIT_BINARY_SETTLEMENT_REQUIRED` | V17 advisory refit scheduler | Exact officially settled outcome is not a nonboolean binary 0/1. | No |
| `REFIT_MODEL_TIME_REQUIRED` | V17 advisory refit scheduler | Pregame model timestamp is missing, invalid or timezone naive. | No |
| `REFIT_EVENT_TIME_REQUIRED` | V17 advisory refit scheduler | Official event timestamp is missing, invalid or timezone naive. | No |
| `REFIT_SETTLEMENT_TIME_REQUIRED` | V17 advisory refit scheduler | Official settlement timestamp is missing, invalid or timezone naive. | No |
| `REFIT_TEMPORAL_IDENTITY_INVALID` | V17 advisory refit scheduler | Model timestamp is not strictly pregame or settlement precedes event. | No |
| `REFIT_EVENT_TIME_CONFLICT` | V17 advisory refit scheduler | Same official event ID has inconsistent event-start timestamps. | No |
| `REFIT_PROBABILITY_INVALID` | V17 advisory refit scheduler | Frozen calibrated forecast probability is not a finite number strictly between 0 and 1. | No |
| `REFIT_EVENT_SAMPLE_INSUFFICIENT` | V17 advisory refit scheduler | Chronologically distinct events fall below candidate-only train plus diagnostic floor. | No |
| `REFIT_MATURED_SAMPLE_INSUFFICIENT` | V17 advisory refit scheduler | Enough fully settled pre-cutoff training events or diagnostic events are unavailable. | No |


## Reviewer provider compatibility — owner-only read-only preflight

These failure codes preserve the pre-existing protected semantic-review gate. A
preflight GET validates only provider compatibility; it produces no QA receipt,
independent security PASS, merger authorization, probability or market order.
Recover only through authenticated protected-main workflow execution.

| Code | Owner / stage | Recovery class | Bounded safe context | Next action |
| --- | --- | --- | --- | --- |
| `REVIEWER_PREFLIGHT_OWNER_MAIN_REQUIRED` | Owner / QA preflight admission | EXTERNAL_AUTHORIZATION | GitHub event type and trusted main identity only | Dispatch owner-only diagnostic from protected main |
| `GEMINI_MODEL_INVALID` | QA reviewer / model selection | CONFIGURATION | Expected allowlisted model ID, never secret | Remove explicit stale model variable or bind reviewed stable model |
| `REVIEWER_PREFLIGHT_CREDENTIAL_MISSING` | QA environment / preflight | PROVIDER_CREDENTIAL | Credential present/missing boolean only | Restore existing scoped QA provider credential |
| `REVIEWER_PREFLIGHT_MODEL_CAPABILITY_UNVERIFIED` | QA provider / metadata GET | PROVIDER_CONFIGURATION | Model identity and `generateContent` supported boolean only | Verify project entitlement and exact supported method |
| `PROVIDER_MODEL_UNAVAILABLE` | QA provider / review | PROVIDER_CONFIGURATION | Structured Google NOT_FOUND for API version/method; no response text or keys | Use owner-approved model selection and a fresh independent exact-head review |
| `HTTP_404` | QA provider / review or preflight | DIAGNOSE | Fixed allowlisted endpoint label and HTTP status only | Attribute endpoint/model/credential; do not infer model retirement or semantic REJECT |
| `REVIEWER_CLI_ARGUMENT_INVALID` | QA workflow / preflight entrypoint | CONFIGURATION | Argument shape only | Invoke `--preflight` or normal review through trusted workflow |

A structured model failure may allow the existing reviewer fallback mechanism,
but only a genuine independent semantic PASS on the exact source SHA qualifies as
security evidence. A prior signed REJECT remains binding at that SHA.


## Reviewer model discovery — bounded credential-scoped candidates

No new QA App check, merge gate, approval, spending limit, or probability output
is created by model discovery. The credential-scoped GET lists provider models;
only stable, exact, approved family IDs are eligible. The model-ID allowlist is
anchored (including end of string), and the reviewer logs selected/unavailable
model IDs only, never prompts, API keys, raw response data, or URLs. At most
three approved candidates are attempted, only after typed
`PROVIDER_MODEL_UNAVAILABLE`; a substantive REJECT/malformed answer is terminal.

| Typed code | Owner / stage | Recovery class | Bounded safe context | Required next action |
| --- | --- | --- | --- | --- |
| `REVIEW_MODEL_CATALOG_INVALID` | QA provider selection / GET models | FAIL_CLOSED | Provider label and invalid shape only | Repair provider response contract; do not guess from static models |
| `REVIEW_MODEL_CATALOG_PAGINATION_REQUIRED` | QA provider selection / GET models | FAIL_CLOSED | Provider label and nonempty next-page boolean only | Add governed bounded pagination before using incomplete catalog |
| `REVIEWER_PREFLIGHT_NO_AVAILABLE_MODEL` | QA owner-only preflight | PROVIDER_CONFIGURATION | Provider/model readiness labels only | Verify existing credential entitlement or approved model availability |
| `PROVIDER_MODEL_UNAVAILABLE` | QA provider model availability | PROVIDER_CONFIGURATION | Reviewed exact model ID only | Try next approved credential-listed model within same provider; after bounded exhaustion retain existing authenticated owner fallback |
| `GEMINI_MODEL_INVALID` / `CLAUDE_MODEL_UNAPPROVED` | QA reviewer / configured model | FAIL_CLOSED | Sanitized model pattern failure only | Correct explicitly unapproved variable via governed trust-root change |

The optional `WOW_QA_GEMINI_MODEL`/`WOW_QA_CLAUDE_MODEL` preference
is honored only when listed under the credential and approved family.
A failed transient model catalog GET may use a short *static approved*
candidate list for normal review; owner preflight has `strict=True` and
may report `PROVIDER_READY` only with real live catalog proof.
A valid empty catalog, malformed listing, authentication failure, or
partial paginated listing cannot be treated as approved availability.
Changing source invalidates prior owner review; no code may forge the
current-head approval or a signed independent semantic PASS.

## D1 certification replay split-metadata integrity (NHL #1349)

| Code | Owning lane/stage | Exact condition | Rank eligible? |
|---|---|---|---:|
| `CANDIDATE_PARTITION_METRICS_MISMATCH` | team/event certification replay (read-only) | Persisted fitted D1 train/calibration/test partitions disagree with candidate `validation_metrics.train_n/calibration_n/test_n`, or partial/malformed fitted split metrics are supplied. A historical source-review or replay receipt cannot override the discrepancy; retain the candidate as immutable, fail the replay and require a new verified candidate. | No |



## NCAAB current-event schedule identity (#2550)

Discovery-only failure codes; they do not authorize fitted probabilities or publication. An unresolved row stays `EVENT_IDENTITY_UNRESOLVED` and `can_execute=false`.

| Typed code | Owning lane | Recovery class | Rank eligible? |
| --- | --- | --- | --- |
| `NCAAB_EVENT_TIME_INVALID` | NCAAB identity | INPUT_INVALID | No |
| `NCAAB_PROVIDER_TEAM_ALIAS_INVALID` | NCAAB identity | INPUT_INVALID | No |
| `NCAAB_PROVIDER_TEAM_ALIAS_MISSING` | NCAAB handoff | SOURCE_INCOMPLETE | No |
| `NCAAB_CANONICAL_EVENT_NOT_FOUND` | NCAAB identity | SOURCE_MISMATCH | No |
| `NCAAB_CANONICAL_EVENT_AMBIGUOUS` | NCAAB identity | SOURCE_AMBIGUOUS | No |
| `NCAAB_CANONICAL_EVENT_ID_MISSING` | NCAAB handoff | SOURCE_MALFORMED | No |
| `NCAAB_SCHEDULE_SOURCE_NOT_READY` | NCAAB source | SOURCE_NOT_READY | No |
| `NCAAB_SCHEDULE_SOURCE_UNAVAILABLE` | NCAAB source | SOURCE_UNAVAILABLE | No |
| `NCAAB_SCHEDULE_SOURCE_RATE_LIMITED` | NCAAB source | TRANSIENT | No |
| `NCAAB_SCHEDULE_SCHEMA_DRIFT` | NCAAB source | SOURCE_MALFORMED | No |

## Simple V1 deterministic delivery re-entry

These are read-only delivery **decisions**, not fitted-model failure substitutes,
approval checks, or new release gates. Only the trusted QA App on the exact SHA
can create independent review evidence. A prior signed semantic REJECT is binding
on that SHA even when a later attempt produces a provider HOLD.

| Decision / typed reason | Owner / stage | Recovery class | Safe context | Required next action |
| --- | --- | --- | --- | --- |
| `SEMANTIC_REJECT_SAME_SHA` | Independent QA / PR continuation | CODE_REPAIR | QA App ID, exact PR/SHA, signed check title | Repair findings on a new source SHA, then independent review |
| `REVIEWER_MODEL_REPAIR` | QA provider / preflight | PROVIDER_CONFIGURATION | Explicit typed model-unavailable reason, no response body | Verify supported model and authorized credential, then obtain a new signed review |
| `REVIEWER_PROVIDER_DIAGNOSIS` | QA provider / preflight | PROVIDER_CONFIGURATION | Ambiguous bare HTTP 404 only | Prove endpoint, model and credential scope with authorized preflight before any reviewer retry |
| `REVIEWER_AUTH_OR_QUOTA_REPAIR` | QA provider / preflight | PROVIDER_CONFIGURATION | Typed provider credit / 401, no credentials or billing data | Repair existing entitlement or credential without new vendor/spend |
| `REVIEWER_TRANSIENT_OUTAGE` | QA provider / review | TRANSIENT | Bounded 429/5xx/transport code only | One bounded retry after provider recovery, never on signed REJECT |
| `OWNER_REVIEW_REQUIRED` | Owner / protected release | EXTERNAL_AUTHORIZATION | Exact PR/SHA and established owner-fallback reason | Authenticated independent owner review where policy allows, then fresh exact-head QA |
| `INDEPENDENT_QA_NOT_ACCEPTED` | Independent QA / PR continuation | FAIL_CLOSED | Unknown/invalid/missing definitive review evidence | Diagnose signed QA receipt; no automatic override |
| `EVIDENCE_PENDING` | Engineering controller / PR continuation | RETRY_ON_NEW_EVIDENCE | Missing/pending exact-head QA check | Re-enter on signed QA App check or explicit authorized owner dispatch |

The existing controller performs no automatic protected merge, no semantic
self-approval, no live betting and no unbounded provider retry. Production DONE
requires authentic merged/live/verified SHA parity, healthy route and persisted
business result; a green workflow is not proof of production delivery.

## Offline MLflow/Evidently research-receipt preflight (incident #2536)

Read-only research diagnostics only: the codes below have **no probability rank eligibility**, no model-certification significance, and never substitute for a scorer error. Only research manifest validation and local reporting emit them. A reported hold cannot be repaired with an odds-derived or heuristic prediction; preserve original candidate artifacts and source bytes.

| Code | Owning lane/stage | Condition and recovery class | Rank eligible? |
|---|---|---|---:|
| `RESEARCH_MANIFEST_UNREADABLE` | V17 research telemetry / local receipt preflight | Manifest missing or not valid JSON. Recovery: `EVIDENCE_PENDING`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_MANIFEST_INVALID` | V17 research telemetry / local receipt preflight | Missing model-family or sport identity. Recovery: `CODE_REPAIR`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_FILE_MANIFEST_INVALID` | V17 research telemetry / local receipt preflight | Relative path or SHA256 declaration is invalid. Recovery: `EVIDENCE_PENDING`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_FILE_OUTSIDE_ROOT` | V17 research telemetry / local receipt preflight | Referenced source escapes research workspace. Recovery: `EVIDENCE_PENDING`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_FILE_UNREADABLE` | V17 research telemetry / local receipt preflight | Source or artifact bytes cannot be read. Recovery: `EVIDENCE_PENDING`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_FILE_SHA256_MISMATCH` | V17 research telemetry / local receipt preflight | Actual file bytes differ from pinned expected hash. Recovery: `EVIDENCE_PENDING`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_SOURCE_SNAPSHOTS_MISSING` | V17 research telemetry / local receipt preflight | No source archive files are provided. Recovery: `EVIDENCE_PENDING`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_SOURCE_SNAPSHOT_DUPLICATE` | V17 research telemetry / local receipt preflight | Duplicate source archive path in manifest. Recovery: `CODE_REPAIR`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_PREDICTIONS_UNREADABLE` | V17 research telemetry / local receipt preflight | Predictions CSV cannot be read. Recovery: `EVIDENCE_PENDING`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_PREDICTION_SCHEMA_INVALID` | V17 research telemetry / local receipt preflight | Required per-event columns missing. Recovery: `CODE_REPAIR`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_COHORT_EMPTY` | V17 research telemetry / local receipt preflight | No original fitted predictions available. Recovery: `EVIDENCE_PENDING`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_EVENT_IDENTITY_MISSING_OR_DUPLICATE` | V17 research telemetry / local receipt preflight | Missing or repeated official event identity. Recovery: `EVIDENCE_PENDING`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_SPLIT_UNKNOWN` | V17 research telemetry / local receipt preflight | Unexpected HOLDOUT or FORWARD partition value. Recovery: `CODE_REPAIR`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_SPLIT_MISSING` | V17 research telemetry / local receipt preflight | Either HOLDOUT or FORWARD split missing. Recovery: `EVIDENCE_PENDING`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_PREDICTION_OR_LABEL_INVALID` | V17 research telemetry / local receipt preflight | Nonfinite/out-of-range probability or nonbinary settlement. Recovery: `EVIDENCE_PENDING`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_TIMESTAMP_INVALID` | V17 research telemetry / local receipt preflight | Invalid, naive, or overflowing timestamp. Recovery: `EVIDENCE_PENDING`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_CUTOFF_OR_SETTLEMENT_INVALID` | V17 research telemetry / local receipt preflight | Forecast not strictly pre-start or label before start. Recovery: `EVIDENCE_PENDING`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_SPLIT_CHRONOLOGY_OVERLAP` | V17 research telemetry / local receipt preflight | Holdout event chronology overlaps forward cohort. Recovery: `EVIDENCE_PENDING`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_LABEL_LEAKAGE` | V17 research telemetry / local receipt preflight | Holdout labels available after first forward forecast. Recovery: `EVIDENCE_PENDING`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_OUTPUT_ROOT_INVALID` | V17 research telemetry / local receipt preflight | Reports target input-root or tracking-root. Recovery: `CODE_REPAIR`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_OUTPUT_OVERLAPS_INPUT` | V17 research telemetry / local receipt preflight | Reports would overwrite pinned source, predictions, manifest, or artifact. Recovery: `CODE_REPAIR`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_TRACKING_OVERLAPS_OUTPUT` | V17 research telemetry / local receipt preflight | Local tracking and report trees nest recursively. Recovery: `CODE_REPAIR`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_OUTPUT_ROOT_NOT_EMPTY` | V17 research telemetry / local receipt preflight | Report destination contains previous or unrelated files. Recovery: `CODE_REPAIR`; investigate the exact files/environment, then use a new distinct output directory. | No |
| `RESEARCH_MLFLOW_OR_EVIDENTLY_NOT_INSTALLED` | V17 research telemetry / local receipt preflight | Optional reporting dependency unavailable; no receipt generated. Recovery: `PROVIDER_CONFIGURATION`; investigate the exact files/environment, then use a new distinct output directory. | No |

## NHL pregame starter-goalie evidence pipeline (November 2026)

Research-only evidence chain: two server-owned official starter feeds, an
independent confirmation contract, the pregame evidence gate, and the
collect → persist → gate orchestration. None of these codes is
`MODEL_UNAVAILABLE`: a feed, timestamp, identity or persistence failure never
proves fitted-model absence. A passing gate still means only input completeness.
`can_execute=false` and `probability_publishable=false` remain invariant.

### Starter-feed adapters (`v17/nhl_starter_feed_adapters.py`)

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---:|
| `NHL_STARTER_FEED_ENDPOINT_UNCONFIGURED` | Neither an explicit endpoint nor the provider deployment variable is set. Adapter construction fails closed before any request. | `v17/nhl_starter_feed_adapters.py` | — | No |
| `NHL_STARTER_FEED_ENDPOINT_NOT_ALLOWLISTED` | Endpoint is not HTTPS, its host is outside the provider allowlist, or it carries userinfo/a fragment. Rejected before any request. | `v17/nhl_starter_feed_adapters.py` | — | No |
| `NHL_STARTER_FEED_TRANSPORT_FAILED` | Timeout, connection, DNS or other transport exception during collection. Acquisition failure only; never a model failure. | `v17/nhl_starter_feed_adapters.py` | — | No |
| `NHL_STARTER_FEED_REDIRECT_REJECTED` | Upstream returned a 3xx response. Redirects are never followed. | `v17/nhl_starter_feed_adapters.py` | — | No |
| `NHL_STARTER_FEED_HTTP_STATUS` | Non-200 response (including 401/403/429/5xx) or a response URL that differs from the requested allowlisted URL. | `v17/nhl_starter_feed_adapters.py` | — | No |
| `NHL_STARTER_FEED_PAYLOAD_INVALID` | Empty body or bytes that are not valid JSON. | `v17/nhl_starter_feed_adapters.py` | — | No |
| `NHL_STARTER_FEED_PAYLOAD_TOO_LARGE` | Response body exceeds the bounded 512 KiB adapter cap. | `v17/nhl_starter_feed_adapters.py` | — | No |
| `NHL_STARTER_FEED_SCHEMA_DRIFT` | JSON root is not an object, a required contract field/shape is missing, or the event ID is malformed. | `v17/nhl_starter_feed_adapters.py` | — | No |
| `NHL_STARTER_FEED_EVENT_MISMATCH` | Receipt event ID differs from the server-owned canonical identity. | `v17/nhl_starter_feed_adapters.py` | — | No |
| `NHL_STARTER_FEED_START_TIME_MISMATCH` | Receipt kickoff differs from the canonical start by more than 60 seconds. | `v17/nhl_starter_feed_adapters.py` | — | No |
| `NHL_STARTER_FEED_TEAM_MISMATCH` | Receipt home/away abbreviations differ from the canonical identity. | `v17/nhl_starter_feed_adapters.py` | — | No |
| `NHL_STARTER_FEED_TEAM_NOT_IN_EVENT` | League roster feed designates a starter for a team outside this event. | `v17/nhl_starter_feed_adapters.py` | — | No |
| `NHL_STARTER_FEED_TIMESTAMP_MISSING` | Neither the payload observation time nor the HTTP `Date` header supplies a timezone-aware timestamp. | `v17/nhl_starter_feed_adapters.py` | — | No |
| `NHL_STARTER_FEED_TIMESTAMP_IN_FUTURE` | Observation time is later than the run instant. | `v17/nhl_starter_feed_adapters.py` | — | No |
| `NHL_STARTER_FEED_AFTER_KICKOFF` | Observation time is at or after the official start. | `v17/nhl_starter_feed_adapters.py` | — | No |
| `NHL_STARTER_FEED_STALE` | Observation is older than the 90-minute pregame confirmation window. | `v17/nhl_starter_feed_adapters.py` | — | No |
| `NHL_STARTER_FEED_GOALIE_MISSING` | A side has no numeric starting-goalie identity, or the league feed has no `STARTER` for a team. | `v17/nhl_starter_feed_adapters.py` | — | No |
| `NHL_STARTER_FEED_GOALIE_AMBIGUOUS` | The same goalie is claimed for both sides, or a team has more than one `STARTER`. | `v17/nhl_starter_feed_adapters.py` | — | No |
| `NHL_STARTER_FEED_STATUS_MISSING` | Starter status is absent on a side. | `v17/nhl_starter_feed_adapters.py` | — | No |
| `NHL_STARTER_FEED_STATUS_DIVERGENT` | Home and away starter statuses differ, so one aggregate status cannot be claimed. | `v17/nhl_starter_feed_adapters.py` | — | No |

### Pregame Gamecenter source capture (`v17/nhl_pregame_gamecenter_capture.py`)

This adapter captures an official pregame boxscore under the canonical identity
gate. A pregame boxscore proves roster/participation, never who starts in goal,
so a fully successful capture still terminates as a HOLD. All outcomes keep
`probability_publishable=false`, `rank_eligible=false`, `can_execute=false`.

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---:|
| `NHL_GAMECENTER_EVENT_ID_INVALID` | Supplied event ID is not exactly 10 ASCII digits. Rejected before identity validation or any fetch. | `v17/nhl_pregame_gamecenter_capture.py` | — | No |
| `NHL_OFFICIAL_GAMECENTER_SOURCE_UNOBTAINABLE` | Transport exception, non-200 response, or a response URL that differs from the requested allowlisted Gamecenter URL. No payload is accepted. | `v17/nhl_pregame_gamecenter_capture.py` | — | No |
| `NHL_OFFICIAL_GAMECENTER_RATE_LIMITED` | Upstream returned HTTP 429. Acquisition failure only, never a model failure. | `v17/nhl_pregame_gamecenter_capture.py` | — | No |
| `NHL_OFFICIAL_GAMECENTER_REDIRECT_REJECTED` | Upstream returned a 3xx response. Redirects are never followed. | `v17/nhl_pregame_gamecenter_capture.py` | — | No |
| `NHL_OFFICIAL_GAMECENTER_PAYLOAD_INVALID` | Body is empty, exceeds the 2 MiB capture cap, or is not valid JSON. | `v17/nhl_pregame_gamecenter_capture.py` | — | No |
| `NHL_OFFICIAL_GAMECENTER_SCHEMA_DRIFT` | Decoded JSON root is not an object. | `v17/nhl_pregame_gamecenter_capture.py` | — | No |
| `NHL_GAMECENTER_CANONICAL_IDENTITY_MISMATCH` | Payload event ID or home/away abbreviations differ from the canonical identity receipt. | `v17/nhl_pregame_gamecenter_capture.py` | — | No |
| `NHL_GAMECENTER_START_TIME_MISMATCH` | Payload kickoff is missing or differs from the canonical start by more than 30 minutes. | `v17/nhl_pregame_gamecenter_capture.py` | — | No |
| `NHL_GOALIE_STARTER_CONFIRMATION_REQUIRED` | Capture, identity, timestamp and `gameState=FUT` checks all passed, but the payload is only a pregame boxscore. It cannot establish the starting goalie, so the result is HELD with `goalie_status=UNVERIFIED`, `goalie_confirmation_status=NOT_ESTABLISHED`, and the source hash retained. Research-only; never certification. | `v17/nhl_pregame_gamecenter_capture.py` | — | No |

The capture also emits `NHL_CANONICAL_IDENTITY_RECEIPT_MISSING`,
`NHL_CANONICAL_IDENTITY_RECEIPT_MISMATCH` and `NHL_EVENT_NOT_PREGAME`, which are
registered once under the gate/confirmation tables above.

### Independent confirmation (`v17/nhl_starter_goalie_confirmation.py`)

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---:|
| `NHL_CANONICAL_IDENTITY_RECEIPT_MISSING` | No server-owned canonical identity receipt was supplied. A caller field is not authoritative identity. | `v17/nhl_starter_goalie_confirmation.py` | `v17/nhl_pregame_evidence_boundary.py` | No |
| `NHL_CANONICAL_IDENTITY_RECEIPT_MISMATCH` | Identity provider/source/event/start/teams/flags/staleness fail validation. | `v17/nhl_starter_goalie_confirmation.py` | `v17/nhl_pregame_evidence_boundary.py` | No |
| `NHL_EVENT_NOT_PREGAME` | The canonical start is not in the future. | `v17/nhl_starter_goalie_confirmation.py` | `v17/nhl_pregame_evidence_boundary.py` | No |
| `NHL_GOALIE_INDEPENDENT_CONFIRMATION_INSUFFICIENT` | Fewer than two usable confirmation receipts were supplied. | `v17/nhl_starter_goalie_confirmation.py` | — | No |
| `NHL_GOALIE_CONFIRMATION_EVIDENCE_INCOMPLETE` | Aggregate wrapper for one or more per-source blockers; the specific blocker codes remain in the receipt. | `v17/nhl_starter_goalie_confirmation.py` | — | No |
| `NHL_GAMECENTER_PARTICIPATION_NOT_CONFIRMATION` | Source kind is Gamecenter boxscore participation, which never proves who starts in goal. | `v17/nhl_starter_goalie_confirmation.py` | — | No |
| `NHL_GOALIE_CONFIRMATION_SOURCE_KIND_UNSUPPORTED` | Source kind is not an approved starter designation, lineup or roster designation. | `v17/nhl_starter_goalie_confirmation.py` | — | No |
| `NHL_GOALIE_CONFIRMATION_PROVIDER_NOT_OFFICIAL` | Provider is outside the official confirmation allowlist. | `v17/nhl_starter_goalie_confirmation.py` | — | No |
| `NHL_GOALIE_CONFIRMATION_SNAPSHOT_MISSING` | Source snapshot identity is blank. | `v17/nhl_starter_goalie_confirmation.py` | — | No |
| `NHL_GOALIE_CONFIRMATION_PAYLOAD_HASH_INVALID` | Payload hash is not 64 lowercase hexadecimal characters. | `v17/nhl_starter_goalie_confirmation.py` | — | No |
| `NHL_GOALIE_CONFIRMATION_EVENT_MISMATCH` | Receipt event ID or kickoff clock differs from the canonical event. | `v17/nhl_starter_goalie_confirmation.py` | — | No |
| `NHL_GOALIE_CONFIRMATION_TEAM_MISMATCH` | Receipt home/away teams differ from the canonical event. | `v17/nhl_starter_goalie_confirmation.py` | — | No |
| `NHL_GOALIE_CONFIRMATION_TIME_MISSING` | Observation timestamp is absent or not timezone-aware. | `v17/nhl_starter_goalie_confirmation.py` | — | No |
| `NHL_GOALIE_CONFIRMATION_TIME_IN_FUTURE` | Observation timestamp is later than the run instant. | `v17/nhl_starter_goalie_confirmation.py` | — | No |
| `NHL_GOALIE_CONFIRMATION_AFTER_KICKOFF` | Observation timestamp is at or after the official start. | `v17/nhl_starter_goalie_confirmation.py` | — | No |
| `NHL_GOALIE_CONFIRMATION_STALE` | Observation is older than the 90-minute confirmation window. | `v17/nhl_starter_goalie_confirmation.py` | — | No |
| `NHL_STARTING_GOALIE_NOT_CONFIRMED` | Source status is not `CONFIRMED` (for example `PROJECTED`). | `v17/nhl_starter_goalie_confirmation.py` | — | No |
| `NHL_GOALIE_IDENTITY_MISSING` | Starting-goalie identity is missing on a side. | `v17/nhl_starter_goalie_confirmation.py` | — | No |
| `NHL_GOALIE_IDENTITY_INVALID` | Home and away starting-goalie identities are identical. | `v17/nhl_starter_goalie_confirmation.py` | — | No |
| `NHL_GOALIE_SOURCES_NOT_INDEPENDENT` | Fewer than two distinct providers or distinct source receipts. | `v17/nhl_starter_goalie_confirmation.py` | — | No |
| `NHL_STARTING_GOALIE_CONFLICT` | Independent receipts disagree on a starting-goalie identity. | `v17/nhl_starter_goalie_confirmation.py` | — | No |

### Pregame evidence gate (`v17/nhl_pregame_evidence_boundary.py`)

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---:|
| `NHL_TEAM_IDENTITY_UNVERIFIED` | `home_team`/`away_team` cannot be normalized to a recognized NHL abbreviation. Alias text is not canonical team identity. | `v17/nhl_pregame_evidence_boundary.py` | — | No |
| `NHL_DUPLICATE_OPPONENT_IDENTITY` | Normalized home and away team identities are the same team. | `v17/nhl_pregame_evidence_boundary.py` | — | No |
| `NHL_EVENT_IDENTITY_UNVERIFIED` | `official_event_id` is not a 10-character ASCII digit string. | `v17/nhl_pregame_evidence_boundary.py` | — | No |
| `NHL_SETTLEMENT_BASIS_UNVERIFIED` | `settlement_basis` is not `HOME_AWAY_INCLUDING_OT_SO`, so a 60-minute or partial-game market could be mixed with a full-game result. | `v17/nhl_pregame_evidence_boundary.py` | — | No |
| `NHL_GOALIE_STATUS_UNVERIFIED` | Evidence `goalie_status` is not `CONFIRMED`. This is the pre-existing evidence-side label check; it is necessary but never sufficient without the independent receipt below. | `v17/nhl_pregame_evidence_boundary.py` | — | No |
| `NHL_GOALIE_STATUS_STALE` | `goalie_status_observed_at` is missing, unparseable, in the future, or older than `max_status_age_minutes` (default 60). | `v17/nhl_pregame_evidence_boundary.py` | — | No |
| `NHL_NUMERIC_FEATURE_INVALID` | An Elo value is missing/non-finite, or a save/PP/PK rate is missing, boolean, non-finite or outside `0..1`. | `v17/nhl_pregame_evidence_boundary.py` | — | No |
| `NHL_SOURCE_SNAPSHOT_MISSING` | `source_snapshot_id` is absent or blank. A payload hash is not a persisted snapshot receipt. | `v17/nhl_pregame_evidence_boundary.py` | — | No |
| `NHL_GOALIE_CONFIRMATION_RECEIPT_MISSING` | The gate was called without an independent confirmation receipt; a caller `CONFIRMED` label is not proof. | `v17/nhl_pregame_evidence_boundary.py` | — | No |
| `NHL_GOALIE_CONFIRMATION_RECEIPT_NOT_CONFIRMED` | Receipt status/code/event/goalie-status are wrong, or the receipt already carries blockers. | `v17/nhl_pregame_evidence_boundary.py` | — | No |
| `NHL_GOALIE_CONFIRMATION_SOURCES_NOT_INDEPENDENT` | Receipt reports fewer than two independent sources or a non-integer source count. | `v17/nhl_pregame_evidence_boundary.py` | — | No |
| `NHL_GOALIE_IDENTITY_SNAPSHOT_MISMATCH` | Receipt snapshot identity differs from the evidence row's snapshot identity. | `v17/nhl_pregame_evidence_boundary.py` | — | No |
| `NHL_GOALIE_CONFIRMATION_TIME_MISMATCH` | Receipt and evidence observation instants differ. | `v17/nhl_pregame_evidence_boundary.py` | — | No |
| `NHL_GOALIE_IDENTITY_UNVERIFIED` | Goalie identities are missing, blank or identical; also the pre-existing required-input check. | `v17/nhl_pregame_evidence_boundary.py` | — | No |
| `NHL_GOALIE_CONFIRMATION_AUTHORITY_INVALID` | Receipt claims `probability_publishable`, `rank_eligible` or `can_execute`. | `v17/nhl_pregame_evidence_boundary.py` | — | No |

The eight identity, settlement, feature, goalie-status and snapshot rows above
predate the independent confirmation contract and remain emitted by the same
validator. The receipt adds independent proof; it does not replace these checks.

### Orchestration and receipt persistence (`v17/nhl_pregame_orchestration.py`)

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---:|
| `NHL_PREGAME_ADAPTER_PROVIDER_NOT_OFFICIAL` | Adapter registration used a non-official provider. Configuration error, never a sport result. | `v17/nhl_pregame_orchestration.py` | — | No |
| `NHL_PREGAME_ADAPTER_NOT_A_STARTER_SOURCE` | Adapter registration used a Gamecenter participation, projection or last-start-inference kind. | `v17/nhl_pregame_orchestration.py` | — | No |
| `NHL_PREGAME_ADAPTER_SOURCE_KIND_UNSUPPORTED` | Adapter registration used a kind outside the approved starter kinds. | `v17/nhl_pregame_orchestration.py` | — | No |
| `NHL_PREGAME_ADAPTER_COLLECT_NOT_CALLABLE` | Adapter registration supplied a non-callable collector. | `v17/nhl_pregame_orchestration.py` | — | No |
| `NHL_PREGAME_ORCHESTRATION_ADAPTERS_INSUFFICIENT` | Fewer than two adapters, or fewer than two distinct official providers. Nothing is fetched or persisted. | `v17/nhl_pregame_orchestration.py` | — | No |
| `NHL_PREGAME_STARTER_SOURCE_UNOBTAINABLE:<provider>:<code>` | An adapter raised during collection. The suffix names the provider and its typed adapter/transport code. Acquisition only. | `v17/nhl_pregame_orchestration.py` | — | No |
| `NHL_PREGAME_STARTER_SOURCE_INVALID:<provider>` | An adapter returned a non-mapping receipt. | `v17/nhl_pregame_orchestration.py` | — | No |
| `NHL_PREGAME_RECEIPT_PROVIDER_MISMATCH:<provider>` | A receipt claimed a different provider or source kind than its adapter. | `v17/nhl_pregame_orchestration.py` | — | No |
| `NHL_PREGAME_RECEIPT_PERSISTENCE_FAILED` | The immutable receipt ledger write failed, so the evidence gate was not invoked. | `v17/nhl_pregame_orchestration.py` | — | No |
| `NHL_PREGAME_RECEIPT_PERSISTENCE_ERROR:<TYPE>` | Suffix names the persistence exception class for the failed ledger write. | `v17/nhl_pregame_orchestration.py` | — | No |
| `NHL_PREGAME_GATE_NOT_PASSED` | Aggregate gate HOLD after persistence and confirmation; nested blockers identify the exact cause. | `v17/nhl_pregame_orchestration.py` | — | No |

### NHL candidate preflight and promotion-stage maintenance (`v17/nhl_candidate_preflight.py`, `v17/nhl_model_maintenance.py`)

This is the candidate-training lane, not the pregame evidence lane. Governed
maintenance runs in one fixed stage order:

```text
CONFIGURATION -> CANDIDATE_TRAINING -> NHL_ACQUISITION_OR_TRAINING
-> CANDIDATE_PREFLIGHT -> NHL_RECONCILIATION -> CANDIDATE_PERSISTENCE
```

A fully successful run terminates at `CANDIDATE_EVIDENCE_UPDATED`, which still
carries `automatic_certification=false`, `automatic_promotion=false`,
`probability_publishable=false` and `can_execute=false`. No code below can
certify, promote, activate, publish, rank or execute; a preflight pass means only
that evidence is fit to persist as a candidate. None of these is
`MODEL_UNAVAILABLE` or `MODEL_INPUTS_INSUFFICIENT`.

#### Candidate source acquisition and reconstruction (`nhl_candidate_pipeline.py`)

This upstream lane fetches official club-season schedules and rebuilds features
chronologically from prior settled games only; sportsbook prices never enter a
feature. Rows remain historical reconstructions, not archived pregame snapshots,
and the fitted output stays `lifecycle_state=CANDIDATE` with
`probability_publishable=false`, `promoted=false` and `can_execute=false`.
`CANDIDATE_BUILT` and the `NHL_REGULAR_SEASON_LOGISTIC_V1` /
`NHL_REGULAR_SEASON_FEATURES_V1` / `NHL_PRIOR_SETTLED_RECONSTRUCTION_V1` /
`NHL_RS_LOGISTIC_V1` labels are identifiers, not failure codes. This module also
raises `NHL_EVENT_TIME_INVALID`, registered once under the event-identity section.

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---:|
| `NHL_SOURCE_NOT_READY` | The model-source entitlement registry does not admit `NHL_PUBLIC_WEB_API` for candidate training; detail lists its blockers. Raised before any team request. | `nhl_candidate_pipeline.py` | — | No |
| `NHL_TEAM_UNSUPPORTED` | The requested team is not in the known NHL abbreviation universe, including historical ARI/UTA. | `nhl_candidate_pipeline.py` | — | No |
| `NHL_TEAM_NOT_ACTIVE_IN_SEASON` | The team is known but not active in the requested season (for example SEA before 2021, ARI after 2023, UTA before 2024); detail is `abbr:year`. | `nhl_candidate_pipeline.py` | — | No |
| `NHL_SOURCE_TRANSPORT_FAILED` | A club-season request raised; detail is the exception type. Acquisition failure only, never a model verdict. | `nhl_candidate_pipeline.py` | — | No |
| `NHL_SOURCE_RATE_LIMITED` | The club-season endpoint returned HTTP 429; detail is the team. | `nhl_candidate_pipeline.py` | — | No |
| `NHL_SOURCE_HTTP_<status>` | The club-season endpoint returned a non-200, non-429 status; the suffix is the status and detail is the team. | `nhl_candidate_pipeline.py` | — | No |
| `NHL_SOURCE_RESPONSE_INVALID` | The response body could not be decoded as JSON; detail is the exception type. | `nhl_candidate_pipeline.py` | — | No |
| `NHL_SOURCE_SCHEMA_DRIFT` | The decoded body is not a mapping or lacks a `games` list. | `nhl_candidate_pipeline.py` | — | No |
| `NHL_SOURCE_SEASON_IDENTITY_DRIFT` | A normalized game's season differs from the season requested for that team schedule; detail is the first mismatched game ID. | `nhl_candidate_pipeline.py` | — | No |
| `NHL_DUPLICATE_GAME_CONFLICT` | The same game ID observed through two team mirrors disagrees on season, start, teams or scores. Identical duplicates are merged without error. | `nhl_candidate_pipeline.py` | — | No |
| `NHL_RECENT_HISTORY_EMPTY` | Recent-form metrics were requested for a team with no prior games in its window. | `nhl_candidate_pipeline.py` | — | No |
| `NHL_REST_HISTORY_EMPTY` | Rest days were requested before the team had any prior game. | `nhl_candidate_pipeline.py` | — | No |
| `NHL_CHRONOLOGY_INVALID` | The computed rest interval is non-positive, so the prior start is not strictly earlier than the current fixture. | `nhl_candidate_pipeline.py` | — | No |
| `NHL_GAMES_NOT_CHRONOLOGICAL` | Reconstruction received games that were not sorted by start and game ID. Sorting is mandatory so no later result can enter an earlier feature row. | `nhl_candidate_pipeline.py` | — | No |
| `NHL_CANDIDATE_TRAINING_FAILED` | Wrapper for any non-`NHLCandidateError` raised by `train_binary_candidate`. An embedded typed code is preserved; otherwise this code names the untyped failure. | `nhl_candidate_pipeline.py` | — | No |
| `NHL_TRAINING_CODE_SHA_REQUIRED` | `training_code_sha` is shorter than 7 characters, so the artifact version could not be tied to deployed code. | `nhl_candidate_pipeline.py` | — | No |

#### Candidate package and dataset preflight (stage `CANDIDATE_PREFLIGHT`)

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---:|
| `NHL_CANDIDATE_PACKAGE_INCOMPLETE` | Package lacks a non-empty `games` list, a non-empty `feature_rows` list, or a `candidate` mapping. Nothing is validated further. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_CANDIDATE_IDENTITY_MISMATCH` | Candidate sport/league, model family, source policy or feature-schema version differs from the governed NHL family. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_CANDIDATE_EVENT_TIME_INVALID` | `event_start_time` or `feature_as_of` is missing, unparseable or timezone-naive. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_CANDIDATE_FEATURE_SCHEMA_MISMATCH` | Artifact `feature_names` or a training row's feature key set differs from the governed `FEATURE_NAMES`. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_CANDIDATE_FEATURE_INVALID` | A training feature is boolean, non-numeric or non-finite. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_CANDIDATE_OUTCOME_MISSING` | `positive_outcome` is not a bool. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_CANDIDATE_SOURCE_MANIFEST_INVALID` | Row manifest is not a mapping, its SHA is invalid or mismatched, source/schema/reconstruction identity is wrong, an archived pregame snapshot is claimed, or market features / `can_execute` are not false. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_CANDIDATE_GOVERNANCE_FLAGS_INVALID` | Candidate or package is not research-only: lifecycle is not `CANDIDATE`, or promoted/active/automatic-certification/automatic-promotion/publishable/execute flags are not false. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_CANDIDATE_ARTIFACT_CHECKSUM_MISMATCH` | `artifact_checksum` is not 64-hex or does not equal the canonical hash of the artifact payload. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_CANDIDATE_PARTITIONS_INVALID` | `validation_metrics` is not a mapping. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_CANDIDATE_MARKET_FEATURES_FORBIDDEN` | Validation metrics admit market-derived inputs as model features. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_CANDIDATE_PARTITION_MISMATCH` | Stored or fitted train/calibration/test counts are not positive integers, disagree with each other, or do not sum to the feature-row count. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_CANDIDATE_ARTIFACT_IDENTITY_MISMATCH` | Embedded artifact model family differs from the governed family. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_CANDIDATE_ARTIFACT_SPLIT_MISMATCH` | Embedded split policy is not `CHRONOLOGICAL_60_20_20`, or its boundary indices differ from the fitted cumulative counts. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_CANDIDATE_SOURCE_EVENT_INVALID` | A source game entry is not a mapping. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_CANDIDATE_SOURCE_PIN_MISSING` | A source game fails provider, payload-hash, official URI-prefix, retrieved-time, historical-reconstruction or `can_execute` pinning. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_CANDIDATE_DUPLICATE_IDENTITY` | Empty or duplicate event ID, a repeated (season, start, home, away) identity, or a feature row with no matching source game. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_CANDIDATE_FEATURE_ROW_INVALID` | A feature row is not a mapping. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_CANDIDATE_SOURCE_IDENTITY_MISMATCH` | Feature-row flags, start/outcome, or manifest game hash disagree with its source game. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_CANDIDATE_TEMPORAL_LEAKAGE` | `feature_as_of` is at or after the event start, so pregame features would contain outcome-adjacent information. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_CANDIDATE_ROWS_NOT_CHRONOLOGICAL` | Feature rows are not in non-decreasing event-start order. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_CANDIDATE_DATASET_HASH_MISMATCH` | `training_dataset_hash` is invalid or differs from the recomputed canonical dataset hash. | `v17/nhl_candidate_preflight.py` | — | No |

#### Frozen → current goalie-status handoff (`validate_nhl_goalie_status_handoff`)

This comparison is evidence reconciliation only. It does not qualify a scorer,
invoke one, or certify a probability.

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---:|
| `NHL_GOALIE_EVENT_IDENTITY_MISMATCH` | Frozen and current snapshots do not both carry the same non-empty official event ID. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_GOALIE_KICKOFF_INVALID` | Either snapshot's kickoff is missing, unparseable or timezone-naive. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_GOALIE_AS_OF_INVALID` | Either snapshot's `as_of` is missing, unparseable or timezone-naive. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_GOALIE_KICKOFF_MISMATCH` | Frozen and current kickoffs differ. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_GOALIE_EVIDENCE_AFTER_KICKOFF` | Either snapshot was observed at or after its kickoff. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_GOALIE_CURRENT_RECEIPT_STALE` | The current observation does not postdate the frozen one, or kickoff is more than 4 hours after the current observation. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_GOALIE_SOURCE_PIN_MISSING` | Either snapshot lacks the governed source ID, a snapshot ID, or a 64-hex payload hash. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_GOALIE_SOURCE_NOT_REFRESHED` | Snapshot ID and payload hash are both unchanged; a newer timestamp alone is not a refreshed immutable receipt. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_GOALIE_MISSING` | A side lacks a goalie mapping or goalie ID in either snapshot. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_STARTING_GOALIE_CHANGED` | The starting-goalie ID changed between the frozen and current snapshots. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_GOALIE_STATUS_MISSING` | Starting status is blank on a side in either snapshot. | `v17/nhl_candidate_preflight.py` | — | No |
| `NHL_STARTING_STATUS_CHANGED` | Starting status differs between the snapshots even though both are confirmed statuses. | `v17/nhl_candidate_preflight.py` | — | No |

This handoff also emits `NHL_STARTING_GOALIE_NOT_CONFIRMED` and
`NHL_GOALIE_IDENTITY_INVALID`, registered once under the confirmation table above.

#### Promotion stages and blocked dispositions (`v17/nhl_model_maintenance.py`)

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---:|
| `NHL_MAINTENANCE_RANGE_EMPTY` | Stage `CONFIGURATION`: no start years remain after normalization. Nothing is acquired or trained. | `v17/nhl_model_maintenance.py` | — | No |
| `NHL_TRAINING_CODE_SHA_UNAVAILABLE` | Stage `CANDIDATE_TRAINING`: no training-code SHA was supplied and `RENDER_GIT_COMMIT` is unset, so evidence could not be tied to deployed code. | `v17/nhl_model_maintenance.py` | — | No |
| `NHL_CANDIDATE_PREFLIGHT_BLOCKED` | Stage `CANDIDATE_PREFLIGHT`: wrapper emitted only after candidate preflight rejects the package; nested `detail.code` names the specific preflight code above. | `v17/nhl_model_maintenance.py` | — | No |
| `NHL_MODEL_MAINTENANCE_FAILED` | Stage `NHL_ACQUISITION_OR_TRAINING`: an untyped exception escaped acquisition/training; only the exception type is recorded. | `v17/nhl_model_maintenance.py` | — | No |
| `NHL_TRAINING_OUTCOME_NOT_RECONCILED` | Stage `NHL_RECONCILIATION`: a feature-row event ID has no settled source-game outcome. | `v17/nhl_model_maintenance.py` | — | No |
| `NHL_CANDIDATE_PERSISTENCE_FAILED` | Stage `CANDIDATE_PERSISTENCE`: an untyped failure occurred while writing the immutable candidate package; only the exception type is recorded. | `v17/nhl_model_maintenance.py` | — | No |

Success status `NHL_PREGAME_GATE_INPUTS_COMPLETE` is not a failure code and grants
no probability, rank or execution authority.

## NHL official event identity and source resolution (November 2026)

First-party resolver (`v17/nhl_event_identity.py`). Provider discovery IDs (for
example ESPN IDs) remain aliases; canonical event identity is assigned only
after an exact participant plus start-time match against the NHL-owned club
season schedule endpoint, within a 30-minute tolerance. The resolver returns
`identity_provider=NHL_PUBLIC_WEB_API`, `market_features_used=false`,
`prediction_authority=false` and `can_execute=false`; it grants no probability,
ranking, certification or execution authority. None of these codes is
`MODEL_UNAVAILABLE`: they describe identity or acquisition state, not the
absence of a fitted model.

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---:|
| `NHL_EVENT_TIME_INVALID` | Supplied event start is missing, not parseable as ISO-8601, or timezone-naive. Raised before any source fetch, so no provider aliasing occurs. | `v17/nhl_event_identity.py` | `nhl_candidate_pipeline.py` | No |
| `NHL_EVENT_PARTICIPANT_IDENTITY_UNRESOLVED` | Home or away cannot be normalized to a recognized NHL abbreviation, or both normalize to the same team. Raised before any source fetch. | `v17/nhl_event_identity.py` | — | No |
| `NHL_OFFICIAL_SOURCE_TRANSPORT_FAILED` | The club-season schedule request raised (timeout, DNS, connection, TLS). Acquisition failure only; carries the exception type, never a model verdict. | `v17/nhl_event_identity.py` | — | No |
| `NHL_OFFICIAL_SOURCE_RATE_LIMITED` | The official schedule endpoint returned HTTP 429. Acquisition failure only. | `v17/nhl_event_identity.py` | — | No |
| `NHL_OFFICIAL_SOURCE_HTTP_<status>` | The official schedule endpoint returned any other non-200 status. The suffix is the provider status code, or `UNKNOWN` when absent. | `v17/nhl_event_identity.py` | — | No |
| `NHL_OFFICIAL_SOURCE_RESPONSE_INVALID` | The response body could not be decoded as JSON. The detail records the exception type only. | `v17/nhl_event_identity.py` | — | No |
| `NHL_OFFICIAL_SOURCE_SCHEMA_DRIFT` | Decoded payload is not a mapping, or its `games` member is not a list. | `v17/nhl_event_identity.py` | — | No |
| `NHL_CANONICAL_EVENT_NOT_FOUND` | No scheduled game matched both normalized participants and a start within the 30-minute tolerance, so no canonical official event ID exists. | `v17/nhl_event_identity.py` | — | No |
| `NHL_CANONICAL_EVENT_AMBIGUOUS` | More than one distinct official event ID matched the same participants and start window. No arbitrary winner is chosen. | `v17/nhl_event_identity.py` | — | No |

## NHL total-goals research lane (November 2026)

Research challengers only (`v17/nhl_total_goals_*`). Every artifact and receipt in
this lane sets `probability_publishable=false`, `rank_eligible=false`,
`can_execute=false`; the regime-mixture module additionally sets
`automatic_certification=false` and `automatic_promotion=false`. Features are
retrospectively reconstructed from prior settled games only and are explicitly
**not** archived pregame snapshots. A passing disposition means the research
evidence reconciled; it never certifies a probability or registers a serving
specialist. None of these codes is `MODEL_UNAVAILABLE`. Information-bearing
dispositions are not failure codes: `NHL_TOTALS_OFFICIAL_HISTORICAL_PROJECTION_VERIFIED`,
`NHL_TOTALS_REAL_SOURCE_RESEARCH_FEATURES_READY`,
`NHL_TOTALS_REGIME_FIT_INPUTS_RECONCILED_RESEARCH_ONLY`,
`NHL_TOTALS_RETROSPECTIVE_SOURCE_LABELS_RECONCILED`,
`NHL_TOTALS_OFFICIAL_REGIME_COHORT_RECONCILED`,
`NHL_TOTALS_OFFICIAL_REGIME_PARTIAL_RESEARCH_ONLY`,
`NHL_TOTALS_PARTIAL_RESEARCH_NO_FIT`,
`NHL_TOTALS_REAL_5SEASON_REGIME_REPLAY_RESEARCH_ONLY` and
`NHL_TOTALS_RESEARCH_MODEL_REPLAY_COMPLETED_NOT_CERTIFIED`. Their stage meanings
are recorded in `docs/nhl_status_registry.md`.

### Source admission and cohort reconstruction

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---:|
| `NHL_TOTALS_SOURCE_ROWS_EMPTY` | No D1 or official source records were supplied, so no research row can be reconstructed. | `v17/nhl_total_goals_source_adapter.py` | `v17/nhl_total_goals_challenger.py` | No |
| `NHL_TOTALS_SOURCE_ROW_INVALID` | A source row fails sport/source, historical-reconstruction, `can_execute`, season identity, event/team identity, 64-hex hash, regular-season (`gameType` 2) or final-score (non-negative, non-tie) admission. Detail is the underlying validation text. | `v17/nhl_total_goals_challenger.py` | — | No |
| `NHL_TOTALS_EVENT_TIME_INVALID` | A source timestamp is missing, unparseable, or timezone-naive. | `v17/nhl_total_goals_challenger.py` | — | No |
| `NHL_TOTALS_SOURCE_IDENTITY_CONFLICT` | The same season/event appears with different start, teams or scores; no source version is chosen implicitly. | `v17/nhl_total_goals_source_adapter.py` | `v17/nhl_total_goals_challenger.py` | No |
| `NHL_TOTALS_DUPLICATE_EVENT` | The same event identity appears twice with identical content. | `v17/nhl_total_goals_challenger.py` | — | No |
| `NHL_TOTALS_OVERLAPPING_TEAM_EVENT` | Within one scheduled instant a team appears in more than one fixture, so its prior-game history would be ambiguous. | `v17/nhl_total_goals_challenger.py` | — | No |
| `NHL_TOTALS_CHRONOLOGY_INVALID` | A history entry used to build features is timestamped at or after the fixture being featurized. | `v17/nhl_total_goals_challenger.py` | — | No |
| `NHL_TOTALS_FEATURE_INVALID` | A reconstructed prior-10 feature is non-finite or negative. | `v17/nhl_total_goals_challenger.py` | — | No |
| `NHL_TOTALS_PERSISTED_ROW_INVALID` | A persisted D1 row fails season/event/score/team/source/governance/hash admission; detail is the validation text. | `v17/nhl_total_goals_source_adapter.py` | — | No |
| `NHL_TOTALS_SOURCE_VERSION_REVIEW_REQUIRED` | One event has multiple source payload versions or instances. The explicit research-only override `allow_matching_source_versions=True` is required after review, and the audit ledger retains every hash. | `v17/nhl_total_goals_source_adapter.py` | — | No |
| `NHL_TOTALS_SCHEDULE_ROW_INVALID` | An official schedule game row is not a mapping, or its event ID, season, start, teams, scores or period fail admission, including OT/SO one-goal-margin and odd-parity rules. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_SCHEDULE_SCHEMA_INVALID` | A club schedule payload is not a mapping or lacks a `games` list. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_SCHEDULE_TEAM_INVALID` | The requested schedule team is not a 3-letter abbreviation. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_SCHEDULE_IDENTITY_DRIFT` | A schedule row's season differs from the requested season, or the requested team is not a participant. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_SCHEDULE_DUPLICATE_EVENT` | One club schedule contains the same event ID twice. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_REGIME_SOURCE_TYPE_INVALID` | Reconciliation received a row that is not an `NHLOfficialRegimeResult`. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_REGIME_GOVERNANCE_INVALID` | A regime row admits execution/publication or is not marked historically reconstructed. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_REGIME_SOURCE_EMPTY` | No completed regular-season games remained after normalization. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_REGIME_MIRROR_CONFLICT` | Two team-mirror views of the same event disagree on start, teams, score, period or goal-count definition. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_REGIME_D1_COHORT_EMPTY` | Either the D1 event cohort or the official regime cohort is empty; a one-sided join is never attempted. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_REGIME_D1_TYPE_INVALID` | A regime-side join input is not an `NHLOfficialRegimeResult`. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_REGIME_D1_DUPLICATE` | A season/event key is duplicated in either cohort. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_REGIME_D1_COHORT_MISSING` | A D1 event has no official regime label, or an official label was never matched; detail counts the unmatched side and no partial join is accepted. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_REGIME_D1_IDENTITY_CONFLICT` | D1 start, teams or scores disagree with the official settled result. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_REGIME_D1_ROW_INVALID` | A D1 row lacks required fields or contains invalid ones during the retrospective join. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_COHORT_SEASONS_INVALID` | No seasons, non-integer seasons, or seasons outside the fully historical 2021–2025 range were requested. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_COHORT_SOURCE_NOT_READY` | The source entitlement registry does not admit `NHL_PUBLIC_WEB_API` for candidate training; detail lists its blockers. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_COHORT_TEAMS_INVALID` | The requested team set is empty, duplicated, or contains a team outside the official season team list. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_COHORT_TRANSPORT_FAILED` | A club-schedule request raised; detail records the team and exception type. Acquisition failure only. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_COHORT_HTTP_<status>` | A club-schedule request returned non-200; the suffix is the HTTP status and detail is the team. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_COHORT_RESPONSE_INVALID` | A club-schedule response could not be decoded as JSON; detail is the team. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_COHORT_COVERAGE_INCOMPLETE` | A supposedly complete season did not reconcile to 1,312 unique games, 2,624 mirror instances, and exactly two instances per event. | `v17/nhl_total_goals_regime_source.py` | — | No |
| `NHL_TOTALS_OFFICIAL_SEASON_INVALID` | The fixture year is not an integer in the approved 2021–2025 set. | `v17/nhl_total_goals_official_fixture.py` | — | No |
| `NHL_TOTALS_OFFICIAL_ARCHIVE_INVALID` | Base64, gzip or JSON decoding failed, or the compressed (>100 KB) or expanded (>2 MB) bound was exceeded. | `v17/nhl_total_goals_official_fixture.py` | — | No |
| `NHL_TOTALS_OFFICIAL_PROVENANCE_INVALID` | Fixture season, URL template, historical-reconstruction flags, projection-not-raw-payload marker, or mirror-instance count is wrong. | `v17/nhl_total_goals_official_fixture.py` | — | No |
| `NHL_TOTALS_OFFICIAL_COVERAGE_INCOMPLETE` | A season does not contain exactly 1,312 admitted games. | `v17/nhl_total_goals_official_fixture.py` | — | No |
| `NHL_TOTALS_OFFICIAL_GAME_PROJECTION_INVALID` | A projected game fails event-ID/season prefix, final-state, team, score, two-mirror or final-period admission. | `v17/nhl_total_goals_official_fixture.py` | — | No |
| `NHL_TOTALS_OFFICIAL_TEAM_COVERAGE_INVALID` | The season does not cover all 32 teams at 82 games each. | `v17/nhl_total_goals_official_fixture.py` | — | No |
| `NHL_TOTALS_OFFICIAL_DUPLICATE_EVENT` | A game ID repeats within a season, or the same event appears across seasons during reconstruction. | `v17/nhl_total_goals_official_fixture.py` | — | No |
| `NHL_TOTALS_OFFICIAL_SOURCE_FINGERPRINT_MISMATCH` | The recomputed identity or REG/OT/SO label digest differs from the pinned per-season checksum. | `v17/nhl_total_goals_official_fixture.py` | — | No |
| `NHL_TOTALS_OFFICIAL_SEASON_SET_INVALID` | Reconstruction received no seasons or duplicate years. | `v17/nhl_total_goals_official_fixture.py` | — | No |
| `NHL_TOTALS_OFFICIAL_ROW_UNVERIFIED` | A reconstruction input is not an `OfficialProjection` or carries the wrong season tag. | `v17/nhl_total_goals_official_fixture.py` | — | No |
| `NHL_TOTALS_OFFICIAL_MODEL_JOIN_MISMATCH` | A reconstructed row's total, source hash or start disagrees with its admitted official projection. | `v17/nhl_total_goals_official_fixture.py` | — | No |
| `NHL_TOTALS_OFFICIAL_FIXTURE_MISSING` | The checked-in compressed fixture for a requested season is absent; there is no network fallback. | `v17/nhl_total_goals_official_fixture.py` | — | No |
| `NHL_TOTALS_LANDING_SOURCE_INVALID` | The gamecenter landing payload is not a mapping. | `v17/nhl_total_goals_score_regimes.py` | — | No |
| `NHL_TOTALS_FINAL_SCORE_REGIME_MISMATCH` | Landing reconciliation failed: identity, final state, period order/completeness, per-goal side/team, shootout award, or official-versus-reconstructed total. Detail names the specific internal check. | `v17/nhl_total_goals_score_regimes.py` | — | No |

### Feature reconstruction and regime-label bridge

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---:|
| `NHL_TOTALS_BRIDGE_TIME_INVALID` | Bridge timestamp is missing, unparseable or timezone-naive. | `v17/nhl_total_goals_model_bridge.py` | — | No |
| `NHL_TOTALS_BRIDGE_SOURCE_HASH_INVALID` | A D1 or official digest is not 64 lowercase hexadecimal characters. | `v17/nhl_total_goals_model_bridge.py` | — | No |
| `NHL_TOTALS_BRIDGE_RECEIPT_INVALID` | The joined-label receipt status, reconstruction flags, publish/execute flags, `model_probability` or `calibrated_lower_bound` are unsafe for a research join. | `v17/nhl_total_goals_model_bridge.py` | — | No |
| `NHL_TOTALS_BRIDGE_LABELS_INVALID` | Labels are not a non-empty list, `count` is not an int equal to the list length, or a label is not a mapping. | `v17/nhl_total_goals_model_bridge.py` | — | No |
| `NHL_TOTALS_BRIDGE_LABEL_DUPLICATE` | A label event ID is not 10 decimal digits or is duplicated. | `v17/nhl_total_goals_model_bridge.py` | — | No |
| `NHL_TOTALS_BRIDGE_LABEL_UNVERIFIED` | A label is not historical, claims an archived pregame snapshot, or admits publication/execution. | `v17/nhl_total_goals_model_bridge.py` | — | No |
| `NHL_TOTALS_BRIDGE_FEATURES_EMPTY` | No reconstructed total rows were supplied. | `v17/nhl_total_goals_model_bridge.py` | — | No |
| `NHL_TOTALS_BRIDGE_FEATURE_UNVERIFIED` | A feature row is not a `TotalRow` or is not marked historically reconstructed. | `v17/nhl_total_goals_model_bridge.py` | — | No |
| `NHL_TOTALS_BRIDGE_DUPLICATE_FEATURE` | Duplicate feature event ID. | `v17/nhl_total_goals_model_bridge.py` | — | No |
| `NHL_TOTALS_BRIDGE_MISSING_REGIME` | No official regime label exists for a feature event. | `v17/nhl_total_goals_model_bridge.py` | — | No |
| `NHL_TOTALS_BRIDGE_IDENTITY_CONFLICT` | Season ID, final total, D1 payload hash or kickoff disagrees between the feature row and its label. | `v17/nhl_total_goals_model_bridge.py` | — | No |
| `NHL_TOTALS_BRIDGE_LEAKAGE` | `reconstructed_as_of` is at or after kickoff. | `v17/nhl_total_goals_model_bridge.py` | — | No |
| `NHL_TOTALS_BRIDGE_FEATURE_WIDTH` | The feature tuple is not the governed width of 6. | `v17/nhl_total_goals_model_bridge.py` | — | No |
| `NHL_TOTALS_BRIDGE_REGIME_CONFLICT` | Terminal regime is not REG/OT/SO, or a non-REG total is even. | `v17/nhl_total_goals_model_bridge.py` | — | No |
| `NHL_TOTALS_BRIDGE_ROW_INVALID` | A required label field is missing or mistyped while constructing the research row. | `v17/nhl_total_goals_model_bridge.py` | — | No |
| `NHL_REGIME_TIME_INVALID` | Mixture timestamp is missing, unparseable or timezone-naive. | `v17/nhl_total_goals_regime_mixture_research.py` | — | No |
| `NHL_REGIME_ROWS_EMPTY` | No regime research rows were supplied. | `v17/nhl_total_goals_regime_mixture_research.py` | — | No |
| `NHL_REGIME_ROW_TYPE_INVALID` | Row is not a `RegimeResearchRow`. | `v17/nhl_total_goals_regime_mixture_research.py` | — | No |
| `NHL_REGIME_DUPLICATE_EVENT` | Duplicate event ID in the regime cohort. | `v17/nhl_total_goals_regime_mixture_research.py` | — | No |
| `NHL_REGIME_FEATURE_CHRONOLOGY_INVALID` | `feature_as_of` is at or after kickoff, the row is not historical, or it claims an archived pregame snapshot. | `v17/nhl_total_goals_regime_mixture_research.py` | — | No |
| `NHL_REGIME_OUTCOME_INVALID` | Regime is not REG/OT/SO, the total is not a positive int, or a non-REG total is even. | `v17/nhl_total_goals_regime_mixture_research.py` | — | No |
| `NHL_REGIME_FEATURES_INVALID` | Feature count differs from 6 or a value is non-finite. | `v17/nhl_total_goals_regime_mixture_research.py` | — | No |
| `NHL_REGIME_SOURCE_HASH_INVALID` | A D1 or regime digest is not 64 hexadecimal characters. | `v17/nhl_total_goals_regime_mixture_research.py` | — | No |
| `NHL_REGIME_SEASON_UNSUPPORTED` | Season is outside the 2021–2025 research cohorts. | `v17/nhl_total_goals_regime_mixture_research.py` | — | No |

### Fitting and chronological splits

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---:|
| `NHL_TOTALS_NONFINITE_DATA` | The feature or outcome matrix contains non-finite values before fitting. | `v17/nhl_total_goals_challenger.py` | — | No |
| `NHL_TOTALS_HOLDOUT_INSUFFICIENT` | Train, calibration or validation rows are below the 300/120/120 minimums. | `v17/nhl_total_goals_challenger.py` | — | No |
| `NHL_TOTALS_SPLIT_OVERLAP` | An event appears in more than one cohort (train, calibration, validation or forward reserve). | `v17/nhl_total_goals_challenger.py` | — | No |
| `NHL_REGIME_HOLDOUT_INSUFFICIENT` | Train, calibration or development rows are below the 300/120/120 minimums. | `v17/nhl_total_goals_regime_mixture_research.py` | — | No |
| `NHL_REGIME_CLASS_HISTORY_INSUFFICIENT` | A terminal regime has fewer than 30 training or 15 calibration rows. | `v17/nhl_total_goals_regime_mixture_research.py` | — | No |
| `NHL_REGIME_CLASSES_MISSING` | Fitted logistic classes are not exactly REG/OT/SO. | `v17/nhl_total_goals_regime_mixture_research.py` | — | No |

### Calibration

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---:|
| `NHL_TOTALS_CALIBRATION_INVALID` | The empirical calibration mean multiplier is non-finite, non-positive, or outside 0.25–4. | `v17/nhl_total_goals_challenger.py` | — | No |
| `NHL_TOTALS_PARITY_CALIBRATION_INSUFFICIENT` | The parity calibration cohort is below 120 rows or contains duplicate events. | `v17/nhl_total_goals_parity_challenger.py` | — | No |
| `NHL_TOTALS_PARITY_CALIBRATION_DEGENERATE` | The odd-total share is not strictly between 0 and 1. | `v17/nhl_total_goals_parity_challenger.py` | — | No |
| `NHL_REGIME_CLASS_CALIBRATION_INVALID` | A class-weight factor is non-finite or outside 0.20–5. | `v17/nhl_total_goals_regime_mixture_research.py` | — | No |
| `NHL_REGIME_COUNT_CALIBRATION_INVALID` | A per-class count mean factor is non-finite or outside 0.20–5. | `v17/nhl_total_goals_regime_mixture_research.py` | — | No |

### Validation and distribution integrity

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---:|
| `NHL_TOTALS_MEAN_INVALID` | Fitted logits are non-finite or beyond ±80, or a supplied mean is non-finite or non-positive. | `v17/nhl_total_goals_challenger.py` | — | No |
| `NHL_TOTALS_VALIDATION_INVALID` | Validation length mismatch, non-finite predictions, or non-positive predicted means. | `v17/nhl_total_goals_challenger.py` | — | No |
| `NHL_TOTALS_DIRECTION_INVALID` | Side is not UNDER/OVER. | `v17/nhl_total_goals_challenger.py` | — | No |
| `NHL_TOTALS_LINE_UNSUPPORTED` | Line is not finite, is negative, or is not a half-integer. | `v17/nhl_total_goals_challenger.py` | — | No |
| `NHL_TOTALS_PROBABILITY_INVALID` | Win plus push plus loss mass does not equal 1 within 1e-8. | `v17/nhl_total_goals_challenger.py` | — | No |
| `NHL_TOTALS_PARITY_PARAMETERS_INVALID` | Mean is outside 0.1–30, or the odd share is non-finite or outside (0,1). | `v17/nhl_total_goals_parity_challenger.py` | — | No |
| `NHL_TOTALS_PARITY_LINE_UNSUPPORTED` | Parity line is not finite, is outside 0–20.5, or is not a half-integer. | `v17/nhl_total_goals_parity_challenger.py` | — | No |
| `NHL_TOTALS_PARITY_DIRECTION_UNRESOLVED` | Parity side is not UNDER/OVER. | `v17/nhl_total_goals_parity_challenger.py` | — | No |
| `NHL_TOTALS_PARITY_MASS_INVALID` | Parity under/push/over mass is out of range or inconsistent. | `v17/nhl_total_goals_parity_challenger.py` | — | No |
| `NHL_REGIME_SCORING_FEATURES_INVALID` | The scoring feature vector has the wrong width or a non-finite value. | `v17/nhl_total_goals_regime_mixture_research.py` | — | No |
| `NHL_REGIME_DISTRIBUTION_INVALID` | Component probabilities or means are non-finite, a mean is <= 0, or a mean exceeds 40. | `v17/nhl_total_goals_regime_mixture_research.py` | — | No |
| `NHL_REGIME_GOAL_COUNT_INVALID` | The requested goal count is not a non-negative integer. | `v17/nhl_total_goals_regime_mixture_research.py` | — | No |
| `NHL_REGIME_LINE_UNSUPPORTED` | Regime line is not finite, is outside 0–30, or is not a half-integer. | `v17/nhl_total_goals_regime_mixture_research.py` | — | No |
| `NHL_REGIME_DIRECTION_INVALID` | Regime side is not UNDER/OVER. | `v17/nhl_total_goals_regime_mixture_research.py` | — | No |
| `NHL_REGIME_MASS_INVALID` | The exact CDF/complement mass fails normalization. | `v17/nhl_total_goals_regime_mixture_research.py` | — | No |
| `NHL_REGIME_DEV_DENSITY_INVALID` | The development likelihood of an observed total is non-positive. | `v17/nhl_total_goals_regime_mixture_research.py` | — | No |

### Replay and corpus reproduction

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---:|
| `NHL_TOTALS_HISTORICAL_DUPLICATE_EVENT` | The five-season replay encountered the same event ID in more than one season fixture. | `v17/nhl_total_goals_official_fitted_replay.py` | — | No |
| `NHL_TOTALS_EXPERIMENT_COMPARISON_COHORT_MISMATCH` | Mixture and Poisson models were not fitted on identical train/calibration/development counts, or the admitted corpus is not 6,560 games. | `v17/nhl_total_goals_official_fitted_replay.py` | — | No |
| `NHL_TOTALS_REPLAY_SEASON_COHORT_INCOMPLETE` | A complete-corpus replay is missing one of the five required historical seasons. | `v17/nhl_total_goals_offline_replay.py` | — | No |
| `NHL_TOTALS_REPLAY_SCHEDULE_INPUT_INVALID` | A schedule export record is not a mapping or lacks season, team or payload. | `v17/nhl_total_goals_offline_replay.py` | — | No |
| `NHL_TOTALS_REPLAY_SCHEDULE_MISSING` | No official schedule exports were supplied. | `v17/nhl_total_goals_offline_replay.py` | — | No |
| `NHL_TOTALS_REPLAY_COVERAGE_INCOMPLETE` | Full-corpus invariants failed: not 6,560 games on each side, 13,120 mirror instances, 6,560 mirrored events, or a ledger event without exactly two instances. | `v17/nhl_total_goals_offline_replay.py` | — | No |

## NHL goalie/shot/special-teams research challenger (November 2026)

Research-only side-by-side comparison (`v17/nhl_goalie_shot_challenger.py`,
`v17/nhl_goalie_shot_runner.py`). V1 and V2 are refit on the identical
boxscore-complete intersection with the same chronological 60/20/20 partitions;
the added features are prior-settled-game reconstructions with a projected
goalie policy (`LAST_START_OR_B2B_ALTERNATE_V1`, `goalie_status=PROJECTED`), so
no pregame starter confirmation is claimed. The current game's settled boxscore
is excluded from every feature-row manifest. The report is
`ADVISORY_RESEARCH_ONLY` with `automatic_promotion_allowed=false`,
`probability_publishable=false` and `can_execute=false`; even a passing research
gate only requires forward shadow and governed review. The CI runner holds no
secrets and writes no database rows. None of these codes is `MODEL_UNAVAILABLE`.

### Boxscore acquisition and paired-row construction

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---:|
| `NHL_BOX_SCHEMA_DRIFT` | Official boxscore structure or numeric data is unusable: a count is boolean, negative or non-integer, a team/player array or goalie list is missing, saves exceed shots against, a required field is absent, or the decoded payload is not a mapping. Detail names the field or exception type; no default is substituted. | `v17/nhl_goalie_shot_challenger.py` | — | No |
| `NHL_BOX_IDENTITY_MISMATCH` | Boxscore ID/season, a team abbreviation, or the parsed box season/home/away disagrees with the scheduled game. Detail appends the side when only one team conflicts. | `v17/nhl_goalie_shot_challenger.py` | — | No |
| `NHL_BOX_NOT_FINAL` | `gameState` is not `FINAL`/`OFF`, or `gameType` is not 2 (regular season). A non-final boxscore is never admitted as a settled outcome. | `v17/nhl_goalie_shot_challenger.py` | — | No |
| `NHL_BOX_STARTER_AMBIGUOUS` | A side's goalie list has zero or more than one entry with `starter: true`, so the starting goalie cannot be identified without guessing. The CI runner may exclude this event as a documented coverage gap. | `v17/nhl_goalie_shot_challenger.py` | — | No |
| `NHL_BOX_TRANSPORT_FAILED` | A boxscore request raised `requests.RequestException` on the final of four attempts (backoff 1s, 2s, 4s, capped at 8s). | `v17/nhl_goalie_shot_challenger.py` | — | No |
| `NHL_BOX_HTTP_<status>` | Non-200 response: immediately for non-retryable statuses such as 404, or after the final attempt for 429/500/502/503/504. The suffix is the HTTP status and detail is the game ID. | `v17/nhl_goalie_shot_challenger.py` | — | No |
| `NHL_BOX_RETRIES_EXHAUSTED` | The retry loop ended without a classified response or exception, which happens when the retry budget is non-positive. Defensive terminal; it reports no provider result. | `v17/nhl_goalie_shot_challenger.py` | — | No |
| `NHL_BOX_JSON_INVALID` | The boxscore response body could not be decoded as JSON. | `v17/nhl_goalie_shot_challenger.py` | — | No |
| `NHL_BOX_GAME_RECONCILIATION_FAILED` | Schedule game IDs are duplicated, or a supplied boxscore key is not in the schedule universe. No box is silently dropped or invented. | `v17/nhl_goalie_shot_challenger.py` | — | No |
| `NHL_GAME_ID_DUPLICATE` | The fetched historical schedule contains a duplicate official game ID, in either replay entry point. | `v17/nhl_goalie_shot_challenger.py` | `v17/nhl_goalie_shot_runner.py` | No |
| `NHL_V1_IDENTITY_DUPLICATE` | The reconstructed V1 baseline rows contain a duplicate event ID, so the side-by-side comparison would not hold one row per game. | `v17/nhl_goalie_shot_challenger.py` | — | No |
| `NHL_GAME_TIME_CONFLICT` | A team appears twice at the same scheduled instant, or its prior history entry is timestamped at or after the current fixture. | `v17/nhl_goalie_shot_challenger.py` | — | No |
| `NHL_COMPARISON_INTERSECTION_INCOMPLETE` | Fewer than 300 events remain in the boxscore-complete V1/V2 intersection, so the paired comparison is not fitted. | `v17/nhl_goalie_shot_challenger.py` | — | No |

### Cached replay driver

The runner excludes an event only for the three documented coverage failures
(`NHL_BOX_HTTP_404`, `NHL_BOX_STARTER_AMBIGUOUS`, `NHL_BOX_SCHEMA_DRIFT`).
Transport, rate-limit and identity failures remain blocking.

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---:|
| `NHL_CACHE_IDENTITY_CONFLICT` | A cached box's event ID, season, home or away team differs from the schedule row. | `v17/nhl_goalie_shot_runner.py` | — | No |
| `NHL_CACHE_HASH_INVALID` | A cached box's `source_hash` is not 64 characters. | `v17/nhl_goalie_shot_runner.py` | — | No |
| `NHL_CACHE_INVALID` | A cached file is unreadable, mistyped, missing keys, or failed its identity/hash check. The run fails closed instead of trusting the row or silently refetching it. | `v17/nhl_goalie_shot_runner.py` | — | No |
| `NHL_BOX_COVERAGE_BELOW_98_PERCENT` | More than 2% of scheduled games were excluded for documented coverage reasons, so the research corpus is too incomplete to replay. | `v17/nhl_goalie_shot_runner.py` | — | No |
| `NHL_RESEARCH_REPLAY_FAILED` | The CI driver caught an exception with no typed `code` attribute. The block is still rendered typed, with `exception_type`, `can_execute=false` and a non-zero exit. | `v17/nhl_goalie_shot_runner.py` | — | No |

`NHL_REPLAY_BLOCKED:<code>:<type>` is the CI stderr marker for a typed block, not
a distinct failure code; the underlying code is always one of the rows above.

<!-- sport-failure-registry:NFL:begin -->
## NFL failure-code registry (November 2026)

Derived from production modules by `scripts/sport_failure_registry.py`; run it
after changing NFL code. A code that is a model label, table or metric
name, or a success disposition is catalogued in the tables below rather than
as a failure. `can_execute=false` and dry-run-only remain invariant.

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---|
| `NFL_ACTIVE_CALIBRATOR_NOT_AVAILABLE` | No inline message; terminal code raised in `load_champion_model`. | `nfl_event_model_v17.py` | — | No |
| `NFL_ARTIFACT_CHECKSUM_MISMATCH` | No inline message; terminal code raised in `load_champion_model`. | `nfl_event_model_v17.py` | — | No |
| `NFL_ARTIFACT_DIMENSION_MISMATCH` | No inline message; terminal code raised in `load_champion_model`. | `nfl_event_model_v17.py` | — | No |
| `NFL_ARTIFACT_INVALID_SCALER` | No inline message; terminal code raised in `load_champion_model`. | `nfl_event_model_v17.py` | — | No |
| `NFL_ARTIFACT_NONFINITE_PARAMETERS` | No inline message; terminal code raised in `load_champion_model`. | `nfl_event_model_v17.py` | — | No |
| `NFL_ATD_EXACT_ROUTE_REQUIRED` | No inline message; terminal code raised in `verify_anytime_td_gamebook`. | `v17/nfl_atd_gamebook_evidence.py` | — | No |
| `NFL_BOUNDS_MARGIN_INVALID` | No inline message; terminal code raised in `load_champion_model`. | `nfl_event_model_v17.py` | — | No |
| `NFL_CANONICAL_EVENT_IDENTITY_MISMATCH` | No inline message; terminal code raised in `resolve_nfl_team_event_evidence`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_CANONICAL_EVENT_ID_AMBIGUOUS` | No inline message; terminal code raised in `resolve_nfl_team_event_evidence`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_CANONICAL_EVENT_ID_MISMATCH` | No inline message; terminal code raised in `resolve_nfl_event_identity`. | `v17/nfl_prop_boundary_integrity.py` | — | No |
| `NFL_CANONICAL_EVENT_METADATA_INVALID` | No inline message; terminal code raised in `resolve_nfl_team_event_evidence`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_CANONICAL_EVENT_PLAYER_IDENTITY_REQUIRED` | No inline message; terminal code raised in `settle_nfl_scalar`. | `v17/nfl_prop_settlement_overlay.py` | — | No |
| `NFL_CANONICAL_GAME_ROW_MISSING` | No inline message; terminal code raised in `settle_nfl_scalar`. | `v17/nfl_prop_settlement_overlay.py` | — | No |
| `NFL_CANONICAL_NFLVERSE_PLAYER_STATS` | No inline message; terminal code raised in `module scope`. | `v17/nfl_prop_settlement_overlay.py` | — | No |
| `NFL_CANONICAL_PLAYER_GAME_ROW_REQUIRED` | No inline message; terminal code raised in `settle_nfl_scalar`. | `v17/nfl_prop_settlement_overlay.py` | — | No |
| `NFL_CANONICAL_PLAYER_STATS_SOURCE_UNAVAILABLE` | No inline message; terminal code raised in `settle_nfl_scalar`. | `v17/nfl_prop_settlement_overlay.py` | — | No |
| `NFL_CANONICAL_PREGAME_FEATURE_ROW` | No inline message; terminal code raised in `_attach_nfl_feature_consumption_metadata`. | `v17/nfl_team_event_publication.py` | — | No |
| `NFL_CANONICAL_SCHEDULE_BYTES_UNAVAILABLE` | No inline message; terminal code raised in `_load_latest_schedule_snapshot`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_CANONICAL_SCHEDULE_EMPTY` | No inline message; terminal code raised in `_load_latest_schedule_snapshot`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_CANONICAL_SCHEDULE_REQUERY` | No inline message; terminal code raised in `_final_refresh_proven`. | `v17/nfl_daily_stage_receipts.py` | `v17/nfl_team_event_publication.py` | No |
| `NFL_CANONICAL_SCHEDULE_SNAPSHOT_UNAVAILABLE` | No inline message; terminal code raised in `_load_latest_schedule_snapshot`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_CANONICAL_STADIUM_UNAVAILABLE` | No inline message; terminal code raised in `resolve_nfl_team_event_evidence`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_CERTIFIED_FULL_GAME_TOTAL_MODEL_NOT_REGISTERED` | No inline message; terminal code raised in `tiebreaker_capability`. | `v17/nfl_pickem_pool_optimizer.py` | — | No |
| `NFL_CHALLENGER_BOOTSTRAP_EMPTY` | No inline message; terminal code raised in `empirical_lower`. | `v17/nfl_ml_challenger_v2.py` | — | No |
| `NFL_CHALLENGER_BOOTSTRAP_PROBABILITY_INVALID` | No inline message; terminal code raised in `empirical_lower`. | `v17/nfl_ml_challenger_v2.py` | — | No |
| `NFL_CHALLENGER_BOOTSTRAP_QUANTILE_INVALID` | No inline message; terminal code raised in `empirical_lower`. | `v17/nfl_ml_challenger_v2.py` | — | No |
| `NFL_CHALLENGER_FORWARD_IDENTITY_MISSING` | No inline message; terminal code raised in `build_forward_shadow_record`. | `v17/nfl_ml_challenger_v2.py` | — | No |
| `NFL_CHALLENGER_LOCAL_CALIBRATION_EMPTY` | No inline message; terminal code raised in `wilson_lower`. | `v17/nfl_ml_challenger_v2.py` | — | No |
| `NFL_CHALLENGER_LOCAL_CALIBRATION_SUCCESS_COUNT_INVALID` | No inline message; terminal code raised in `wilson_lower`. | `v17/nfl_ml_challenger_v2.py` | — | No |
| `NFL_CHALLENGER_LOWER_BOUND_POINT_MISMATCH` | No inline message; terminal code raised in `build_forward_shadow_record`. | `v17/nfl_ml_challenger_v2.py` | — | No |
| `NFL_CHALLENGER_SELECTED_PROBABILITY_OUT_OF_RANGE` | No inline message; terminal code raised in `nearest_local_lower`. | `v17/nfl_ml_challenger_v2.py` | — | No |
| `NFL_CHAMPION_ARTIFACT_NOT_AVAILABLE` | No inline message; terminal code raised in `load_champion_model`. | `nfl_event_model_v17.py` | — | No |
| `NFL_CHAMPION_CALIBRATOR_ID_MISSING` | No inline message; terminal code raised in `load_champion_model`. | `nfl_event_model_v17.py` | — | No |
| `NFL_CURRENT_SEASON_PBP_ASSET_UNAVAILABLE` | No inline message; terminal code raised in `_pbp_asset`. | `v17/nfl_current_season_summary_refresh.py` | — | No |
| `NFL_CURRENT_SEASON_TRAINING_GAMES_UNAVAILABLE` | No inline message; terminal code raised in `_load_season_training_games`. | `v17/nfl_current_season_summary_refresh.py` | — | No |
| `NFL_DYNAMIC_CALIBRATION_PRODUCER_RECEIPT_IDENTITY_MISMATCH` | No inline message; terminal code raised in `_actual_producer_receipt`. | `v17/nfl_daily_stage_receipts.py` | — | No |
| `NFL_DYNAMIC_CALIBRATION_PRODUCER_RECEIPT_INVALID` | No inline message; terminal code raised in `_actual_producer_receipt`. | `v17/nfl_daily_stage_receipts.py` | — | No |
| `NFL_DYNAMIC_CALIBRATION_PRODUCER_RECEIPT_MISSING` | No inline message; terminal code raised in `_actual_producer_receipt`. | `v17/nfl_daily_stage_receipts.py` | — | No |
| `NFL_DYNAMIC_CALIBRATION_PRODUCER_RECEIPT_NUMERIC_MISMATCH` | No inline message; terminal code raised in `_actual_producer_receipt`. | `v17/nfl_daily_stage_receipts.py` | — | No |
| `NFL_DYNAMIC_CALIBRATION_PRODUCER_RECEIPT_TIMESTAMP_MISMATCH` | No inline message; terminal code raised in `_actual_producer_receipt`. | `v17/nfl_daily_stage_receipts.py` | — | No |
| `NFL_ESPN_EVENT_ID_REQUIRED` | No inline message; terminal code raised in `_nfl_event_finality`. | `v17/nfl_prop_settlement_overlay.py` | — | No |
| `NFL_EVENT_CALIBRATOR_UNAVAILABLE` | No inline message; terminal code raised in `p0_readiness`. | `nfl_event_model_contract.py` | — | No |
| `NFL_EVENT_CONTEXT_CALIBRATION_SAMPLE_INSUFFICIENT` | No inline message; terminal code raised in `_train_season_split_candidate`. | `v17/nfl_event_context_challenger.py` | — | No |
| `NFL_EVENT_CONTEXT_CHAMPION_METRICS_INCOMPLETE` | No inline message; terminal code raised in `_champion_comparison`. | `v17/nfl_event_context_challenger.py` | — | No |
| `NFL_EVENT_CONTEXT_CHAMPION_UNAVAILABLE` | No inline message; terminal code raised in `_active_champion`. | `v17/nfl_event_context_challenger.py` | — | No |
| `NFL_EVENT_CONTEXT_CLASS_DEGENERATE` | No inline message; terminal code raised in `_train_season_split_candidate`. | `v17/nfl_event_context_challenger.py` | — | No |
| `NFL_EVENT_CONTEXT_METADATA_MISMATCH` | No inline message; terminal code raised in `_train_season_split_candidate`. | `v17/nfl_event_context_challenger.py` | — | No |
| `NFL_EVENT_CONTEXT_METRICS_INVALID` | No inline message; terminal code raised in `_train_season_split_candidate`. | `v17/nfl_event_context_challenger.py` | — | No |
| `NFL_EVENT_CONTEXT_ROWS_EMPTY` | No inline message; terminal code raised in `build_rows`. | `v17/nfl_event_context_challenger.py` | — | No |
| `NFL_EVENT_CONTEXT_SPLIT_INVALID` | No inline message; terminal code raised in `_train_season_split_candidate`. | `v17/nfl_event_context_challenger.py` | — | No |
| `NFL_EVENT_CONTEXT_TRAIN_SAMPLE_INSUFFICIENT` | No inline message; terminal code raised in `_train_season_split_candidate`. | `v17/nfl_event_context_challenger.py` | — | No |
| `NFL_EVENT_CONTEXT_VALIDATION_SAMPLE_INSUFFICIENT` | No inline message; terminal code raised in `_train_season_split_candidate`. | `v17/nfl_event_context_challenger.py` | — | No |
| `NFL_EVENT_FEATURE_PACKET_MISSING` | No inline message; terminal code raised in `validate_feature_packet`. | `nfl_event_model_contract.py` | — | No |
| `NFL_EVENT_IDENTITY_BOUNDARY` | No inline message; terminal code raised in `_merge_response`. | `v17/nfl_prop_identity_boundary.py` | — | No |
| `NFL_EVENT_IDENTITY_RESOLUTION` | No inline message; terminal code raised in `_blocked_identity_outcome`. | `v17/nfl_prop_identity_boundary.py` | — | No |
| `NFL_EVENT_LEDGER_CLIENT_UNAVAILABLE` | No inline message; terminal code raised in `score_nfl_team_event_request`. | `v17/nfl_team_event_publication.py` | — | No |
| `NFL_EVENT_MARKET_UNSUPPORTED` | No inline message; terminal code raised in `validate_candidate_identity`. | `nfl_event_model_contract.py` | — | No |
| `NFL_EVENT_PERIOD_UNSUPPORTED` | No inline message; terminal code raised in `validate_candidate_identity`. | `nfl_event_model_contract.py` | — | No |
| `NFL_EVENT_PROBABILITY_CAPABILITY_UNAVAILABLE` | No inline message; terminal code raised in `p0_readiness`. | `nfl_event_model_contract.py` | — | No |
| `NFL_EVENT_SPORT_MISMATCH` | No inline message; terminal code raised in `validate_candidate_identity`. | `nfl_event_model_contract.py` | — | No |
| `NFL_EVENT_START_SLATE_DATE_MISMATCH` | No inline message; terminal code raised in `resolve_nfl_team_event_evidence`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_EVENT_START_TIME_INVALID` | No inline message; terminal code raised in `resolve_nfl_team_event_evidence`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_EVENT_STATUS_SOURCE_UNAVAILABLE` | No inline message; terminal code raised in `_nfl_event_finality`. | `v17/nfl_prop_settlement_overlay.py` | — | No |
| `NFL_FEATURE_ASSEMBLY_FAILED` | No inline message; terminal code raised in `score_nfl_team_event`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_FEATURE_ORDER_ARTIFACT_MISMATCH` | No inline message; terminal code raised in `load_champion_model`. | `nfl_event_model_v17.py` | — | No |
| `NFL_FEATURE_ORDER_HASH_MISMATCH` | No inline message; terminal code raised in `load_champion_model`. | `nfl_event_model_v17.py` | — | No |
| `NFL_FEATURE_ORDER_INPUT_MISMATCH` | No inline message; terminal code raised in `score_feature_row`. | `nfl_event_model_v17.py` | — | No |
| `NFL_FEATURE_SCHEMA_ARTIFACT_MISMATCH` | No inline message; terminal code raised in `load_champion_model`. | `nfl_event_model_v17.py` | — | No |
| `NFL_FEATURE_SCHEMA_INPUT_MISMATCH` | No inline message; terminal code raised in `score_feature_row`. | `nfl_event_model_v17.py` | — | No |
| `NFL_FEATURE_VECTOR_DIMENSION_MISMATCH` | No inline message; terminal code raised in `score_feature_row`. | `nfl_event_model_v17.py` | — | No |
| `NFL_FEATURE_VECTOR_NONFINITE` | No inline message; terminal code raised in `score_feature_row`. | `nfl_event_model_v17.py` | — | No |
| `NFL_FEATURE_VECTOR_NONNUMERIC` | No inline message; terminal code raised in `score_feature_row`. | `nfl_event_model_v17.py` | — | No |
| `NFL_FINAL_REFRESH_NOW_MUST_BE_TIMEZONE_AWARE` | No inline message; terminal code raised in `_nfl_final_refresh`. | `v17/nfl_team_event_publication.py` | — | No |
| `NFL_FINAL_REFRESH_SOURCE_PROOF_UNPROVEN` | No inline message; terminal code raised in `attach_nfl_daily_stage_receipt`. | `v17/nfl_daily_stage_receipts.py` | — | No |
| `NFL_FITTED_MODEL_ARTIFACT_UNAVAILABLE` | No inline message; terminal code raised in `p0_readiness`. | `nfl_event_model_contract.py` | — | No |
| `NFL_FITTED_SCORER_FAILED` | No inline message; terminal code raised in `score_nfl_team_event`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_FORWARD_CERTIFICATION_UNPROVEN` | No inline message; terminal code raised in `attach_nfl_daily_stage_receipt`. | `v17/nfl_daily_stage_receipts.py` | — | No |
| `NFL_GOVERNED_PROBABILITY_PACKAGE_INVALID` | No inline message; terminal code raised in `_govern`. | `v17/nfl_team_event_publication.py` | — | No |
| `NFL_GOVERNED_PUBLICATION_NOT_PROVEN` | No inline message; terminal code raised in `_govern`. | `v17/nfl_team_event_publication.py` | — | No |
| `NFL_GSIS_URL_NOT_CANONICAL` | No inline message; terminal code raised in `verify_anytime_td_gamebook`. | `v17/nfl_atd_gamebook_evidence.py` | — | No |
| `NFL_HISTORICAL_FEATURE_CONTEXT_UNAVAILABLE` | No inline message; terminal code raised in `_load_history`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_IMMUTABLE_PREDICTION_IDENTITY_AMBIGUOUS` | No inline message; terminal code raised in `_load_existing_prediction`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_IMMUTABLE_PREDICTION_IDENTITY_LOOKUP_FAILED` | No inline message; terminal code raised in `score_nfl_team_event`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_IMMUTABLE_PREDICTION_PERSISTENCE_FAILED` | No inline message; terminal code raised in `score_nfl_team_event`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_IMMUTABLE_PREDICTION_PERSISTENCE_NO_ROW` | No inline message; terminal code raised in `score_nfl_team_event`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_IMMUTABLE_PREDICTION_REUSE_SNAPSHOT_MISSING` | No inline message; terminal code raised in `_reused_prediction_result`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_IMMUTABLE_PREGAME_PREDICTION_READBACK` | No inline message; terminal code raised in `module scope`. | `v17/nfl_daily_stage_receipts.py` | — | No |
| `NFL_MODEL_CERTIFICATION_BLOCKED` | No inline message; terminal code raised in `score_nfl_team_event_request`. | `v17/nfl_team_event_publication.py` | — | No |
| `NFL_MODEL_CONSUMED_VECTOR_MISMATCH` | No inline message; terminal code raised in `score_feature_row`. | `nfl_event_model_v17.py` | — | No |
| `NFL_MODEL_PROMOTION_INVALID_RESPONSE` | No inline message; terminal code raised in `ensure_champion_model`. | `nfl_event_model_v17.py` | — | No |
| `NFL_PICKEM_ASYNC_IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST` | No inline message; terminal code raised in `_submit`. | `v17/nfl_pickem_async_runtime.py` | — | No |
| `NFL_PICKEM_ASYNC_RUN_NOT_FOUND` | No inline message; terminal code raised in `get_nfl_pickem_run`. | `v17/nfl_pickem_async_runtime.py` | — | No |
| `NFL_PICKEM_ASYNC_STATUS_UNAVAILABLE` | No inline message; terminal code raised in `get_nfl_pickem_run`. | `v17/nfl_pickem_async_runtime.py` | — | No |
| `NFL_PICKEM_ASYNC_SUBMISSION_PERSISTENCE_FAILED` | No inline message; terminal code raised in `submit_nfl_pickem`. | `v17/nfl_pickem_async_runtime.py` | — | No |
| `NFL_PRIOR_GAMES_ABLATION_FEATURE_ORDER_INVALID` | No inline message; terminal code raised in `challenger_feature_order`. | `v17/nfl_ml_prior_games_ablation.py` | — | No |
| `NFL_PRIOR_GAMES_ABLATION_NONFINITE_INPUT` | No inline message; terminal code raised in `_project_row`. | `v17/nfl_ml_prior_games_ablation.py` | — | No |
| `NFL_PRIOR_GAMES_ABLATION_SOURCE_ORDER_MISMATCH` | No inline message; terminal code raised in `_project_row`. | `v17/nfl_ml_prior_games_ablation.py` | — | No |
| `NFL_PRIOR_GAMES_ABLATION_SOURCE_VECTOR_INVALID` | No inline message; terminal code raised in `_project_row`. | `v17/nfl_ml_prior_games_ablation.py` | — | No |
| `NFL_PROBABILITY_PACKAGE_NONFINITE_OR_OUT_OF_RANGE` | No inline message; terminal code raised in `score_feature_row`. | `nfl_event_model_v17.py` | — | No |
| `NFL_PROBABILITY_PAIR_NOT_NORMALIZED` | No inline message; terminal code raised in `score_feature_row`. | `nfl_event_model_v17.py` | — | No |
| `NFL_PROP_BOX_SCORE_INVALID` | No inline message; terminal code raised in `history_pairs`. | `nfl_prop_model_adapter.py` | — | No |
| `NFL_PROP_FEATURE_VECTOR_OOD` | No inline message; terminal code raised in `nfl_prop_rolling_fitted_v1_adapter`. | `nfl_prop_model_adapter.py` | — | No |
| `NFL_PROP_HISTORY_CHRONOLOGY_UNVERIFIED` | No inline message; terminal code raised in `_history_unverified`. | `nfl_prop_auto_hydration.py` | — | No |
| `NFL_PROP_HISTORY_EMPTY` | nflverse season payload was empty | `nfl_prop_auto_hydration.py` | `v17/nfl_prop_memory_safety.py` | No |
| `NFL_PROP_HISTORY_INSUFFICIENT` | No inline message; terminal code raised in `_vector_from_pairs`. | `nfl_prop_model_adapter.py` | — | No |
| `NFL_PROP_HISTORY_INVALID` | No inline message; terminal code raised in `_finite`. | `nfl_prop_model_adapter.py` | — | No |
| `NFL_PROP_HISTORY_MISALIGNED` | No inline message; terminal code raised in `history_pairs`. | `nfl_prop_model_adapter.py` | — | No |
| `NFL_PROP_HISTORY_MISSING` | No inline message; terminal code raised in `history_pairs`. | `nfl_prop_model_adapter.py` | — | No |
| `NFL_PROP_LINEAR_PREDICTOR_INVALID` | No inline message; terminal code raised in `expected_value`. | `nfl_prop_model_adapter.py` | — | No |
| `NFL_PROP_OPPORTUNITY_INVALID` | No inline message; terminal code raised in `history_pairs`. | `nfl_prop_model_adapter.py` | — | No |
| `NFL_PROP_PMF_INVALID` | No inline message; terminal code raised in `distribution_for_vector`. | `nfl_prop_model_adapter.py` | — | No |
| `NFL_PROP_SOURCE_UNAVAILABLE` | required NFL evidence source could not be retrieved | `nfl_prop_auto_hydration.py` | — | No |
| `NFL_PROVIDER_EVENT_IDENTITY_INCOMPLETE` | No inline message; terminal code raised in `resolve_nfl_team_event_evidence`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_PROVIDER_EVENT_ID_CANONICAL_MATCH_AMBIGUOUS` | No inline message; terminal code raised in `resolve_nfl_team_event_evidence`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_PROVIDER_EVENT_ID_CANONICAL_MATCH_NOT_FOUND` | No inline message; terminal code raised in `resolve_nfl_team_event_evidence`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_PROVIDER_EVENT_ID_NOT_EXACTLY_RESOLVED` | No inline message; terminal code raised in `_nfl_event_finality`. | `v17/nfl_prop_settlement_overlay.py` | — | No |
| `NFL_PROVIDER_EVENT_TEAM_IDENTITY_CONFLICT` | No inline message; terminal code raised in `_nfl_event_finality`. | `v17/nfl_prop_settlement_overlay.py` | — | No |
| `NFL_PROVIDER_TEAM_IDENTITY_REQUIRED` | No inline message; terminal code raised in `_nfl_event_finality`. | `v17/nfl_prop_settlement_overlay.py` | — | No |
| `NFL_RECENT_GAMES_INSUFFICIENT` | fewer than ten prior relevant NFL games were available from nflverse | `nfl_prop_auto_hydration.py` | — | No |
| `NFL_REQUESTED_SLATE_DATE_INVALID` | No inline message; terminal code raised in `resolve_nfl_team_event_evidence`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_REQUESTED_TIMEZONE_INVALID` | No inline message; terminal code raised in `resolve_nfl_team_event_evidence`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_SCHEDULE_RAW_OBJECT_PATH_MISSING` | No inline message; terminal code raised in `_raw_object_path`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_SCHEDULE_RAW_OBJECT_URI_INVALID` | No inline message; terminal code raised in `_raw_object_path`. | `v17/nfl_team_event_specialist.py` | — | No |
| `NFL_SCORE_SNAPSHOT_ID_MISSING` | No inline message; terminal code raised in `_govern`. | `v17/nfl_team_event_publication.py` | — | No |
| `NFL_SOURCE_SNAPSHOT_INSERT_RETURNED_NO_ID` | No inline message; terminal code raised in `_snapshot_id`. | `nfl_event_hydration_runtime.py` | — | No |
| `NFL_SPREAD_CONTEXT_NONFINITE_INPUT` | No inline message; terminal code raised in `_matrix`. | `v17/nfl_spread_context_challenger.py` | — | No |
| `NFL_SPREAD_CONTEXT_RESIDUALS_INVALID` | No inline message; terminal code raised in `train_candidate`. | `v17/nfl_spread_context_challenger.py` | — | No |
| `NFL_SPREAD_CONTEXT_ROWS_EMPTY` | No inline message; terminal code raised in `build_margin_rows`. | `v17/nfl_spread_context_challenger.py` | — | No |
| `NFL_SPREAD_CONTEXT_SEASON_HOLDOUT_INSUFFICIENT` | train={len(train)} calibration={len(calibration)} validation={len(validation)} | `v17/nfl_spread_context_challenger.py` | — | No |
| `NFL_SPREAD_V3_CALIBRATION_SPLIT_INSUFFICIENT` | need >= {MIN_RESIDUAL_FIT_N + MIN_SCALE_TUNE_N} 2024 rows, got {len(ordered)} | `v17/nfl_spread_scale_calibrated_challenger.py` | — | No |
| `NFL_SPREAD_V3_CALIBRATION_TIME_BLOCK_SPLIT_INSUFFICIENT` | residual_fit={len(fit_rows)} scale_tune={len(tune_rows)} | `v17/nfl_spread_scale_calibrated_challenger.py` | — | No |
| `NFL_SPREAD_V3_CRPS_INPUT_INVALID` | CRPS requires finite empirical samples and outcome | `v17/nfl_spread_scale_calibrated_challenger.py` | — | No |
| `NFL_SPREAD_V3_FORWARD_RESERVE_MISMATCH` | V2 fit and V3 partitions disagree on forward reserve | `v17/nfl_spread_scale_calibrated_challenger.py` | — | No |
| `NFL_SPREAD_V3_RESIDUALS_INVALID` | V3 residual distribution is unavailable or non-finite | `v17/nfl_spread_scale_calibrated_challenger.py` | — | No |
| `NFL_SPREAD_V3_RESIDUAL_FIT_INVALID` | valid residual_fit_n={len(residuals)} | `v17/nfl_spread_scale_calibrated_challenger.py` | — | No |
| `NFL_SPREAD_V3_RESIDUAL_SCALE_INVALID` | residual scale must be positive and finite, got {scale!r} | `v17/nfl_spread_scale_calibrated_challenger.py` | — | No |
| `NFL_SPREAD_V3_VALIDATION_EXACT_LINE_COVERAGE_INCOMPLETE` | No inline message; terminal code raised in `run_nfl_context_v3_scale_calibrated_replay`. | `v17/nfl_spread_scale_calibrated_challenger.py` | — | No |
| `NFL_SPREAD_V3_VALIDATION_IDENTITY_MISMATCH` | V2 fit and V3 partitions disagree on untouched validation identity | `v17/nfl_spread_scale_calibrated_challenger.py` | — | No |
| `NFL_STAGE_PROOF_*` | No inline message; terminal code raised in `attach_nfl_daily_stage_receipt`. | `v17/nfl_daily_stage_receipts.py` | — | No |
| `NFL_STAGE_PROOF_CALIBRATED_BOUNDS_INVALID` | No inline message; terminal code raised in `attach_nfl_daily_stage_receipt`. | `v17/nfl_daily_stage_receipts.py` | — | No |
| `NFL_STAGE_PROOF_DYNAMIC_CALIBRATION_UNPROVEN` | No inline message; terminal code raised in `attach_nfl_daily_stage_receipt`. | `v17/nfl_daily_stage_receipts.py` | — | No |
| `NFL_STAGE_PROOF_EXACT_ROW_UNAVAILABLE` | No inline message; terminal code raised in `attach_nfl_daily_stage_receipt`. | `v17/nfl_daily_stage_receipts.py` | — | No |
| `NFL_STAGE_PROOF_MODEL_PACKAGE_NOT_CERTIFIED` | No inline message; terminal code raised in `attach_nfl_daily_stage_receipt`. | `v17/nfl_daily_stage_receipts.py` | — | No |
| `NFL_STAGE_PROOF_PREDICTION_ID_MISSING` | No inline message; terminal code raised in `attach_nfl_daily_stage_receipt`. | `v17/nfl_daily_stage_receipts.py` | — | No |
| `NFL_STAGE_PROOF_READBACK_FAILED` | No inline message; terminal code raised in `attach_nfl_daily_stage_receipt`. | `v17/nfl_daily_stage_receipts.py` | — | No |
| `NFL_STAGE_PROOF_STALE_MODEL` | No inline message; terminal code raised in `attach_nfl_daily_stage_receipt`. | `v17/nfl_daily_stage_receipts.py` | — | No |
| `NFL_STAGE_PROOF_UNPROVEN` | No inline message; terminal code raised in `attach_nfl_daily_stage_receipt`. | `v17/nfl_daily_stage_receipts.py` | — | No |
| `NFL_STAT_SETTLEMENT_UNSUPPORTED` | No inline message; terminal code raised in `settle_nfl_scalar`. | `v17/nfl_prop_settlement_overlay.py` | — | No |
| `NFL_TARGET_EVENT_IDENTITY_INVALID` | No inline message; terminal code raised in `build_prediction_feature_row`. | `nfl_event_prediction_features.py` | — | No |
| `NFL_TEMPORAL_SPLIT_CLASS_IMBALANCE` | No inline message; terminal code raised in `fit_candidate`. | `nfl_event_model_v17.py` | — | No |
| `NFL_TOTAL_POINTS_ALPHA_GRID_EMPTY` | No inline message; terminal code raised in `train_candidate`. | `v17/nfl_total_points_challenger.py` | — | No |
| `NFL_TOTAL_POINTS_ARTIFACT_INVALID` | No inline message; terminal code raised in `artifact_from_json`. | `v17/nfl_total_points_challenger.py` | — | No |
| `NFL_TOTAL_POINTS_ARTIFACT_SEASON_UNSUPPORTED` | No inline message; terminal code raised in `score_tiebreaker`. | `v17/nfl_total_points_tiebreaker_specialist.py` | — | No |
| `NFL_TOTAL_POINTS_CALIBRATION_RESIDUALS_INVALID` | No inline message; terminal code raised in `train_candidate`. | `v17/nfl_total_points_challenger.py` | — | No |
| `NFL_TOTAL_POINTS_EVENT_IDENTITY_INVALID` | No inline message; terminal code raised in `score_tiebreaker`. | `v17/nfl_total_points_challenger.py` | `v17/nfl_total_points_tiebreaker_specialist.py` | No |
| `NFL_TOTAL_POINTS_NONFINITE_FEATURE` | No inline message; terminal code raised in `build_rows`. | `v17/nfl_total_points_challenger.py` | — | No |
| `NFL_TOTAL_POINTS_NONFINITE_INPUT` | No inline message; terminal code raised in `_matrix`. | `v17/nfl_total_points_challenger.py` | — | No |
| `NFL_TOTAL_POINTS_PREDICTION_NONFINITE` | No inline message; terminal code raised in `_predict`. | `v17/nfl_total_points_challenger.py` | — | No |
| `NFL_TOTAL_POINTS_PREGAME_HISTORY_INSUFFICIENT` | No inline message; terminal code raised in `score_tiebreaker`. | `v17/nfl_total_points_challenger.py` | — | No |
| `NFL_TOTAL_POINTS_PROJECTION_INVALID` | No inline message; terminal code raised in `score_tiebreaker`. | `v17/nfl_total_points_tiebreaker_specialist.py` | — | No |
| `NFL_TOTAL_POINTS_REPLAY_LENGTH_INVALID` | No inline message; terminal code raised in `replay_metrics`. | `v17/nfl_total_points_tiebreaker_specialist.py` | — | No |
| `NFL_TOTAL_POINTS_ROWS_EMPTY` | No inline message; terminal code raised in `build_rows`. | `v17/nfl_total_points_challenger.py` | — | No |
| `NFL_TOTAL_POINTS_SEASON_HOLDOUT_INSUFFICIENT` | No inline message; terminal code raised in `train_candidate`. | `v17/nfl_total_points_challenger.py` | — | No |
| `NFL_TRAINING_FEATURE_ROWS_EMPTY` | No inline message; terminal code raised in `ensure_champion_model`. | `nfl_event_model_v17.py` | — | No |
| `NFL_UPSTREAM_TERMINAL_UNPROVEN` | No inline message; terminal code raised in `attach_nfl_daily_stage_receipt`. | `v17/nfl_daily_stage_receipts.py` | — | No |
| `NFL_WEEK_AUTHENTICATED_TRANSPORT_REQUIRED` | No inline message; terminal code raised in `execute_nfl_week_snapshot_preflight`. | `v17/nfl_multiday_snapshot_adapter.py` | — | No |
| `NFL_WEEK_BATCH_LIMIT` | No inline message; terminal code raised in `plan_nfl_week_dates`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_DAILY_AUTH_OR_TERMINAL_INVALID` | No inline message; terminal code raised in `reconcile_nfl_week_snapshots`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_DAILY_EVENT_MISMATCH` | No inline message; terminal code raised in `reconcile_nfl_week_snapshots`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_DAILY_ROWS_INVALID` | No inline message; terminal code raised in `reconcile_nfl_week_snapshots`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_DAILY_ROW_IDENTITY_INVALID` | No inline message; terminal code raised in `reconcile_nfl_week_snapshots`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_DAILY_RUN_ID_INVALID_OR_DUPLICATE` | No inline message; terminal code raised in `reconcile_nfl_week_snapshots`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_DATE_COVERAGE_MISMATCH` | No inline message; terminal code raised in `reconcile_nfl_week_snapshots`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_DATE_REQUEST_BUDGET_INVALID` | No inline message; terminal code raised in `execute_nfl_week_snapshot_preflight`. | `v17/nfl_multiday_snapshot_adapter.py` | — | No |
| `NFL_WEEK_EVENT_IDS_INVALID` | No inline message; terminal code raised in `plan_nfl_week_dates`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_EXACT_ONCE_MISMATCH` | No inline message; terminal code raised in `reconcile_nfl_week_snapshots`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_FIXTURE_MISMATCH` | No inline message; terminal code raised in `plan_nfl_week_dates`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_FIXTURE_NOT_VERIFIED_PREGAME` | No inline message; terminal code raised in `plan_nfl_week_dates`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_IDENTITY_UNVERIFIED` | No inline message; terminal code raised in `plan_nfl_week_dates`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_KICKOFF_NOT_FUTURE` | No inline message; terminal code raised in `plan_nfl_week_dates`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_NOT_ELIGIBLE` | No inline message; terminal code raised in `plan_nfl_week_dates`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_PLAN_EVENT_COVERAGE_INVALID` | No inline message; terminal code raised in `reconcile_nfl_week_snapshots`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_PLAN_NOT_READY` | No inline message; terminal code raised in `reconcile_nfl_week_snapshots`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_RECONCILIATION_FAILED` | No inline message; terminal code raised in `reconcile_nfl_week_snapshots`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_RESPONSE_DATE_MISMATCH` | No inline message; terminal code raised in `execute_nfl_week_snapshot_preflight`. | `v17/nfl_multiday_snapshot_adapter.py` | — | No |
| `NFL_WEEK_ROW_INVALID_OR_EXHAUSTED` | No inline message; terminal code raised in `reconcile_nfl_week_snapshots`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_SNAPSHOT_TRANSPORT_UNVERIFIED` | No inline message; terminal code raised in `execute_nfl_week_snapshot_preflight`. | `v17/nfl_multiday_snapshot_adapter.py` | — | No |
| `NFL_WEEK_SOURCE_STALE_OR_FUTURE` | No inline message; terminal code raised in `plan_nfl_week_dates`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_SOURCE_TIME_UNVERIFIED` | No inline message; terminal code raised in `plan_nfl_week_dates`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_SOURCE_UNVERIFIED` | No inline message; terminal code raised in `plan_nfl_week_dates`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_THRESHOLD_INVALID` | No inline message; terminal code raised in `plan_nfl_week_dates`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |
| `NFL_WEEK_TRUSTED_CLOCK_REQUIRED` | No inline message; terminal code raised in `plan_nfl_week_dates`. | `v17/nfl_multiday_acceptance_preflight.py` | — | No |

<!-- sport-failure-registry:NFL:end -->

<!-- sport-failure-registry:NBA:begin -->
## NBA failure-code registry (November 2026)

Derived from production modules by `scripts/sport_failure_registry.py`; run it
after changing NBA code. A code that is a model label, table or metric
name, or a success disposition is catalogued in the tables below rather than
as a failure. `can_execute=false` and dry-run-only remain invariant.

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---|
| `NBA_CALIBRATOR_PAYLOAD_RESOLVER_UNAVAILABLE` | No inline message; terminal code raised in `prepare_nba_current_pregame_scorer_receipt`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_CALIBRATOR_RESOLUTION_FAILED` | No inline message; terminal code raised in `prepare_nba_current_pregame_scorer_receipt`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_CANONICAL_EVENT_AMBIGUOUS` | No inline message; terminal code raised in `resolve_nba_current_event_identity`. | `v17/nba_event_identity.py` | — | No |
| `NBA_CANONICAL_EVENT_NOT_FOUND` | No inline message; terminal code raised in `resolve_nba_current_event_identity`. | `v17/nba_event_identity.py` | — | No |
| `NBA_CANONICAL_IDENTITY_INCOMPLETE` | No inline message; terminal code raised in `prepare_nba_current_pregame_scorer_receipt`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_CANONICAL_IDENTITY_RESOLUTION_FAILED` | No inline message; terminal code raised in `prepare_nba_current_pregame_scorer_receipt`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_CERTIFICATION_RESOLUTION_FAILED` | No inline message; terminal code raised in `prepare_nba_current_pregame_scorer_receipt`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_CERTIFICATION_SPECIALIST_SCOPE_OR_MODEL_MISMATCH` | No inline message; terminal code raised in `prepare_nba_current_pregame_scorer_receipt`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_CERTIFIED_CALIBRATOR_INVALID` | No inline message; terminal code raised in `prepare_nba_current_pregame_scorer_receipt`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_CERTIFIED_CALIBRATOR_UNAVAILABLE` | No inline message; terminal code raised in `prepare_nba_current_pregame_scorer_receipt`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_CERTIFIED_MODEL_ARTIFACT_CERTIFICATION_MISMATCH` | No inline message; terminal code raised in `resolve_nba_champion_artifact`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_CERTIFIED_MODEL_ARTIFACT_LOOKUP_FAILED` | No inline message; terminal code raised in `resolve_nba_champion_artifact`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND` | No inline message; terminal code raised in `resolve_nba_champion_artifact`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_CERTIFIED_MODEL_ARTIFACT_PAYLOAD_INVALID` | No inline message; terminal code raised in `_research_receipt`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_CERTIFIED_MODEL_ARTIFACT_PAYLOAD_MISMATCH` | No inline message; terminal code raised in `_research_receipt`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_CERTIFIED_MODEL_OUTPUT_INVALID` | No inline message; terminal code raised in `_research_receipt`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_CHAMPION_ARTIFACT_RESOLUTION_FAILED` | No inline message; terminal code raised in `prepare_nba_current_pregame_scorer_receipt`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_CHANGED_STARTERS_RECEIPT_UNVERIFIED` | No inline message; terminal code raised in `_rotation_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_CONTROLLING_SPECIALIST_NOT_CERTIFIED` | No inline message; terminal code raised in `prepare_nba_current_pregame_scorer_receipt`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_CURRENT_FEATURES_INCOMPLETE` | No inline message; terminal code raised in `_feature_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_CURRENT_FEATURES_INVALID` | No inline message; terminal code raised in `_feature_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_CURRENT_PREGAME_FEATURES_NOT_READY` | No inline message; terminal code raised in `_feature_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_CURRENT_PREGAME_FEATURE_HYDRATION_UNAVAILABLE` | No inline message; terminal code raised in `_feature_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_CURRENT_ROSTER_ACTIVE_SOURCE_VERIFIED_ROLE_NOT_PROVEN` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_DYNAMIC_BOUNDS_POLICY_NOT_APPROVED` | No inline message; terminal code raised in `prepare_nba_current_pregame_scorer_receipt`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_EVENT_TIME_INVALID` | No inline message; terminal code raised in `_aware`. | `v17/nba_event_identity.py` | — | No |
| `NBA_FEATURE_AS_OF_AFTER_CUTOFF` | No inline message; terminal code raised in `_feature_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_FEATURE_AS_OF_INVALID` | No inline message; terminal code raised in `_feature_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_FEATURE_DATASET_ID_MISSING` | No inline message; terminal code raised in `_feature_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_FEATURE_EVENT_IDENTITY_MISMATCH` | No inline message; terminal code raised in `_feature_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_FEATURE_EVENT_TIME_MISMATCH` | No inline message; terminal code raised in `_feature_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_FEATURE_LINEAGE_MISSING` | No inline message; terminal code raised in `_feature_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_FEATURE_SCHEMA_MISMATCH` | No inline message; terminal code raised in `_feature_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_FEATURE_SPORT_MISMATCH` | No inline message; terminal code raised in `_feature_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_FEATURE_TEAM_IDENTITY_MISMATCH` | No inline message; terminal code raised in `_feature_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_FEATURE_TIMESTAMP_INVALID_FOR_PREGAME` | No inline message; terminal code raised in `_feature_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_FEATURE_TIMESTAMP_MISSING` | No inline message; terminal code raised in `_feature_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_FITTED_RESEARCH_SCORER_FAILED` | No inline message; terminal code raised in `prepare_nba_current_pregame_scorer_receipt`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_HISTORY_BOX_GAME_ID_MISMATCH` | No inline message; terminal code raised in `acquire_nba_points_l10`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_HISTORY_BOX_PLAYER_ID_MISSING_OR_DUPLICATE` | No inline message; terminal code raised in `acquire_nba_points_l10`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_HISTORY_BOX_SCORE_SCHEMA_INVALID` | No inline message; terminal code raised in `acquire_nba_points_l10`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_HISTORY_BOX_TIME_CONTRADICTION` | No inline message; terminal code raised in `acquire_nba_points_l10`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_HISTORY_DUPLICATE_GAME_CONTRADICTION` | No inline message; terminal code raised in `acquire_nba_points_l10`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_HISTORY_EVENT_NOT_PREGAME` | No inline message; terminal code raised in `acquire_nba_points_l10`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_HISTORY_GAMELOG_SCHEMA_INVALID` | No inline message; terminal code raised in `_gamelog_rows`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_HISTORY_GAME_DATE_INVALID` | No inline message; terminal code raised in `_date`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_HISTORY_GAME_ID_INVALID` | No inline message; terminal code raised in `acquire_nba_points_l10`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_HISTORY_GAME_NOT_FINAL` | No inline message; terminal code raised in `acquire_nba_points_l10`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_HISTORY_L10_INSUFFICIENT` | No inline message; terminal code raised in `acquire_nba_points_l10`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_HISTORY_L10_VERIFIED_NO_CURRENT_ROLE_OR_LINE` | No inline message; terminal code raised in `acquire_nba_points_l10`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_HISTORY_PLAYER_ID_INVALID` | No inline message; terminal code raised in `acquire_nba_points_l10`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_HISTORY_PLAYER_ID_MISMATCH` | No inline message; terminal code raised in `acquire_nba_points_l10`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_HISTORY_POINTS_DISAGREE_WITH_BOX` | No inline message; terminal code raised in `acquire_nba_points_l10`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_HISTORY_POINTS_INVALID` | No inline message; terminal code raised in `_points`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_HISTORY_SEASON_SCOPE_INVALID` | No inline message; terminal code raised in `acquire_nba_points_l10`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_HISTORY_SOURCE_HTTP_FAILURE` | No inline message; terminal code raised in `_get`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_HISTORY_SOURCE_INVALID` | No inline message; terminal code raised in `_get`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_HISTORY_SOURCE_UNAVAILABLE` | No inline message; terminal code raised in `_get`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_HISTORY_TIME_INVALID` | No inline message; terminal code raised in `_utc`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_HISTORY_TIME_NAIVE` | No inline message; terminal code raised in `_utc`. | `v17/nba_points_official_history.py` | — | No |
| `NBA_INJURY_REPORT_NOT_CURRENT` | No inline message; terminal code raised in `_rotation_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_LINEUP_EVIDENCE_MISSING` | No inline message; terminal code raised in `_rotation_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_LINEUP_UPDATE_TIMESTAMP_MISSING` | No inline message; terminal code raised in `_rotation_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_POINTS_ADJACENT_LINE_NOT_EXACT` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_CANONICAL_EVENT_UNRESOLVED` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_CANONICAL_EVENT_UNVERIFIED` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_CANONICAL_EVIDENCE_REJECTED` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_CANONICAL_TEAM_MISMATCH` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_CAPTURE_NOT_PREGAME` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_DIRECTION_INVALID` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_EVENT_NOT_PREGAME` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_EXACT_LINE_MISMATCH` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_GAME_BOX_SCORE_MISMATCH` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_L10_EVIDENCE_INCOMPLETE` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_LINE_TIMESTAMP_INVALID` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_LINE_UNSUPPORTED` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_NOW_NAIVE` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_PERIOD_UNSUPPORTED` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_PLAYER_AVAILABILITY_HOLD` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_PRIOR_GAME_DUPLICATE_OR_TARGET` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_PRIOR_GAME_FUTURE_LEAKAGE` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_PRIOR_GAME_INVALID` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_PRIOR_GAME_PLAYER_MISMATCH` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_ROLE_EVIDENCE_MISSING` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_ROLE_TIMESTAMP_INVALID` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_ROSTER_PLAYER_MISMATCH` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_ROSTER_TIMESTAMP_INVALID` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_SNAPSHOT_IDENTITY_MISMATCH` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_SNAPSHOT_LINE_MISMATCH` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_SOURCE_IDENTITY_INVALID` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_SOURCE_TIMESTAMPS_MISSING` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_SOURCE_TIMESTAMP_INVALID` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_POINTS_TEAM_IDENTITY_CONFLICT` | No inline message; terminal code raised in `build_nba_points_snapshot`. | `v17/nba_points_snapshot_bridge.py` | — | No |
| `NBA_PROVIDER_TEAM_ALIAS_INVALID` | No inline message; terminal code raised in `resolve_nba_current_event_identity`. | `v17/nba_event_identity.py` | — | No |
| `NBA_ROSTER_ACTIVE_STATUS_NOT_AFFIRMED` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_CAPTURE_IN_FUTURE` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_CLOCK_INVALID` | No inline message; terminal code raised in `acquire_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_CROSS_PROVIDER_KICKOFF_MISMATCH` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_CROSS_PROVIDER_TEAM_MISMATCH` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_ESPN_COMPETITION_AMBIGUOUS` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_ESPN_COMPETITION_MISMATCH` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_ESPN_EVENT_ID_INVALID` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_ESPN_EVENT_MISMATCH` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_ESPN_HEADER_INVALID` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_ESPN_TEAMS_INVALID` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_NBA_GAME_ID_INVALID` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_NBA_GAME_ID_MISMATCH` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_NBA_GAME_NOT_SCHEDULED` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_NBA_PLAYERS_MISSING` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_NBA_PLAYER_ID_INVALID` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_NBA_PLAYER_SCHEMA_INVALID` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_NOT_PLAYING_REASON_PRESENT` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_NOT_PREGAME` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_NOW_NAIVE` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_PLAYER_MISSING_OR_AMBIGUOUS` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_PLAYER_NAME_MISSING` | No inline message; terminal code raised in `verify_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_PROVIDER_HTTP_FAILURE` | No inline message; terminal code raised in `acquire_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_PROVIDER_SCHEMA_INVALID` | No inline message; terminal code raised in `acquire_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_PROVIDER_UNAVAILABLE` | No inline message; terminal code raised in `acquire_nba_points_current_roster`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_ROSTER_TEAM_TRICODE_INVALID` | No inline message; terminal code raised in `_team_key`. | `v17/nba_points_pregame_roster.py` | — | No |
| `NBA_SCALAR_ARTIFACT_VERSION_COLLISION` | No inline message; terminal code raised in `derive_and_register_candidates`. | `v17/nba_scalar_candidate_registry.py` | — | No |
| `NBA_SCALAR_CANDIDATE_ARTIFACT_AMBIGUOUS` | Multiple research candidates resolved for NBA {stat} | `v17/nba_scalar_candidate_bridge.py` | — | No |
| `NBA_SCALAR_CANDIDATE_ARTIFACT_FORMAT_UNSUPPORTED` | No inline message; terminal code raised in `_candidate_artifact`. | `v17/nba_scalar_candidate_bridge.py` | — | No |
| `NBA_SCALAR_CANDIDATE_ARTIFACT_INVALID` | No inline message; terminal code raised in `_candidate_artifact`. | `v17/nba_scalar_candidate_bridge.py` | — | No |
| `NBA_SCALAR_CANDIDATE_ARTIFACT_NOT_FOUND` | No activated research candidate exists for NBA {stat} | `v17/nba_scalar_candidate_bridge.py` | — | No |
| `NBA_SCALAR_CANDIDATE_ARTIFACT_REGISTRY_UNAVAILABLE` | NBA scalar candidate artifact registry lookup failed | `v17/nba_scalar_candidate_bridge.py` | — | No |
| `NBA_SCALAR_CANDIDATE_AUTHORITY_CONFLICT` | No inline message; terminal code raised in `_candidate_artifact`. | `v17/nba_scalar_candidate_bridge.py` | — | No |
| `NBA_SCALAR_CANDIDATE_DERIVATION_FAILED` | No inline message; terminal code raised in `build_candidate_rows`. | `v17/nba_scalar_candidate_registry.py` | — | No |
| `NBA_SCALAR_CANDIDATE_LIFECYCLE_INVALID` | No inline message; terminal code raised in `_candidate_artifact`. | `v17/nba_scalar_candidate_bridge.py` | — | No |
| `NBA_SCALAR_CANDIDATE_NOT_RESEARCH_ACTIVE` | No inline message; terminal code raised in `_candidate_artifact`. | `v17/nba_scalar_candidate_bridge.py` | — | No |
| `NBA_SCALAR_CANDIDATE_PAYLOAD_INVALID` | No inline message; terminal code raised in `_candidate_artifact`. | `v17/nba_scalar_candidate_bridge.py` | — | No |
| `NBA_SCALAR_CANDIDATE_ROUTE_IDENTITY_MISMATCH` | No inline message; terminal code raised in `_candidate_artifact`. | `v17/nba_scalar_candidate_bridge.py` | — | No |
| `NBA_SCALAR_CANDIDATE_SCORER_EXCEPTION` | NBA scalar candidate scorer threw an unexpected exception | `v17/nba_scalar_candidate_bridge.py` | — | No |
| `NBA_SCALAR_CANDIDATE_SCORER_INVALID` | No inline message; terminal code raised in `score_nba_scalar_candidate_research`. | `v17/nba_scalar_candidate_bridge.py` | — | No |
| `NBA_SCALAR_CANDIDATE_SPECIALIST_MISMATCH` | No inline message; terminal code raised in `_candidate_artifact`. | `v17/nba_scalar_candidate_bridge.py` | — | No |
| `NBA_SCALAR_CANDIDATE_UNCALIBRATED` | No inline message; terminal code raised in `score_nba_scalar_candidate_research`. | `v17/nba_scalar_candidate_bridge.py` | `v17/nba_scalar_fitted_candidate.py` | No |
| `NBA_SCALAR_CANONICAL_ACTION_CANARY_MISSING` | No inline message; terminal code raised in `build_candidate_rows`. | `v17/nba_scalar_candidate_registry.py` | — | No |
| `NBA_SCALAR_EVIDENCE_BRIDGE_FAILED` | NBA scalar evidence retrieval failed | `v17/nba_scalar_candidate_bridge.py` | — | No |
| `NBA_SCALAR_EVIDENCE_INCOMPLETE` | NBA scalar candidate inputs are incomplete | `v17/nba_scalar_candidate_bridge.py` | — | No |
| `NBA_SCALAR_EXACT_LINE_CALIBRATION_MISSING` | No inline message; terminal code raised in `build_candidate_rows`. | `v17/nba_scalar_candidate_registry.py` | — | No |
| `NBA_SCALAR_FORWARD_CERTIFICATION_REQUIRED` | No inline message; terminal code raised in `simulate_exact_line`. | `v17/nba_scalar_fitted_candidate.py` | — | No |
| `NBA_SCALAR_FORWARD_COHORT_NOT_CERTIFIED` | No inline message; terminal code raised in `build_candidate_rows`. | `v17/nba_scalar_candidate_registry.py` | — | No |
| `NBA_SCALAR_ROUTE_UNSUPPORTED` | No inline message; terminal code raised in `score_nba_scalar_candidate_research`. | `v17/nba_scalar_candidate_bridge.py` | — | No |
| `NBA_SCALAR_SOURCE_ARTIFACT_INVALID` | No inline message; terminal code raised in `_source_row`. | `v17/nba_scalar_candidate_registry.py` | — | No |
| `NBA_SCALAR_SOURCE_ARTIFACT_MISSING` | No inline message; terminal code raised in `_source_row`. | `v17/nba_scalar_candidate_registry.py` | — | No |
| `NBA_SCALAR_SOURCE_CHECKSUM_MISSING` | No inline message; terminal code raised in `_source_row`. | `v17/nba_scalar_candidate_registry.py` | — | No |
| `NBA_SCALAR_SOURCE_GOVERNANCE_INVALID` | No inline message; terminal code raised in `_source_row`. | `v17/nba_scalar_candidate_registry.py` | — | No |
| `NBA_SCALAR_SOURCE_MODEL_FAMILY_INVALID` | No inline message; terminal code raised in `_source_row`. | `v17/nba_scalar_candidate_registry.py` | — | No |
| `NBA_SCALAR_SOURCE_PAYLOAD_INVALID` | No inline message; terminal code raised in `_source_row`. | `v17/nba_scalar_candidate_registry.py` | — | No |
| `NBA_SCALAR_SOURCE_PROVENANCE_MISSING` | No inline message; terminal code raised in `_source_row`. | `v17/nba_scalar_candidate_registry.py` | — | No |
| `NBA_SCALAR_SOURCE_PROVIDER_INVALID` | No inline message; terminal code raised in `_source_row`. | `v17/nba_scalar_candidate_registry.py` | — | No |
| `NBA_SCHEDULE_SOURCE_UNAVAILABLE` | No inline message; terminal code raised in `_schedule_rows`. | `v17/nba_event_identity.py` | — | No |
| `NBA_SOURCE_EVENT_REPLAY_NOT_VERIFIED` | No inline message; terminal code raised in `_feature_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_SOURCE_SNAPSHOT_AFTER_FEATURE` | No inline message; terminal code raised in `_feature_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_SOURCE_SNAPSHOT_AFTER_KICKOFF` | No inline message; terminal code raised in `_feature_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_SOURCE_SNAPSHOT_ID_MISMATCH` | No inline message; terminal code raised in `_feature_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_SOURCE_SNAPSHOT_PROVENANCE_MISSING` | No inline message; terminal code raised in `_feature_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_SOURCE_TIMESTAMP_ATTESTATION_NOT_VERIFIED` | No inline message; terminal code raised in `_feature_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_STARTER_CHANGED_AFTER_FEATURE_HYDRATION` | No inline message; terminal code raised in `_rotation_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |
| `NBA_STARTING_ROTATION_NOT_CONFIRMED` | No inline message; terminal code raised in `_rotation_blocker`. | `v17/nba_current_pregame_scorer.py` | — | No |

<!-- sport-failure-registry:NBA:end -->

<!-- sport-failure-registry:NCAAF:begin -->
## NCAAF failure-code registry (November 2026)

Derived from production modules by `scripts/sport_failure_registry.py`; run it
after changing NCAAF code. A code that is a model label, table or metric
name, or a success disposition is catalogued in the tables below rather than
as a failure. `can_execute=false` and dry-run-only remain invariant.

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---|
| `NCAAF_25_ROW_CALIBRATION_REVIEW_NOT_PASSED` | No inline message; terminal code raised in `assess_ncaaf_trust`. | `ncaaf_trust.py` | — | No |
| `NCAAF_25_SETTLED_REVIEW_NOT_REACHED` | No inline message; terminal code raised in `assess_ncaaf_trust`. | `ncaaf_trust.py` | — | No |
| `NCAAF_50_ROW_CONFIRMATION_NOT_PASSED` | No inline message; terminal code raised in `assess_ncaaf_trust`. | `ncaaf_trust.py` | — | No |
| `NCAAF_ACTIVE_BANNED_FAILURE_PATTERN` | No inline message; terminal code raised in `assess_ncaaf_trust`. | `ncaaf_trust.py` | `ncaaf_trust_tests.py` | No |
| `NCAAF_ARTIFACT_NOT_PUBLISHABLE` | Certified registry row has not cleared probability publication governance. | `ncaaf_fitted_provider.py` | — | No |
| `NCAAF_AVAILABILITY_INGESTION_FAILED` | No inline message; terminal code raised in `ingest_ncaaf_availability_report`. | `ncaaf_raw_availability_runtime.py` | — | No |
| `NCAAF_AVAILABILITY_NOT_PREGAME` | Availability evidence must be timestamped strictly before kickoff. | `ncaaf_availability_aggregation.py` | `ncaaf_official_availability.py` | No |
| `NCAAF_AVAILABILITY_PAYLOAD_INVALID` | No inline message; terminal code raised in `build_qb_evidence`. | `ncaaf_availability_aggregation.py` | — | No |
| `NCAAF_AVAILABILITY_PHASE_INVALID` | No inline message; terminal code raised in `normalize_player_availability`. | `ncaaf_official_availability.py` | — | No |
| `NCAAF_AVAILABILITY_PLAYERS_INVALID` | No inline message; terminal code raised in `ingest_ncaaf_availability_report`. | `ncaaf_raw_availability_runtime.py` | — | No |
| `NCAAF_AVAILABILITY_POLICY_UNVERIFIED` | No verified availability normalization policy is installed for {conference!r}. | `ncaaf_official_availability.py` | — | No |
| `NCAAF_AVAILABILITY_REQUEST_INCOMPLETE` | No inline message; terminal code raised in `ingest_ncaaf_availability_report`. | `ncaaf_raw_availability_runtime.py` | — | No |
| `NCAAF_AVAILABILITY_STATUS_UNRECOGNIZED` | Status {normalized_status!r} is not defined by {policy.policy_version} for {phase}. | `ncaaf_official_availability.py` | — | No |
| `NCAAF_AVAILABILITY_TIMESTAMP_INVALID` | No inline message; terminal code raised in `_aware`. | `ncaaf_official_availability.py` | — | No |
| `NCAAF_BACKUP_QB_DOWNGRADE_INVALID` | No inline message; terminal code raised in `validate_ncaaf_evidence`. | `ncaaf_trust.py` | — | No |
| `NCAAF_BACKUP_QB_DOWNGRADE_UNRESOLVED` | No inline message; terminal code raised in `validate_ncaaf_evidence`. | `ncaaf_trust.py` | `ncaaf_trust_tests.py` | No |
| `NCAAF_BUNDLE_NOT_CERTIFIED` | Resolved NCAAF artifact is not prospectively certified. | `ncaaf_fitted_provider.py` | — | No |
| `NCAAF_CALIBRATED_PROBABILITY_INVALID` | No inline message; terminal code raised in `apply_static_calibration`. | `ncaaf_static_calibrator.py` | — | No |
| `NCAAF_CALIBRATION_HEALTH_NOT_PASS` | No inline message; terminal code raised in `resolve_active_calibrator`. | `ncaaf_static_calibrator.py` | — | No |
| `NCAAF_CALIBRATION_METHOD_UNSUPPORTED` | No inline message; terminal code raised in `resolve_active_calibrator`. | `ncaaf_static_calibrator.py` | — | No |
| `NCAAF_CALIBRATION_SAMPLE_INSUFFICIENT` | No inline message; terminal code raised in `resolve_active_calibrator`. | `ncaaf_static_calibrator.py` | `ncaaf_three_way_trainer.py` | No |
| `NCAAF_CALIBRATOR_BIN_INVALID` | No inline message; terminal code raised in `apply_static_calibration`. | `ncaaf_static_calibrator.py` | — | No |
| `NCAAF_CALIBRATOR_EMPTY` | No inline message; terminal code raised in `apply_static_calibration`. | `ncaaf_static_calibrator.py` | `ncaaf_three_way_trainer.py` | No |
| `NCAAF_CALIBRATOR_MODEL_VERSION_MISMATCH` | No inline message; terminal code raised in `resolve_active_calibrator`. | `ncaaf_static_calibrator.py` | — | No |
| `NCAAF_CALIBRATOR_PAYLOAD_INVALID` | No inline message; terminal code raised in `apply_static_calibration`. | `ncaaf_static_calibrator.py` | — | No |
| `NCAAF_CALIBRATOR_REGISTRY_INVALID_RESPONSE` | No inline message; terminal code raised in `resolve_active_calibrator`. | `ncaaf_static_calibrator.py` | — | No |
| `NCAAF_CALIBRATOR_REGISTRY_UNAVAILABLE` | No inline message; terminal code raised in `resolve_active_calibrator`. | `ncaaf_static_calibrator.py` | — | No |
| `NCAAF_CANDIDATE_ARTIFACT_PERSIST_FAILED` | No inline message; terminal code raised in `train_and_persist_candidate`. | `ncaaf_candidate_training_runner.py` | — | No |
| `NCAAF_CANDIDATE_TRAINING_FAILED` | No inline message; terminal code raised in `run_ncaaf_model_maintenance`. | `v17/ncaaf_model_maintenance.py` | — | No |
| `NCAAF_CANONICAL_EVENT_AMBIGUOUS` | No inline message; terminal code raised in `resolve_ncaaf_current_event_identity`. | `v17/ncaaf_event_identity.py` | — | No |
| `NCAAF_CANONICAL_EVENT_NOT_FOUND` | No inline message; terminal code raised in `resolve_ncaaf_current_event_identity`. | `v17/ncaaf_event_identity.py` | — | No |
| `NCAAF_CERTIFIED_CALIBRATOR_NOT_FOUND` | No inline message; terminal code raised in `resolve_active_calibrator`. | `ncaaf_static_calibrator.py` | — | No |
| `NCAAF_CERTIFIED_DYNAMIC_BOUND_NOT_AVAILABLE` | No inline message; terminal code raised in `score_current_event_snapshot`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND` | No active certified NCAAF fitted-model artifact exists for this feature schema. | `ncaaf_fitted_provider.py` | `ncaaf_event_feature_handoff.py` | No |
| `NCAAF_CFBD_COUNT_NEGATIVE` | No inline message; terminal code raised in `build_read_only_sql`. | `v17/ncaaf_cfbd_exact_sample_sql.py` | — | No |
| `NCAAF_CFBD_RECEIPT_SCOPE_INVALID` | No inline message; terminal code raised in `build_read_only_sql`. | `v17/ncaaf_cfbd_exact_sample_sql.py` | — | No |
| `NCAAF_CFBD_SAMPLE_COUNT_MISMATCH` | No inline message; terminal code raised in `build_read_only_sql`. | `v17/ncaaf_cfbd_exact_sample_sql.py` | — | No |
| `NCAAF_CFBD_SAMPLE_DUPLICATE_KEY` | No inline message; terminal code raised in `build_read_only_sql`. | `v17/ncaaf_cfbd_exact_sample_sql.py` | — | No |
| `NCAAF_CFBD_SAMPLE_HASH_MISMATCH` | No inline message; terminal code raised in `build_read_only_sql`. | `v17/ncaaf_cfbd_exact_sample_sql.py` | — | No |
| `NCAAF_CFBD_SAMPLE_ROW_INVALID` | No inline message; terminal code raised in `build_read_only_sql`. | `v17/ncaaf_cfbd_exact_sample_sql.py` | — | No |
| `NCAAF_CFBD_SAMPLE_SIZE_INVALID` | No inline message; terminal code raised in `build_read_only_sql`. | `v17/ncaaf_cfbd_exact_sample_sql.py` | — | No |
| `NCAAF_CFBD_SOURCE_HASH_INVALID` | No inline message; terminal code raised in `build_read_only_sql`. | `v17/ncaaf_cfbd_exact_sample_sql.py` | — | No |
| `NCAAF_CFBD_STAT_INVALID` | No inline message; terminal code raised in `build_read_only_sql`. | `v17/ncaaf_cfbd_exact_sample_sql.py` | — | No |
| `NCAAF_CLV_PRIMARY_THRESHOLD_NOT_MET` | No inline message; terminal code raised in `assess_ncaaf_trust`. | `ncaaf_trust.py` | — | No |
| `NCAAF_CLV_RATE_UNAVAILABLE` | No inline message; terminal code raised in `assess_ncaaf_trust`. | `ncaaf_trust.py` | — | No |
| `NCAAF_COMPLETE_TRAINING_ROWS_INSUFFICIENT` | No inline message; terminal code raised in `load_training_rows`. | `ncaaf_candidate_training_runner.py` | — | No |
| `NCAAF_CROSSWALK_CANDIDATE_STAT_CONFLICT` | No inline message; terminal code raised in `reconcile`. | `v17/ncaaf_sdv_espn_crosswalk.py` | — | No |
| `NCAAF_CROSSWALK_FULL_COHORT_EMPTY_OR_COUNT_MISMATCH` | No inline message; terminal code raised in `audit_release`. | `v17/ncaaf_sdv_espn_crosswalk.py` | — | No |
| `NCAAF_CROSSWALK_FULL_COHORT_HASH_MISMATCH` | No inline message; terminal code raised in `audit_release`. | `v17/ncaaf_sdv_espn_crosswalk.py` | — | No |
| `NCAAF_CROSSWALK_NCAA_FILE_INVALID` | No inline message; terminal code raised in `audit_release`. | `v17/ncaaf_sdv_espn_crosswalk.py` | — | No |
| `NCAAF_CROSSWALK_PARQUET_READER_MISSING` | No inline message; terminal code raised in `audit_release`. | `v17/ncaaf_sdv_espn_crosswalk.py` | — | No |
| `NCAAF_CROSSWALK_ROW_LIMIT` | No inline message; terminal code raised in `_index`. | `v17/ncaaf_sdv_espn_crosswalk.py` | — | No |
| `NCAAF_CROSSWALK_SOURCE_HASH_INVALID` | No inline message; terminal code raised in `reconcile`. | `v17/ncaaf_sdv_espn_crosswalk.py` | — | No |
| `NCAAF_CURRENT_EVENT_CONTEXT_CHANGED` | No inline message; terminal code raised in `_validate_refreshed_context`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_CURRENT_EVENT_CONTEXT_INCOMPLETE` | No inline message; terminal code raised in `build_current_event_feature_snapshot`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_CURRENT_EVENT_CONTEXT_REFRESH_INVALID` | No inline message; terminal code raised in `_validate_refreshed_context`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_CURRENT_EVENT_CONTEXT_REFRESH_REQUIRED` | No inline message; terminal code raised in `_validate_refreshed_context`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_CURRENT_EVENT_CONTEXT_STALE` | No inline message; terminal code raised in `_validate_refreshed_context`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_CURRENT_SOURCE_RECEIPT_AMBIGUOUS` | No inline message; terminal code raised in `build_current_event_feature_snapshot`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_CURRENT_SOURCE_RECEIPT_INVALID` | No inline message; terminal code raised in `_receipt`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_CURRENT_SOURCE_RECEIPT_MISSING` | No inline message; terminal code raised in `_receipt`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_CURRENT_SOURCE_RECEIPT_UNEXPECTED` | No inline message; terminal code raised in `build_current_event_feature_snapshot`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_ESPN_ARCHIVE_EMPTY` | No inline message; terminal code raised in `_download_espn_release`. | `v17/ncaaf_sdv_espn_crosswalk.py` | — | No |
| `NCAAF_ESPN_RELEASE_TOO_LARGE` | No inline message; terminal code raised in `_download_espn_release`. | `v17/ncaaf_sdv_espn_crosswalk.py` | — | No |
| `NCAAF_ESPN_SEASON_INVALID` | No inline message; terminal code raised in `espn_release_url`. | `v17/ncaaf_sdv_espn_crosswalk.py` | — | No |
| `NCAAF_ESPN_SOURCE_FETCH_FAILED` | No inline message; terminal code raised in `_download_espn_release`. | `v17/ncaaf_sdv_espn_crosswalk.py` | — | No |
| `NCAAF_ESPN_UNAPPROVED_REDIRECT` | No inline message; terminal code raised in `_download_espn_release`. | `v17/ncaaf_sdv_espn_crosswalk.py` | — | No |
| `NCAAF_EVENT_FEATURE_COHORT_MISMATCH` | No inline message; terminal code raised in `score_current_event_snapshot`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_EVENT_FEATURE_SNAPSHOT_IMMUTABLE_INSERT_FAILED` | No inline message; terminal code raised in `persist_current_event_feature_snapshot`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_EVENT_FEATURE_SNAPSHOT_INVALID` | No inline message; terminal code raised in `persist_current_event_feature_snapshot`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_EVENT_FEATURE_SNAPSHOT_NOT_FOUND` | No inline message; terminal code raised in `score_current_event_snapshot`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_EVENT_FEATURE_SNAPSHOT_READ_FAILED` | No inline message; terminal code raised in `score_current_event_snapshot`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_EVENT_FEATURE_TEMPORAL_LEAKAGE` | No inline message; terminal code raised in `_receipt`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_EVENT_IDENTITY_INCOMPLETE` | No inline message; terminal code raised in `build_current_event_feature_snapshot`. | `ncaaf_event_feature_handoff.py` | `ncaaf_evidence_ingestion.py` | No |
| `NCAAF_EVENT_IDENTITY_MISMATCH` | No inline message; terminal code raised in `logistic_adapter`. | `ncaaf_logistic_adapter.py` | — | No |
| `NCAAF_EVENT_NOT_PREGAME` | No inline message; terminal code raised in `build_current_event_feature_snapshot`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_EVENT_START_INVALID` | No inline message; terminal code raised in `_compile_feature_row`. | `ncaaf_feature_compiler.py` | — | No |
| `NCAAF_EVENT_TIMESTAMP_INVALID` | No inline message; terminal code raised in `_aware`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_EVENT_TIME_INVALID` | No inline message; terminal code raised in `_aware`. | `v17/ncaaf_event_identity.py` | — | No |
| `NCAAF_EVIDENCE_EXECUTION_FLAG_INVALID` | No inline message; terminal code raised in `persist_normalized_evidence`. | `ncaaf_evidence_ingestion.py` | — | No |
| `NCAAF_EVIDENCE_KIND_NOT_ALLOWED_BY_INGESTION` | No inline message; terminal code raised in `persist_normalized_evidence`. | `ncaaf_evidence_ingestion.py` | — | No |
| `NCAAF_EVIDENCE_PAYLOAD_INVALID` | No inline message; terminal code raised in `_value`. | `ncaaf_feature_builder.py` | — | No |
| `NCAAF_EVIDENCE_PERSIST_FAILED` | No inline message; terminal code raised in `persist_normalized_evidence`. | `ncaaf_evidence_ingestion.py` | — | No |
| `NCAAF_EVIDENCE_PROVENANCE_INCOMPLETE` | No inline message; terminal code raised in `persist_normalized_evidence`. | `ncaaf_evidence_ingestion.py` | — | No |
| `NCAAF_EVIDENCE_TIMESTAMP_INVALID` | No inline message; terminal code raised in `_parse_ts`. | `ncaaf_feature_builder.py` | — | No |
| `NCAAF_EVIDENCE_VALUE_INVALID` | No inline message; terminal code raised in `_number`. | `ncaaf_feature_builder.py` | — | No |
| `NCAAF_FEATURE_COMPILATION_FAILED` | No inline message; terminal code raised in `run_ncaaf_model_maintenance`. | `v17/ncaaf_model_maintenance.py` | — | No |
| `NCAAF_FEATURE_IDENTITY_INCOMPLETE` | No inline message; terminal code raised in `_compile_feature_row`. | `ncaaf_feature_compiler.py` | — | No |
| `NCAAF_FEATURE_SCHEMA_MISMATCH` | Resolved NCAAF artifact does not match requested feature schema. | `ncaaf_fitted_provider.py` | `ncaaf_logistic_adapter.py` | No |
| `NCAAF_FEATURE_TEMPORAL_LEAKAGE` | No inline message; terminal code raised in `_compile_feature_row`. | `ncaaf_feature_compiler.py` | — | No |
| `NCAAF_FEATURE_TIMESTAMP_MISMATCH` | No inline message; terminal code raised in `logistic_adapter`. | `ncaaf_logistic_adapter.py` | — | No |
| `NCAAF_FEATURE_TRANSFORM_INPUT_INVALID` | No inline message; terminal code raised in `_num`. | `ncaaf_feature_transform.py` | — | No |
| `NCAAF_FEATURE_TRANSFORM_SCHEMA_DRIFT` | No inline message; terminal code raised in `model_features_from_snapshot`. | `ncaaf_feature_transform.py` | — | No |
| `NCAAF_FULL_SOURCE_DUPLICATE` | No inline message; terminal code raised in `audited_lines`. | `v17/ncaaf_cfbd_full_audit_log.py` | — | No |
| `NCAAF_FULL_SOURCE_HASH_MISMATCH` | No inline message; terminal code raised in `audited_lines`. | `v17/ncaaf_cfbd_full_audit_log.py` | — | No |
| `NCAAF_FULL_SOURCE_MANIFEST_INVALID` | No inline message; terminal code raised in `audited_lines`. | `v17/ncaaf_cfbd_full_audit_log.py` | — | No |
| `NCAAF_FULL_SOURCE_ORDER_INVALID` | No inline message; terminal code raised in `audited_lines`. | `v17/ncaaf_cfbd_full_audit_log.py` | — | No |
| `NCAAF_FULL_SOURCE_PROVENANCE_MISSING` | No inline message; terminal code raised in `audited_lines`. | `v17/ncaaf_cfbd_full_audit_log.py` | — | No |
| `NCAAF_FULL_SOURCE_ROW_INVALID` | No inline message; terminal code raised in `audited_lines`. | `v17/ncaaf_cfbd_full_audit_log.py` | — | No |
| `NCAAF_FULL_SOURCE_ROW_SCHEMA_INVALID` | No inline message; terminal code raised in `audited_lines`. | `v17/ncaaf_cfbd_full_audit_log.py` | — | No |
| `NCAAF_HISTORY_HYDRATION_FAILED` | No inline message; terminal code raised in `run_ncaaf_model_maintenance`. | `v17/ncaaf_model_maintenance.py` | — | No |
| `NCAAF_HOME_AWAY_INVALID` | No inline message; terminal code raised in `validate_ncaaf_evidence`. | `ncaaf_trust.py` | — | No |
| `NCAAF_MAINTENANCE_RANGE_EMPTY` | No inline message; terminal code raised in `run_ncaaf_model_maintenance`. | `v17/ncaaf_model_maintenance.py` | — | No |
| `NCAAF_MARKET_FEATURES_PROHIBITED` | No inline message; terminal code raised in `build_current_event_feature_snapshot`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_MARKET_PRICE_STALE` | No inline message; terminal code raised in `validate_ncaaf_evidence`. | `ncaaf_trust.py` | `ncaaf_trust_tests.py` | No |
| `NCAAF_MARKET_ROLE_INVALID` | No inline message; terminal code raised in `validate_ncaaf_evidence`. | `ncaaf_trust.py` | — | No |
| `NCAAF_MARKET_TIMESTAMP_INVALID` | No inline message; terminal code raised in `validate_ncaaf_evidence`. | `ncaaf_trust.py` | — | No |
| `NCAAF_MARKET_TIMESTAMP_IN_FUTURE` | No inline message; terminal code raised in `validate_ncaaf_evidence`. | `ncaaf_trust.py` | — | No |
| `NCAAF_MODEL_ADAPTER_INVALID_OUTPUT` | NCAAF model-family adapter must return RawNCAAFWinProbability. | `ncaaf_event_feature_handoff.py` | `ncaaf_fitted_provider.py` | No |
| `NCAAF_MODEL_ARTIFACT_FORMAT_UNSUPPORTED` | No inline message; terminal code raised in `logistic_adapter`. | `ncaaf_logistic_adapter.py` | — | No |
| `NCAAF_MODEL_ARTIFACT_METADATA_INVALID` | Certified NCAAF artifact metadata is incomplete or malformed. | `ncaaf_fitted_provider.py` | — | No |
| `NCAAF_MODEL_ARTIFACT_PAYLOAD_INVALID` | Certified NCAAF artifact payload or validation metrics are invalid. | `ncaaf_logistic_adapter.py` | `ncaaf_fitted_provider.py` | No |
| `NCAAF_MODEL_FAMILY_ADAPTER_UNAVAILABLE` | No reviewed runtime adapter is registered for model family {artifact.model_family!r}. | `ncaaf_event_feature_handoff.py` | `ncaaf_fitted_provider.py` | No |
| `NCAAF_MODEL_FEATURE_EVIDENCE_INCOMPLETE` | No inline message; terminal code raised in `_compile_feature_row`. | `ncaaf_feature_compiler.py` | — | No |
| `NCAAF_MODEL_FEATURE_PAYLOAD_INVALID` | No inline message; terminal code raised in `_compile_feature_row`. | `ncaaf_feature_compiler.py` | — | No |
| `NCAAF_MODEL_OUTPUT_INVALID` | NCAAF model probabilities must be finite and strictly inside (0,1). | `ncaaf_event_feature_handoff.py` | `ncaaf_fitted_provider.py`, `ncaaf_logistic_adapter.py` | No |
| `NCAAF_MODEL_OUTPUT_NOT_NORMALIZED` | NCAAF home/away probabilities must sum to 1. | `ncaaf_event_feature_handoff.py` | `ncaaf_fitted_provider.py` | No |
| `NCAAF_MODEL_REGISTRY_INVALID_RESPONSE` | Governed NCAAF model registry returned an invalid response. | `ncaaf_fitted_provider.py` | — | No |
| `NCAAF_MODEL_REGISTRY_REJECTED` | Governed NCAAF model registry rejected the route. | `ncaaf_fitted_provider.py` | — | No |
| `NCAAF_MODEL_REGISTRY_UNAVAILABLE` | Could not read the governed NCAAF model registry. | `ncaaf_fitted_provider.py` | — | No |
| `NCAAF_MODEL_TIMESTAMP_INVALID` | No inline message; terminal code raised in `validate_ncaaf_evidence`. | `ncaaf_trust.py` | — | No |
| `NCAAF_MODEL_TIMESTAMP_IN_FUTURE` | No inline message; terminal code raised in `validate_ncaaf_evidence`. | `ncaaf_trust.py` | — | No |
| `NCAAF_NEUTRAL_SITE_CONFLICT` | No inline message; terminal code raised in `validate_ncaaf_evidence`. | `ncaaf_trust.py` | — | No |
| `NCAAF_NO_SETTLED_TRAINING_GAMES_MATERIALIZED` | No inline message; terminal code raised in `materialize_training_games`. | `ncaaf_training_materializer.py` | — | No |
| `NCAAF_NO_VIG_PRICE_MISMATCH` | No inline message; terminal code raised in `validate_ncaaf_evidence`. | `ncaaf_trust.py` | — | No |
| `NCAAF_NO_VIG_PROBABILITY_INVALID` | No inline message; terminal code raised in `validate_ncaaf_evidence`. | `ncaaf_trust.py` | — | No |
| `NCAAF_POSITIVE_ROI_NOT_MET` | No inline message; terminal code raised in `assess_ncaaf_trust`. | `ncaaf_trust.py` | — | No |
| `NCAAF_PREGAME_EVIDENCE_INCOMPLETE` | No inline message; terminal code raised in `assess_pregame_evidence`. | `ncaaf_feature_builder.py` | `ncaaf_evidence_readiness.py` | No |
| `NCAAF_PREGAME_EVIDENCE_ROWS_REJECTED` | No inline message; terminal code raised in `assess_pregame_evidence`. | `ncaaf_evidence_readiness.py` | — | No |
| `NCAAF_PROP_80P_COVERAGE_OUT_OF_TOLERANCE` | No inline message; terminal code raised in `fit_candidate`. | `v17/ncaaf_prop_challenger.py` | — | No |
| `NCAAF_PROP_90P_COVERAGE_OUT_OF_TOLERANCE` | No inline message; terminal code raised in `fit_candidate`. | `v17/ncaaf_prop_challenger.py` | — | No |
| `NCAAF_PROP_ARTIFACT_GOVERNANCE_INVALID` | No inline message; terminal code raised in `_assert_inert_candidate`. | `v17/ncaaf_prop_challenger_runner.py` | — | No |
| `NCAAF_PROP_ARTIFACT_NOT_CANDIDATE` | No inline message; terminal code raised in `_assert_inert_candidate`. | `v17/ncaaf_prop_challenger_runner.py` | — | No |
| `NCAAF_PROP_CAL_HOLDOUT_CHRONOLOGY_INVALID` | No inline message; terminal code raised in `_split`. | `v17/ncaaf_prop_challenger.py` | — | No |
| `NCAAF_PROP_CAL_ROWS_BELOW_MINIMUM` | No inline message; terminal code raised in `_split`. | `v17/ncaaf_prop_challenger.py` | — | No |
| `NCAAF_PROP_CANDIDATE_PERSIST_FAILED` | No inline message; terminal code raised in `persist_inert_candidates`. | `v17/ncaaf_prop_challenger_runner.py` | — | No |
| `NCAAF_PROP_DUPLICATE_PLAYER_STAT` | No inline message; terminal code raised in `_emit`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_FAILS_NAIVE_MAE_GATE` | No inline message; terminal code raised in `fit_candidate`. | `v17/ncaaf_prop_challenger.py` | — | No |
| `NCAAF_PROP_FEATURE_ROWS_EMPTY` | No inline message; terminal code raised in `build_feature_rows`. | `v17/ncaaf_prop_challenger.py` | — | No |
| `NCAAF_PROP_GAMES_CORPUS_EMPTY` | No inline message; terminal code raised in `run_candidate_replay`. | `v17/ncaaf_prop_challenger_runner.py` | — | No |
| `NCAAF_PROP_HISTORY_CACHE_READ_FAILED` | No inline message; terminal code raised in `run_ncaaf_prop_history_maintenance`. | `v17/ncaaf_prop_history_maintenance.py` | — | No |
| `NCAAF_PROP_HISTORY_CACHE_READ_INVALID` | No inline message; terminal code raised in `_available_snapshot_keys`. | `v17/ncaaf_prop_history_maintenance.py` | — | No |
| `NCAAF_PROP_HISTORY_PERSISTENCE_FAILED` | No inline message; terminal code raised in `run_ncaaf_prop_history_maintenance`. | `v17/ncaaf_prop_history_maintenance.py` | — | No |
| `NCAAF_PROP_HISTORY_RANGE_EMPTY` | No inline message; terminal code raised in `run_ncaaf_prop_history_maintenance`. | `v17/ncaaf_prop_history_maintenance.py` | — | No |
| `NCAAF_PROP_HOLDOUT_ROWS_BELOW_MINIMUM` | No inline message; terminal code raised in `_split`. | `v17/ncaaf_prop_challenger.py` | — | No |
| `NCAAF_PROP_NONFINITE_MATRIX` | No inline message; terminal code raised in `_matrix`. | `v17/ncaaf_prop_challenger.py` | — | No |
| `NCAAF_PROP_OUTCOME_TIMESTAMP_PREMATURE` | No inline message; terminal code raised in `_emit`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_PACKAGE_GOVERNANCE_INVALID` | No inline message; terminal code raised in `persist_inert_candidates`. | `v17/ncaaf_prop_challenger_runner.py` | — | No |
| `NCAAF_PROP_PASSING_C_ATT_INVALID` | No inline message; terminal code raised in `_parse_completions_attempts`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_PLAYER_CORPUS_EMPTY` | No inline message; terminal code raised in `run_candidate_replay`. | `v17/ncaaf_prop_challenger_runner.py` | — | No |
| `NCAAF_PROP_PLAYER_GAME_ATHLETES_INVALID` | No inline message; terminal code raised in `normalize_player_game`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_PLAYER_GAME_CATEGORIES_INVALID` | No inline message; terminal code raised in `normalize_player_game`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_PLAYER_GAME_IDENTITY_INVALID` | No inline message; terminal code raised in `normalize_player_game`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_PLAYER_GAME_REVISION_CONFLICT` | No inline message; terminal code raised in `normalize_player_stats_corpus`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_PLAYER_GAME_ROW_INVALID` | No inline message; terminal code raised in `normalize_player_stats_corpus`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_PLAYER_GAME_TEAMS_INVALID` | No inline message; terminal code raised in `normalize_player_game`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_PLAYER_GAME_TEAM_IDENTITY_INVALID` | No inline message; terminal code raised in `normalize_player_game`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_PLAYER_GAME_TEAM_MISMATCH` | No inline message; terminal code raised in `normalize_player_game`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_PLAYER_GAME_TYPES_INVALID` | No inline message; terminal code raised in `normalize_player_game`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_PLAYER_IDENTITY_INVALID` | No inline message; terminal code raised in `normalize_player_game`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_PLAYER_STATS_EMPTY` | No inline message; terminal code raised in `profile_cfbd_player_stats`. | `ncaaf_cfbd_hydrator.py` | `v17/ncaaf_prop_history_maintenance.py` | No |
| `NCAAF_PROP_PLAYER_STATS_NOT_NORMALIZATION_READY` | No inline message; terminal code raised in `run_ncaaf_prop_history_maintenance`. | `v17/ncaaf_prop_history_maintenance.py` | — | No |
| `NCAAF_PROP_PLAYER_STATS_SCHEMA_INVALID` | No inline message; terminal code raised in `profile_cfbd_player_stats`. | `ncaaf_cfbd_hydrator.py` | — | No |
| `NCAAF_PROP_RESEARCH_FLAG_INVALID` | No inline message; terminal code raised in `_assert_inert_candidate`. | `v17/ncaaf_prop_challenger_runner.py` | — | No |
| `NCAAF_PROP_ROUTE_UNSUPPORTED` | No inline message; terminal code raised in `build_feature_rows`. | `v17/ncaaf_prop_challenger.py` | — | No |
| `NCAAF_PROP_SCHEDULE_GAME_UNRESOLVED` | No inline message; terminal code raised in `normalize_player_game`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_SCHEDULE_IDENTITY_CONFLICT` | No inline message; terminal code raised in `build_schedule_index`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_SCHEDULE_IDENTITY_INVALID` | No inline message; terminal code raised in `build_schedule_index`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_SCHEDULE_TEAMS_INVALID` | No inline message; terminal code raised in `build_schedule_index`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_SCHEDULE_TIMESTAMP_INVALID` | No inline message; terminal code raised in `build_schedule_index`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_SCHEDULE_UNRESOLVED` | No inline message; terminal code raised in `build_feature_rows`. | `v17/ncaaf_prop_challenger.py` | — | No |
| `NCAAF_PROP_SOURCE_ENDPOINT_INVALID` | No inline message; terminal code raised in `normalize_player_game`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_SOURCE_GOVERNANCE_INVALID` | No inline message; terminal code raised in `_snapshot_from_row`. | `v17/ncaaf_prop_challenger_runner.py` | — | No |
| `NCAAF_PROP_SOURCE_READ_FAILED` | No inline message; terminal code raised in `load_source_snapshots`. | `v17/ncaaf_prop_challenger_runner.py` | — | No |
| `NCAAF_PROP_SOURCE_READ_INVALID` | No inline message; terminal code raised in `load_source_snapshots`. | `v17/ncaaf_prop_challenger_runner.py` | — | No |
| `NCAAF_PROP_SOURCE_ROWS_INVALID` | No inline message; terminal code raised in `_snapshot_from_row`. | `v17/ncaaf_prop_challenger_runner.py` | — | No |
| `NCAAF_PROP_SOURCE_TIMESTAMP_INVALID` | No inline message; terminal code raised in `_snapshot_retrieved_at`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_SPORT_IDENTITY_INVALID` | No inline message; terminal code raised in `_assert_inert_candidate`. | `v17/ncaaf_prop_challenger_runner.py` | — | No |
| `NCAAF_PROP_STAT_COUNT_INVALID` | No inline message; terminal code raised in `_parse_nonnegative_integer_stat`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_STAT_TYPE_UNSUPPORTED` | No inline message; terminal code raised in `_requested_types`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_STAT_VALUE_INVALID` | No inline message; terminal code raised in `_parse_integer_stat`. | `ncaaf_prop_historical_adapter.py` | — | No |
| `NCAAF_PROP_TARGET_INVALID` | No inline message; terminal code raised in `build_feature_rows`. | `v17/ncaaf_prop_challenger.py` | — | No |
| `NCAAF_PROP_TRAIN_CAL_CHRONOLOGY_INVALID` | No inline message; terminal code raised in `_split`. | `v17/ncaaf_prop_challenger.py` | — | No |
| `NCAAF_PROP_TRAIN_ROWS_BELOW_MINIMUM` | No inline message; terminal code raised in `_split`. | `v17/ncaaf_prop_challenger.py` | — | No |
| `NCAAF_PROP_ZERO_OUTCOME_COVERAGE_UNPROVEN` | No inline message; terminal code raised in `build_feature_rows`. | `v17/ncaaf_prop_challenger.py` | — | No |
| `NCAAF_PROVIDER_IDENTITY_MISMATCH` | Resolved artifact does not belong to WOW_NCAAF_FITTED_MODEL_V1. | `ncaaf_fitted_provider.py` | — | No |
| `NCAAF_QB_STATUS_UNCONFIRMED` | No inline message; terminal code raised in `validate_ncaaf_evidence`. | `ncaaf_trust.py` | `ncaaf_trust_tests.py` | No |
| `NCAAF_RAW_PROBABILITY_INVALID` | No inline message; terminal code raised in `apply_static_calibration`. | `ncaaf_static_calibrator.py` | — | No |
| `NCAAF_RECONSTRUCTED_HISTORY_NOT_ARCHIVED_PREGAME` | No inline message; terminal code raised in `module scope`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPEATING_FAILURE_TAG_ACTIVE` | No inline message; terminal code raised in `assess_ncaaf_trust`. | `ncaaf_trust.py` | — | No |
| `NCAAF_REPLAY_ARTIFACT_CHECKSUM_MISMATCH` | No inline message; terminal code raised in `_candidate_preflight`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_ARTIFACT_SCHEMA_DRIFT` | No inline message; terminal code raised in `_candidate_preflight`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_CALIBRATOR_CONTRACT_UNSUPPORTED` | No inline message; terminal code raised in `_candidate_preflight`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_CALIBRATOR_MISMATCH` | No inline message; terminal code raised in `audit_candidate`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_CANDIDATE_CONTRACT_UNSUPPORTED` | No inline message; terminal code raised in `_candidate_preflight`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_CANDIDATE_NOT_FOUND` | No inline message; terminal code raised in `audit_from_db`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_DATASET_HASH_MISMATCH` | No inline message; terminal code raised in `audit_candidate`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_DATA_READ_FAILED` | No inline message; terminal code raised in `audit_from_db`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_EVENT_ID_MISSING` | No inline message; terminal code raised in `frozen_latest_rows`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_FEATURE_SET_INVALID` | No inline message; terminal code raised in `audit_candidate`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_FITTED_ARTIFACT_MISMATCH` | No inline message; terminal code raised in `audit_candidate`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_FITTED_PACKAGE_MISSING` | No inline message; terminal code raised in `_candidate_preflight`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_FROZEN_CORPUS_COUNT_MISMATCH` | No inline message; terminal code raised in `audit_candidate`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_FROZEN_ROW_FORK` | No inline message; terminal code raised in `frozen_latest_rows`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_INPUT_SHAPE_INVALID` | No inline message; terminal code raised in `audit_candidate`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_NUMERIC_INVALID` | No inline message; terminal code raised in `_numeric`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_OUTCOME_INVALID` | No inline message; terminal code raised in `audit_candidate`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_RESEARCH_METRICS_MISMATCH` | No inline message; terminal code raised in `audit_candidate`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_RESEARCH_SOURCE_FLAGS_INVALID` | No inline message; terminal code raised in `_candidate_preflight`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_ROW_EXECUTION_OR_MARKET_INVALID` | No inline message; terminal code raised in `audit_candidate`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_SCORER_EXECUTION_FAILED` | No inline message; terminal code raised in `audit_candidate`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_SOURCE_MANIFEST_HASH_MISMATCH` | No inline message; terminal code raised in `audit_candidate`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_SOURCE_MANIFEST_INVALID` | No inline message; terminal code raised in `audit_candidate`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_SOURCE_REGIME_CHANGED` | No inline message; terminal code raised in `audit_candidate`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_TEMPORAL_ORDER_INVALID` | No inline message; terminal code raised in `audit_candidate`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_TIMESTAMP_INVALID` | No inline message; terminal code raised in `_iso`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_REPLAY_VERSION_IDENTITY_INVALID` | No inline message; terminal code raised in `_candidate_preflight`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_RESULT_FORM_CANDIDATE_TRAINING_FAILED` | No inline message; terminal code raised in `run_ncaaf_model_maintenance`. | `v17/ncaaf_model_maintenance.py` | — | No |
| `NCAAF_RESULT_FORM_CODE_SHA_REQUIRED` | No inline message; terminal code raised in `train_and_persist`. | `v17/ncaaf_result_form_candidate.py` | — | No |
| `NCAAF_RESULT_FORM_DETERMINISTIC_REPLAY_PASS_SOURCE_HOLD` | No inline message; terminal code raised in `audit_candidate`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_RESULT_FORM_EVENT_TIME_MISSING` | No inline message; terminal code raised in `_dt`. | `v17/ncaaf_result_form_candidate.py` | — | No |
| `NCAAF_RESULT_FORM_GAMES_EMPTY` | No inline message; terminal code raised in `_load_games`. | `v17/ncaaf_result_form_candidate.py` | — | No |
| `NCAAF_RESULT_FORM_REPLAY_FAILED_OR_BLOCKED` | No inline message; terminal code raised in `_fail`. | `v17/ncaaf_result_form_replay_audit.py` | — | No |
| `NCAAF_RESULT_FORM_SAMPLE_INSUFFICIENT` | No inline message; terminal code raised in `build_training_rows`. | `v17/ncaaf_result_form_candidate.py` | — | No |
| `NCAAF_ROI_UNAVAILABLE` | No inline message; terminal code raised in `assess_ncaaf_trust`. | `ncaaf_trust.py` | — | No |
| `NCAAF_ROLE_NOT_PREGAME` | No inline message; terminal code raised in `build_qb_evidence`. | `ncaaf_availability_aggregation.py` | — | No |
| `NCAAF_ROLE_SCOPE_INVALID` | No inline message; terminal code raised in `build_qb_evidence`. | `ncaaf_availability_aggregation.py` | — | No |
| `NCAAF_ROLE_TIMESTAMP_INVALID` | No inline message; terminal code raised in `_ts`. | `ncaaf_availability_aggregation.py` | — | No |
| `NCAAF_SDV_ACQUISITION_FAILED` | No inline message; terminal code raised in `acquire_release`. | `v17/ncaaf_sdv_player_history_acquisition.py` | — | No |
| `NCAAF_SDV_ARCHIVE_EMPTY` | No inline message; terminal code raised in `acquire_release`. | `v17/ncaaf_sdv_player_history_acquisition.py` | — | No |
| `NCAAF_SDV_ARCHIVE_TOO_LARGE` | No inline message; terminal code raised in `acquire_release`. | `v17/ncaaf_sdv_player_history_acquisition.py` | — | No |
| `NCAAF_SDV_CAPTURE_TIMESTAMP_INVALID` | No inline message; terminal code raised in `profile_player_rows`. | `v17/ncaaf_sdv_player_history_acquisition.py` | — | No |
| `NCAAF_SDV_COUNT_NEGATIVE` | No inline message; terminal code raised in `_stat`. | `v17/ncaaf_sdv_player_history_acquisition.py` | — | No |
| `NCAAF_SDV_HASH_INVALID` | No inline message; terminal code raised in `profile_player_rows`. | `v17/ncaaf_sdv_player_history_acquisition.py` | — | No |
| `NCAAF_SDV_NO_USABLE_PLAYER_HISTORY` | No inline message; terminal code raised in `profile_player_rows`. | `v17/ncaaf_sdv_player_history_acquisition.py` | — | No |
| `NCAAF_SDV_PARQUET_READER_MISSING` | No inline message; terminal code raised in `acquire_release`. | `v17/ncaaf_sdv_player_history_acquisition.py` | — | No |
| `NCAAF_SDV_ROW_INVALID` | No inline message; terminal code raised in `profile_player_rows`. | `v17/ncaaf_sdv_player_history_acquisition.py` | — | No |
| `NCAAF_SDV_ROW_LIMIT_EXCEEDED` | No inline message; terminal code raised in `profile_player_rows`. | `v17/ncaaf_sdv_player_history_acquisition.py` | — | No |
| `NCAAF_SDV_ROW_SEASON_INVALID` | No inline message; terminal code raised in `profile_player_rows`. | `v17/ncaaf_sdv_player_history_acquisition.py` | — | No |
| `NCAAF_SDV_ROW_STATS_EMPTY_OR_INVALID` | No inline message; terminal code raised in `profile_player_rows`. | `v17/ncaaf_sdv_player_history_acquisition.py` | — | No |
| `NCAAF_SDV_SCHEMA_DRIFT` | No inline message; terminal code raised in `profile_player_rows`. | `v17/ncaaf_sdv_player_history_acquisition.py` | — | No |
| `NCAAF_SDV_SEASON_INVALID` | No inline message; terminal code raised in `release_url`. | `v17/ncaaf_sdv_player_history_acquisition.py` | — | No |
| `NCAAF_SDV_SEASON_MISMATCH` | No inline message; terminal code raised in `profile_player_rows`. | `v17/ncaaf_sdv_player_history_acquisition.py` | — | No |
| `NCAAF_SDV_SOURCE_IDENTITY_OR_CATEGORY_MISSING` | No inline message; terminal code raised in `profile_player_rows`. | `v17/ncaaf_sdv_player_history_acquisition.py` | — | No |
| `NCAAF_SDV_STAT_INVALID` | No inline message; terminal code raised in `_stat`. | `v17/ncaaf_sdv_player_history_acquisition.py` | — | No |
| `NCAAF_SDV_UNAPPROVED_REDIRECT` | No inline message; terminal code raised in `acquire_release`. | `v17/ncaaf_sdv_player_history_acquisition.py` | — | No |
| `NCAAF_SKILL_AGGREGATION_INVALID` | No inline message; terminal code raised in `build_skill_availability`. | `ncaaf_availability_aggregation.py` | — | No |
| `NCAAF_SKILL_PROBABILITY_INVALID` | No inline message; terminal code raised in `build_skill_availability`. | `ncaaf_availability_aggregation.py` | — | No |
| `NCAAF_SKILL_PROBABILITY_NOT_SOURCE_DEFINED` | No inline message; terminal code raised in `build_skill_availability`. | `ncaaf_availability_aggregation.py` | — | No |
| `NCAAF_SKILL_PROVIDER_MIXED` | No inline message; terminal code raised in `build_skill_availability`. | `ncaaf_availability_aggregation.py` | — | No |
| `NCAAF_SKILL_WEIGHTS_INVALID` | No inline message; terminal code raised in `build_skill_availability`. | `ncaaf_availability_aggregation.py` | — | No |
| `NCAAF_SOURCE_EMPTY` | No inline message; terminal code raised in `_snapshot`. | `ncaaf_cfbd_hydrator.py` | — | No |
| `NCAAF_SOURCE_MANIFEST_INVALID` | No inline message; terminal code raised in `_verify_snapshot_manifest`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_SOURCE_MANIFEST_MISMATCH` | No inline message; terminal code raised in `_verify_snapshot_manifest`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_SOURCE_SNAPSHOT_WRITE_ACK_INVALID` | No inline message; terminal code raised in `persist_source_snapshots`. | `ncaaf_cfbd_hydrator.py` | — | No |
| `NCAAF_STARTER_AVAILABILITY_NOT_PROVEN` | No inline message; terminal code raised in `_raw_for`. | `ncaaf_availability_aggregation.py` | — | No |
| `NCAAF_STARTER_IDENTITY_NOT_PROVEN` | No inline message; terminal code raised in `build_qb_evidence`. | `ncaaf_availability_aggregation.py` | — | No |
| `NCAAF_START_TIME_INVALID` | No inline message; terminal code raised in `validate_ncaaf_evidence`. | `ncaaf_trust.py` | — | No |
| `NCAAF_STATIC_CALIBRATION_FAILED` | No inline message; terminal code raised in `score_current_event_snapshot`. | `ncaaf_event_feature_handoff.py` | — | No |
| `NCAAF_TEAM_IDENTITY_MISMATCH` | No inline message; terminal code raised in `logistic_adapter`. | `ncaaf_logistic_adapter.py` | — | No |
| `NCAAF_THREE_WAY_PARTITION_INSUFFICIENT` | No inline message; terminal code raised in `train_calibrate_test`. | `ncaaf_three_way_trainer.py` | — | No |
| `NCAAF_THREE_WAY_SAMPLE_INSUFFICIENT` | No inline message; terminal code raised in `train_calibrate_test`. | `ncaaf_three_way_trainer.py` | — | No |
| `NCAAF_TRAINING_CLASS_DEGENERATE` | Both train and validation partitions must contain wins and losses. | `ncaaf_three_way_trainer.py` | `ncaaf_trainer.py` | No |
| `NCAAF_TRAINING_CODE_SHA_REQUIRED` | No inline message; terminal code raised in `train_and_persist_candidate`. | `ncaaf_candidate_training_runner.py` | — | No |
| `NCAAF_TRAINING_CODE_SHA_UNAVAILABLE` | No inline message; terminal code raised in `run_ncaaf_model_maintenance`. | `v17/ncaaf_model_maintenance.py` | — | No |
| `NCAAF_TRAINING_DATA_EMPTY` | No inline message; terminal code raised in `load_training_rows`. | `ncaaf_candidate_training_runner.py` | — | No |
| `NCAAF_TRAINING_DATA_INVALID` | No inline message; terminal code raised in `_paged`. | `ncaaf_candidate_training_runner.py` | — | No |
| `NCAAF_TRAINING_DATA_READ_FAILED` | No inline message; terminal code raised in `_paged`. | `ncaaf_candidate_training_runner.py` | — | No |
| `NCAAF_TRAINING_FEATURE_INVALID` | Required feature {name!r} is not numeric. | `ncaaf_trainer.py` | `ncaaf_three_way_trainer.py` | No |
| `NCAAF_TRAINING_FEATURE_MISSING` | Required feature {name!r} is missing. | `ncaaf_three_way_trainer.py` | `ncaaf_trainer.py` | No |
| `NCAAF_TRAINING_GAME_PERSISTENCE_COUNT_MISMATCH` | No inline message; terminal code raised in `materialize_training_games`. | `ncaaf_training_materializer.py` | — | No |
| `NCAAF_TRAINING_LEAKAGE_DETECTED` | feature_as_of must be strictly before event_start_time for every training row. | `ncaaf_three_way_trainer.py` | `ncaaf_trainer.py` | No |
| `NCAAF_TRAINING_ROWS_NOT_CHRONOLOGICAL` | Training rows must be provided in ascending event_start_time order. | `ncaaf_three_way_trainer.py` | `ncaaf_trainer.py` | No |
| `NCAAF_TRAINING_SAMPLE_INSUFFICIENT` | At least {min_rows} settled pregame rows are required; got {len(rows)}. | `ncaaf_trainer.py` | — | No |
| `NCAAF_TWO_WAY_ML_PRICE_INVALID` | No inline message; terminal code raised in `validate_ncaaf_evidence`. | `ncaaf_trust.py` | — | No |
| `NCAAF_VALIDATION_SAMPLE_INSUFFICIENT` | At least {min_validation_rows} future validation rows are required; got {len(y_val)}. | `ncaaf_trainer.py` | — | No |

<!-- sport-failure-registry:NCAAF:end -->

<!-- sport-failure-registry:NCAAB:begin -->
## NCAAB failure-code registry (November 2026)

Derived from production modules by `scripts/sport_failure_registry.py`; run it
after changing NCAAB code. A code that is a model label, table or metric
name, or a success disposition is catalogued in the tables below rather than
as a failure. `can_execute=false` and dry-run-only remain invariant.

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---|
| `NCAAB_CANDIDATE_SAMPLE_INSUFFICIENT` | No inline message; terminal code raised in `build_training_rows`. | `v17/ncaab_sportsdataverse_candidate.py` | — | No |
| `NCAAB_EVENT_DATE_MISSING` | No inline message; terminal code raised in `_event_time`. | `v17/ncaab_sportsdataverse_candidate.py` | — | No |
| `NCAAB_PREGAME_CANONICAL_ID_REQUIRED` | No inline message; terminal code raised in `_identity`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_CURRENT_RESULT_LEAKAGE` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_DUPLICATE_GAME_VERSION` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_EVENT_ID_MISMATCH` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_EVENT_MISSING` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_EVENT_TIME_INVALID` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_EVENT_VERSION_INVALID` | No inline message; terminal code raised in `_positive_version`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_EXECUTION_FLAG_REJECTED` | No inline message; terminal code raised in `_source`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_FEATURE_CONTRACT_MISMATCH` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_FRESHNESS_CONFIG_INVALID` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_FUTURE_SEASON` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_HISTORY_FUTURE_LEAKAGE` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_HISTORY_INVALID` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_HISTORY_NOT_SETTLED` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_HISTORY_SOURCE_TIME_MISSING` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_HISTORY_STAT_INVALID` | No inline message; terminal code raised in `_stats`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_HISTORY_STAT_MISSING` | No inline message; terminal code raised in `_stats`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_HISTORY_TIME_INVALID` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_MANIFEST_IDENTITY_MISMATCH` | No inline message; terminal code raised in `_source`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_MARKET_INPUT_REJECTED` | No inline message; terminal code raised in `_source`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_NEUTRAL_SITE_REVIEW_REQUIRED` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_SEASON_INVALID` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_SMALL_SAMPLE_HOLD` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_SOURCE_MISSING` | No inline message; terminal code raised in `_source`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_SOURCE_POLICY_MISMATCH` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_SOURCE_STALE` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_SOURCE_TAMPERED` | No inline message; terminal code raised in `_source`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_SOURCE_TIME_MISSING` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_TARGET_IN_HISTORY` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_TARGET_NOT_PREGAME` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_TEAM_ID_COLLISION` | No inline message; terminal code raised in `hydrate_pregame`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_PREGAME_TEAM_SIDE_MISMATCH` | No inline message; terminal code raised in `_stats`. | `v17/ncaab_pregame_hydration.py` | — | No |
| `NCAAB_REPLAY_ARTIFACT_CHECKSUM_MISMATCH` | No inline message; terminal code raised in `verify_frozen_replay`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_CANDIDATE_CREATED_AT_MISSING` | No inline message; terminal code raised in `verify_frozen_replay`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_CANDIDATE_GOVERNANCE_VIOLATION` | No inline message; terminal code raised in `verify_frozen_replay`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_CANDIDATE_IDENTITY_MISMATCH` | No inline message; terminal code raised in `verify_frozen_replay`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_CANDIDATE_ID_MISMATCH` | No inline message; terminal code raised in `verify_frozen_replay`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_CANDIDATE_ID_MISSING` | No inline message; terminal code raised in `verify_frozen_replay`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_CANDIDATE_MISSING` | No inline message; terminal code raised in `verify_frozen_replay`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_CAN_EXECUTE_VIOLATION` | No inline message; terminal code raised in `_rows_from_frozen`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_DATASET_HASH_MISMATCH` | No inline message; terminal code raised in `verify_frozen_replay`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_DUPLICATE_EVENT` | No inline message; terminal code raised in `_rows_from_frozen`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_EVENT_ID_INVALID` | No inline message; terminal code raised in `_rows_from_frozen`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_EVENT_TIME_INVALID` | No inline message; terminal code raised in `_rows_from_frozen`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_EXPECTED_ARTIFACT_CHECKSUM_INVALID` | No inline message; terminal code raised in `verify_frozen_replay`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_EXPECTED_CANDIDATE_ID_REQUIRED` | No inline message; terminal code raised in `verify_frozen_replay`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_EXPECTED_DATASET_HASH_INVALID` | No inline message; terminal code raised in `verify_frozen_replay`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_FEATURE_INVALID` | No inline message; terminal code raised in `_rows_from_frozen`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_FEATURE_SCHEMA_MISMATCH` | No inline message; terminal code raised in `_rows_from_frozen`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_FEATURE_SET_MISMATCH` | No inline message; terminal code raised in `_rows_from_frozen`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_FROZEN_ROWS_MISSING` | No inline message; terminal code raised in `verify_frozen_replay`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_METRIC_MISMATCH` | No inline message; terminal code raised in `verify_frozen_replay`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_MODEL_FAMILY_MISMATCH` | No inline message; terminal code raised in `_rows_from_frozen`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_OUTCOME_INVALID` | No inline message; terminal code raised in `_rows_from_frozen`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_READ_CREDENTIAL_UNAVAILABLE` | No inline message; terminal code raised in `main`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_READ_ONLY_KEY` | No inline message; terminal code raised in `main`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_REFIT_FAILED` | No inline message; terminal code raised in `verify_frozen_replay`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_ROW_CREATED_AT_MISSING` | No inline message; terminal code raised in `_rows_from_frozen`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_ROW_POST_CANDIDATE` | No inline message; terminal code raised in `_rows_from_frozen`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_SOURCE_MANIFEST_HASH_INVALID` | No inline message; terminal code raised in `_rows_from_frozen`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_SOURCE_MANIFEST_MISSING` | No inline message; terminal code raised in `_rows_from_frozen`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_SOURCE_MANIFEST_TAMPERED` | No inline message; terminal code raised in `_rows_from_frozen`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_SOURCE_READ_FAILED` | No inline message; terminal code raised in `main`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_SPLIT_MISMATCH` | No inline message; terminal code raised in `verify_frozen_replay`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_STORED_ARTIFACT_TAMPERED` | No inline message; terminal code raised in `verify_frozen_replay`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_TEMPORAL_LEAKAGE` | No inline message; terminal code raised in `_rows_from_frozen`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_TRAINING_CODE_SHA_INVALID` | No inline message; terminal code raised in `verify_frozen_replay`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_VERIFIER_SHA_REQUIRED` | No inline message; terminal code raised in `verify_frozen_replay`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_REPLAY_WRONG_SPORT` | No inline message; terminal code raised in `_rows_from_frozen`. | `v17/ncaab_moneyline_replay.py` | — | No |
| `NCAAB_SOURCE_ARTIFACT_CHECKSUM_MISMATCH` | No inline message; terminal code raised in `hold`. | `v17/ncaab_source_pinned_replay.py` | — | No |
| `NCAAB_SOURCE_ASSET_COUNT_MISMATCH` | No inline message; terminal code raised in `hold`. | `v17/ncaab_source_pinned_replay.py` | — | No |
| `NCAAB_SOURCE_CALIBRATOR_CHECKSUM_MISMATCH` | No inline message; terminal code raised in `hold`. | `v17/ncaab_source_pinned_replay.py` | — | No |
| `NCAAB_SOURCE_DATASET_HASH_MISMATCH` | No inline message; terminal code raised in `hold`. | `v17/ncaab_source_pinned_replay.py` | — | No |
| `NCAAB_SOURCE_DUPLICATE_SEASON` | No inline message; terminal code raised in `hold`. | `v17/ncaab_source_pinned_replay.py` | — | No |
| `NCAAB_SOURCE_GOVERNANCE_VIOLATION` | No inline message; terminal code raised in `hold`. | `v17/ncaab_source_pinned_replay.py` | — | No |
| `NCAAB_SOURCE_LICENSE_MISMATCH` | No inline message; terminal code raised in `hold`. | `v17/ncaab_source_pinned_replay.py` | — | No |
| `NCAAB_SOURCE_METRIC_MISMATCH` | No inline message; terminal code raised in `hold`. | `v17/ncaab_source_pinned_replay.py` | — | No |
| `NCAAB_SOURCE_PIN_MISMATCH` | No inline message; terminal code raised in `hold`. | `v17/ncaab_source_pinned_replay.py` | — | No |
| `NCAAB_SOURCE_REPLAY_INPUT_OR_RUNTIME_BLOCKED` | No inline message; terminal code raised in `main`. | `v17/ncaab_source_pinned_replay.py` | — | No |
| `NCAAB_SOURCE_ROW_COUNT_MISMATCH` | No inline message; terminal code raised in `hold`. | `v17/ncaab_source_pinned_replay.py` | — | No |
| `NCAAB_SOURCE_ROW_PROVENANCE_MISMATCH` | No inline message; terminal code raised in `hold`. | `v17/ncaab_source_pinned_replay.py` | — | No |
| `NCAAB_SOURCE_SEASON_MISSING` | No inline message; terminal code raised in `hold`. | `v17/ncaab_source_pinned_replay.py` | — | No |
| `NCAAB_SOURCE_SPECIALIST_MISMATCH` | No inline message; terminal code raised in `hold`. | `v17/ncaab_source_pinned_replay.py` | — | No |
| `NCAAB_SOURCE_TOTAL_ROWS_MISMATCH` | No inline message; terminal code raised in `hold`. | `v17/ncaab_source_pinned_replay.py` | — | No |
| `NCAAB_SOURCE_URL_MISMATCH` | No inline message; terminal code raised in `hold`. | `v17/ncaab_source_pinned_replay.py` | — | No |
| `NCAAB_SPORTSDATAVERSE_EMPTY` | No inline message; terminal code raised in `fetch_games`. | `v17/ncaab_sportsdataverse_candidate.py` | — | No |
| `NCAAB_SPORTSDATAVERSE_HTTP_FAILED` | No inline message; terminal code raised in `fetch_games`. | `v17/ncaab_sportsdataverse_candidate.py` | — | No |
| `NCAAB_TRAINING_CODE_SHA_REQUIRED` | No inline message; terminal code raised in `train_and_persist`. | `v17/ncaab_sportsdataverse_candidate.py` | — | No |

<!-- sport-failure-registry:NCAAB:end -->

<!-- sport-failure-registry:TENNIS:begin -->
## Tennis failure-code registry (November 2026)

Derived from production modules by `scripts/sport_failure_registry.py`; run it
after changing Tennis code. A code that is a model label, table or metric
name, or a success disposition is catalogued in the tables below rather than
as a failure. `can_execute=false` and dry-run-only remain invariant.

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---|
| `TENNIS_CANDIDATE_SAMPLE_INSUFFICIENT` | No inline message; terminal code raised in `build_training_rows`. | `v17/tennis_valuebet_candidate.py` | — | No |
| `TENNIS_MATCH_DATE_MISSING` | No inline message; terminal code raised in `_parse_time`. | `v17/tennis_valuebet_candidate.py` | — | No |
| `TENNIS_TRAINING_CODE_SHA_REQUIRED` | No inline message; terminal code raised in `train_all`. | `v17/tennis_valuebet_candidate.py` | — | No |
| `TENNIS_VALUEBET_EMPTY` | No inline message; terminal code raised in `fetch_matches`. | `v17/tennis_valuebet_candidate.py` | — | No |
| `TENNIS_VALUEBET_HTTP_FAILED` | No inline message; terminal code raised in `fetch_matches`. | `v17/tennis_valuebet_candidate.py` | — | No |
| `TENNIS_VALUEBET_SCHEMA_INVALID` | No inline message; terminal code raised in `fetch_matches`. | `v17/tennis_valuebet_candidate.py` | — | No |

<!-- sport-failure-registry:TENNIS:end -->

<!-- sport-failure-registry:SOCCER:begin -->
## Soccer failure-code registry (November 2026)

Derived from production modules by `scripts/sport_failure_registry.py`; run it
after changing Soccer code. A code that is a model label, table or metric
name, or a success disposition is catalogued in the tables below rather than
as a failure. `can_execute=false` and dry-run-only remain invariant.

| Code | Meaning | Emitting module | Also emitted by | Rank eligible? |
|---|---|---|---|---|
| `SOCCER_CANDIDATE_SAMPLE_INSUFFICIENT` | No inline message; terminal code raised in `build_training_rows`. | `v17/soccer_openfootball_candidate.py` | — | No |
| `SOCCER_EVENT_DATE_MISSING` | No inline message; terminal code raised in `_event_time`. | `v17/soccer_openfootball_candidate.py` | — | No |
| `SOCCER_OPENFOOTBALL_EMPTY` | No inline message; terminal code raised in `fetch_competition`. | `v17/soccer_openfootball_candidate.py` | — | No |
| `SOCCER_OPENFOOTBALL_HTTP_FAILED` | No inline message; terminal code raised in `fetch_competition`. | `v17/soccer_openfootball_candidate.py` | — | No |
| `SOCCER_OPENFOOTBALL_SCHEMA_INVALID` | No inline message; terminal code raised in `fetch_competition`. | `v17/soccer_openfootball_candidate.py` | — | No |
| `SOCCER_TRAINING_CODE_SHA_REQUIRED` | No inline message; terminal code raised in `train_all`. | `v17/soccer_openfootball_candidate.py` | — | No |

<!-- sport-failure-registry:SOCCER:end -->
