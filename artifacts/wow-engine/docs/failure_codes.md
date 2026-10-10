# WOW V17 Canonical Failure-Code Registry

This file is the repository source of truth for governed typed failure/status codes introduced or relied on by the V17 full-board stabilization work. New typed failure codes must be registered in the same change that introduces them.

`can_execute=false` remains invariant. Market/evidence failures never imply a sporting-model capability failure.

| Code | Owning lane/stage | Meaning | Rank eligible? |
|---|---|---|---:|
| `MODEL_UNAVAILABLE` | model capability | Exact required fitted specialist/artifact/adapter is absent for the route. Never use for data, market, scorer, repository, or editor failures. | No |
| `MODEL_INPUTS_INSUFFICIENT` | model readiness | Fitted capability exists but required candidate-specific inputs are missing/insufficient. Includes a missing/invalid required calibration artifact for an otherwise available multisport scorer. | No |
| `MODEL_SCORER_FAILED` | model invocation | Selected model was invoked but threw, timed out, transport-failed, or returned no valid completion. | No |
| `MODEL_OUTPUT_INVALID` | model validation | Selected model returned malformed, non-numeric, schema-invalid, impossible, or non-normalized output. | No |
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

## Registry rule

A new code requires, in the same change: code name, owning lane/stage, exact condition, whether it affects sporting probability/rank, and a regression test. Provider-specific detail codes may be preserved underneath a registered class; they must never be rewritten into `MODEL_UNAVAILABLE` unless the fitted model capability itself is truly absent.

## NCAAF CFBD settled-game neutral-site evidence (research ingestion)

| Code | Owning lane/stage | Exact condition | Rank eligible? |
|---|---|---|---|
| `NCAAF_TRAINING_NEUTRAL_SITE_EVIDENCE_MISSING` | NCAAF CFBD settled-game materializer | One or more completed game rows have missing or non-boolean CFBD `neutralSite`. Such rows are skipped rather than persisted with an invented `neutral_site=False`; valid rows may still persist, but the missing-evidence blocker remains visible. Historical rows already ingested are not modified and require separately governed data-quality repair/refit. | No |
