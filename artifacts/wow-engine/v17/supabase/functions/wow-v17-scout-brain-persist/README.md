# wow-v17-scout-brain-persist

Governed Supabase Edge Function for Scout Brain persistence, vendored here so
the deployed artifact has a version-controlled source of truth. Before this it
existed only as a deployed function, with no diffable history.

Deployed to project `iczfhsmjrrafhvcpmqhr` (`wow-engine-validation`) at
`/functions/v1/wow-v17-scout-brain-persist`.

## Authority

Research-layer writes only. The function re-validates governance on every
request and refuses a payload whose `can_execute` is not `false` or whose
candidate rows carry `probability`, `model_probability`,
`calibrated_probability`, or `calibrated_lower_bound`. It writes `probability`
as `null` and `can_execute` as `false` unconditionally. It is not a probability
authority and may not assign or upgrade a terminal label.

Callers are authorized by GitHub OIDC pinned to the exact protected-main
persistence workflow. No Supabase database or service-role credential is stored
in GitHub.

## Transport phases

A full slate does not fit in one Edge Function worker. The first attempt posted
the whole handoff and returned HTTP 546 `WORKER_RESOURCE_LIMIT`; bounding whole
candidates did not help, because one team/event candidate in run 35131443035 was
1,135,529 bytes over 1,952 evidence rows (largest: 5,291,090 bytes over 2,704)
and a batch always emitted at least one candidate, so it travelled intact under
both a 256 KiB and a 64 KiB bound.

Evidence is therefore sliced, and the bounds apply to the request itself:

| Phase | Body | Effect |
| --- | --- | --- |
| `BEGIN` | run identity and governance header | opens the run, resets counters |
| `APPEND` | header plus candidate slices | persists the slices, accumulates tallies |
| `FINALIZE` | run identity and governance header | recomputes sport counts, closes the run |

Each `APPEND` entry carries the candidate's metadata, a bounded chunk of its
`market_evidence`, and an `evidence_slice` of `{index, final, offset, count,
total}`. A request with no `persist_phase` keeps the original single-request
behavior for small slates and for callers that have not been updated.

Phases and slices change transport only. Governance is re-validated on every
request, and tallies are accumulated server-side rather than supplied by the
caller.

## Invariants the slicing depends on

- **Candidate identity is slice-independent.** A team/event row keys off
  sport + event id + route, never its first evidence row, which changes per
  slice. The key preserves the seven-field shape with the evidence fields
  empty, so it reproduces the id the previous algorithm produced for an
  evidence-free team/event row and existing rows keep their identity. Prop
  rows keep the original algorithm and the object evidence shape.
- **A candidate is counted once.** `changed_candidate_count` advances on the
  first slice, `candidate_count` and `quarantined_count` on the final slice.
- **`candidate_history` is written once per candidate per run**, via a guarded
  insert. Production already holds 302 duplicate `(candidate_id,
  research_run_id)` groups, so the unique constraint belongs in a separate
  hardening migration after those are cleaned, not here.
- **History does not carry the evidence array.** Evidence is durable in
  `observations`, `source_snapshots` and `candidate_source_links`; history
  keeps the research snapshot plus `evidence_row_count`.
- **Every JSON column is bound as text and cast in the statement**, including
  `research_runs.source_snapshot` on BEGIN and FINALIZE. With `prepare: false`,
  a `${...}::jsonb` parameter makes Postgres infer the parameter as jsonb and
  the driver then JSON-encodes the already-serialized string, storing a jsonb
  *string*. Every pre-existing `candidate_history.snapshot` and every
  `research_runs.source_snapshot` is a string for that reason.
- **Snapshot merges are guarded on `jsonb_typeof`.** `string || object` yields
  a jsonb *array* in Postgres, after which `->>'observations_seen'` reads NULL
  and the running tallies silently reset to zero on every request. Guarding the
  base on `jsonb_typeof(...)='object'` makes the existing string-valued rows
  self-heal on their next run instead of corrupting the receipt. Historical
  normalization is deliberately left to a separate cleanup.

## Deployment

Not deployed by CI. Deploy deliberately after review, then confirm the deployed
version against this source.
