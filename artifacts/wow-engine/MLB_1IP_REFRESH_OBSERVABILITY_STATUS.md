# MLB 1IP Refresh Observability

Purpose: make production scheduler activation and passes visible in Render logs while preserving non-publication and non-execution invariants.

- STARTED and PASS scheduler events are emitted at warning level so they are visible under the production logger defaults.
- FAIL remains error level.
- probability_publishable=false and can_execute=false are emitted on every scheduler lifecycle line.
- This change does not alter scoring, registry resolution, exact line support, refresh semantics, or any execution authority.
