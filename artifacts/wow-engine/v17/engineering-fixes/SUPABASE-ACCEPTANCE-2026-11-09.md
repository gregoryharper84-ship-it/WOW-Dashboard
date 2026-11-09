# Hosted Supabase acceptance — availability durability (2026-11-09)

Scope: the durable-snapshot half of the 2026-11-09 availability repair, run
against the hosted Supabase project rather than the local Postgres. The
recheck itself is service-layer logic with no database dependency and is
covered by the Python suites, not here.

Status: **PASS for the snapshot durability contract on the hosted engine.**
Not a production deploy, not a production replay, and not `FIXED_AND_VERIFIED`.

## Target and access path

| Item | Value |
| --- | --- |
| Project | `wow-engine-validation` |
| Ref | `iczfhsmjrrafhvcpmqhr` |
| Engine | PostgreSQL 17.6.1 |
| Access | Supabase integration SQL (read-only and scoped-write endpoints) |
| Credentials | None handled, printed, or stored in this workspace |

This is the only project on the connected Supabase accounts that carries the
WOW schema (`wow_prop_evidence_snapshots`, `wow_prop_evidence_snapshot`). The
Render service's `SUPABASE_URL` binding was not visible from here, so whether
this project is the live production write target is **not established**.

## Why the pytest suite was not pointed at it

`test_prop_snapshot_min3_postgres.py` asserts its DSN host is `localhost` or
`127.0.0.1` and the database is `wow_agent_ci`, with the message "local
ephemeral Postgres only". That guard exists so the suite can never write to a
hosted project by accident. It was **not weakened or bypassed**. Instead the
same acceptance was executed through two hosted layers:

1. read-only verification of the deployed artifact, and
2. a behavioral round-trip of the *deployed function body* against the
   *deployed table shape* inside an isolated scratch schema that was dropped
   afterward. Nothing was written to any `public` table.

## Layer 1 — deployed artifact (read-only)

| Check | Observed |
| --- | --- |
| Reader function present | yes, `public.wow_prop_evidence_snapshot(uuid,text,text,text,text,numeric)` |
| Min3 scoping deployed | yes (`v_required_history` in deployed definition) |
| Blocker enforcement deployed | yes (`cardinality(v_row.blockers) > 0`) |
| Blocker echo deployed | yes (`to_jsonb(v_row.blockers)`) |
| `anon` EXECUTE on reader | false |
| `anon` SELECT on snapshot table | false |
| `service_role` EXECUTE on reader | true |
| RLS on snapshot table | enabled, 0 policies (backend-only) |
| Migration applied | `v17_mlb_strikeout_snapshot_min3`, version `20261107074802` |
| Integration role EXECUTE | denied, SQLSTATE 42501 |

The last row means the integration's own role cannot execute the reader. That
is the intended boundary, and it also means the deployed public function could
not be called directly through the integration.

## Layer 2 — behavioral round-trip on the hosted engine

Method: `create table … (like public.wow_prop_evidence_snapshots including all)`
and `pg_get_functiondef` of the deployed reader, namespaced into scratch schema
`wow_accept_avail_20261109`, seeded with five synthetic rows, asserted, then
`drop schema … cascade`. The fixtures are synthetic; no production row was read
into the acceptance path.

| Fixture | Expected | Observed |
| --- | --- | --- |
| MLB K, 10 games, blocker `PLAYER_AVAILABILITY_RECHECK_REQUIRED` | invalid, no publication | `RUN_INVALID_ACQUISITION_INCOMPLETE`, `blockers` echoed `["PLAYER_AVAILABILITY_RECHECK_REQUIRED"]`, `can_execute=false` |
| MLB K, 3 games, no blockers, `CONFIRMED_STARTER` | ready | `PROP_EVIDENCE_READY`, availability readback intact (`basis=CONFIRMED_STARTER`, official recheck source), `can_execute=false` |
| MLB K, 2 games, no blockers | invalid (min3 floor) | `RUN_INVALID_ACQUISITION_INCOMPLETE` |
| NFL, 3 games, no blockers | invalid (L10 retained) | `RUN_INVALID_ACQUISITION_INCOMPLETE` |
| MLB K, 10 games, blocker `PLAYER_RULED_OUT`, availability `OUT` | invalid, no publication | `RUN_INVALID_ACQUISITION_INCOMPLETE`, `blockers` echoed `["PLAYER_RULED_OUT"]`, `can_execute=false` |
| Matching row with wrong player name | identity rejected | `PROP_EVIDENCE_IDENTITY_MISMATCH` |
| Random snapshot id | not found | `PROP_EVIDENCE_SNAPSHOT_NOT_FOUND` |

Scratch schema dropped after the run; absence verified with a catalog read.

## What the hosted instance itself showed

`public.wow_prop_evidence_snapshots` holds **21,936 rows**, of which **0** carry
`role_status.availability` and **0** carry any blocker. Consequences:

- The repair's write path has not produced availability-bearing rows in this
  project yet. This is consistent with production still running the pre-repair
  build `4c8c1fb0`, which never wrote `role_status.availability`; the Render SHA
  gap is already recorded elsewhere. It is **not** proof, because the binding
  between Render and this project is unverified.
- Real-data durability could not be demonstrated on existing rows, which is why
  the acceptance used fixtures. Read-side enforcement is verified; write-side
  round-trip against authentic production rows remains unproven.

## Not proven here

- Execution of the deployed public function by an authorized application role
  (integration role is denied; no service-role credential exists in this
  workspace, and none was requested in chat).
- Any write to a `public` table, by design.
- Production replay, Render deployment SHA, and the live pregame confirmation
  feed. See `REPORT-2026-11-09__p0-availability-and-pipeline-audit.md`.

## Related local evidence

- Availability, recheck, final-refresh proof, route and durability suites:
  **127 passed**.
- Prop/WNBA/snapshot surface with the ephemeral Postgres: **1118 passed,
  0 skipped**.
