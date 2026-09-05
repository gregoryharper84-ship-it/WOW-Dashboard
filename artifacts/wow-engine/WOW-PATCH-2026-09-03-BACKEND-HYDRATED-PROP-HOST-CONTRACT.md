# WOW PATCH — 2026-09-03 — BACKEND-HYDRATED PROP HOST CONTRACT

## Incident
A live WOW Betting Engine trace for `Tarik Skubal — MORE 5.5 pitcher strikeouts — PrizePicks — pregame` exposed a host/backend contract mismatch. The live GPT attempted to construct raw historical evidence itself. Early Action attempts failed validation because the host supplied incomplete evidence fields; a later structurally complete but short packet reached the backend and terminated on `L10_GAME_LOG_INCOMPLETE` before specialist invocation.

## Verified backend state
Current V17 `pick_request_runtime.py` already owns automatic evidence hydration for certified MLB `PITCHER_STRIKEOUTS` rows when `PickRequestRow.evidence` is omitted. `prop_auto_hydration.py` acquires official MLB player identity, probable-pitcher schedule context, at least ten prior regular-season starts, aligned box-score rows, role/opportunity metadata, timestamps, and optional opponent context. The current canonical V17 OpenAPI does not require `PickRequestRow.evidence`.

## Controlling repair
For any route with certified server-owned automatic hydration, the WOW Custom GPT host MUST submit the normalized identity/board contract and omit `evidence` unless it already possesses a complete backend-compatible raw evidence packet from an authoritative source.

The host MUST NOT synthesize, guess, pad, or partially populate any of the following merely to satisfy an Action schema:
- `game_log`
- `box_score_log`
- `role_status`
- `role_timestamp`
- `opportunity_ledger`
- `source_timestamps`
- `rate_provenance`

For MLB `PITCHER_STRIKEOUTS`, canonical `/score-pick-request` owns historical acquisition, L10 construction, evidence validation/freeze, specialist invocation, calibration, bounds, and persistence.

If an installed live GPT Action schema demands historical evidence for a canonical route whose repository schema marks `evidence` optional, classify that as `LIVE_GPT_ACTION_SCHEMA_STALE` / incomplete `LIVE_GPT_EDITOR_SYNC`. Do not work around it by fabricating host evidence.

## Exact regression fixture
Minimal row:

```json
{
  "request_id": "skubal-minimal-contract-regression",
  "rows": [
    {
      "row_key": "skubal-k55-more",
      "event_id": "823907",
      "event_start_time": "2026-09-04T02:10:00+00:00",
      "sport": "MLB",
      "league": "MLB",
      "player": "Tarik Skubal",
      "stat_type": "PITCHER_STRIKEOUTS",
      "line": 5.5,
      "direction": "MORE",
      "source_type": "NORMALIZED",
      "platform": "PrizePicks"
    }
  ]
}
```

Acceptance:
1. Request schema validation passes with `evidence` omitted.
2. Certified backend auto-hydration is attempted.
3. If official historical inputs are obtained, the frozen evidence contains at least 10 prior starts and the controlling specialist is invoked.
4. If server acquisition genuinely fails, return the typed acquisition/input blocker; never rewrite it as `MODEL_UNAVAILABLE` unless the controlling fitted capability/artifact itself is absent.
5. `can_execute=false` remains invariant.

## Live editor sync requirement
Update the live WOW Betting Engine GPT instructions to state explicitly: for certified backend-hydrated prop routes, omit raw evidence by default and never manufacture a partial L10 packet. Ensure the live Action uses the current canonical `v17/openapi.wow-betting-engine.v17.yaml` schema, where `PickRequestRow.evidence` is optional.
