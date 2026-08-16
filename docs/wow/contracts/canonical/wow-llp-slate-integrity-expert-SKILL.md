# Skill: wow.llp-slate-integrity-expert

## Purpose

Verify that every LLP candidate belongs to the correct current slate before probability modeling begins.

## Governance

```text
WOW_VERSION=WOW_v16_CLEAN_CORE
lane_status=IDENTITY_AND_STATUS_GATE
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS
```

## Required Inputs

```text
candidate_id
sport
league
participant_a
participant_b
event_date_claimed
event_time_claimed
user_timezone
official_schedule_source
source_capture_timestamp
```

## Required Outputs

```text
official_event_id
official_event_url_or_source
official_start_local
official_start_utc
home_participant
away_participant
venue
event_status
status_timestamp
slate_date_match
participant_identity_match
duplicate_event_status
```

## Workflow

1. Normalize participant names and league.
2. Resolve the official event ID from an official league, team, governing-body, or tournament source.
3. Convert the official start to UTC and the user's timezone.
4. Compare the official date with the requested slate date.
5. Verify current status: scheduled, delayed, postponed, canceled, in progress, final.
6. Detect wrong-year and stale-search contamination.
7. Detect impossible duplicate-team appearances.
8. Preserve verified doubleheaders or tournament formats only when official IDs differ and timing is feasible.
9. Write one terminal identity/status result per candidate.

## Hard Blocks

```text
WRONG_DATE
WRONG_YEAR
EVENT_NOT_FOUND
EVENT_ALREADY_STARTED
EVENT_FINISHED
EVENT_POSTPONED
EVENT_CANCELED
DUPLICATE_TEAM_EVENT
PARTICIPANT_IDENTITY_CONFLICT
TIMEZONE_DATE_MISMATCH
```

## Decision Logic

```text
all identity and status fields verified => SLATE_IDENTITY_PASS
material field unresolved => SLATE_DATA_UNOBTAINABLE
any hard block => candidate_removed=true
```

## Required Output

| Candidate | Official Event ID | Official Start | Status | Date Match | Identity Match | Duplicate Check | Result |
|---|---|---|---|---|---|---|---|

Footer:

```text
rows_in=
rows_passed=
rows_removed=
wrong_date=
wrong_year=
started=
finished=
duplicate_team=
unresolved=
can_execute=false
```

## Acceptance Tests

1. An August 2 game is rejected from an August 1 slate.
2. A 2025 event found by search is rejected from a 2026 slate.
3. A team appearing in two impossible same-day events triggers duplicate detection.
4. A verified doubleheader is preserved.
5. A game that starts before final output is removed.
