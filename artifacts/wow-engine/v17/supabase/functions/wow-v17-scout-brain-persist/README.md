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

A full nightly slate does not fit in one Edge Function worker; posting it whole
returned HTTP 546 `WORKER_RESOURCE_LIMIT` and lost the run. The function accepts
a `persist_phase`:

| Phase | Body | Effect |
| --- | --- | --- |
| `BEGIN` | run identity and governance header | opens the run, resets counters |
| `APPEND` | header plus one `candidates` batch | persists the batch, accumulates tallies on the row |
| `FINALIZE` | run identity and governance header | recomputes sport counts, closes the run |

A request with no `persist_phase` keeps the original single-request behavior for
small slates and for callers that have not been updated.

Phases change transport only. Governance is re-validated on every phase, so a
chunked upload is not a way to move a row past the gate, and tallies are
accumulated server-side rather than supplied by the caller.

## Deployment

Not deployed by CI. Deploy deliberately after review, then confirm the deployed
version against this source.
