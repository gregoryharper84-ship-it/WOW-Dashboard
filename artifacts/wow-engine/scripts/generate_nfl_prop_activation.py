#!/usr/bin/env python3
"""Generate the exact governed activation artifacts for validated NFL direct props.

This script refuses to generate production registration unless all four frozen
candidate artifacts satisfy the offline validation contract. It does not touch a
database, publish a probability, or enable wager execution.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {"PASSING_YARDS", "RUSHING_YARDS", "RECEIVING_YARDS", "ANYTIME_TD"}
SPECIALIST = "wow.nfl-player-prop-probability-expert"


def _lit(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _jsonb(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return "'" + raw.replace("'", "''") + "'::jsonb"


def _validated_artifacts() -> dict[str, dict[str, Any]]:
    rows = json.loads((ROOT / "data/wow_nfl_prop_artifacts_v1.json").read_text(encoding="utf-8"))
    by_stat = {str(row["stat_type"]): row for row in rows}
    if set(by_stat) != EXPECTED:
        raise RuntimeError(f"NFL_PROP_ROUTE_SET_INVALID:{sorted(by_stat)}")
    for stat, row in by_stat.items():
        metrics = row["validation_metrics"]
        assertions = {
            "certification_eligible": row.get("certification_eligible") is True,
            "validation_pass": metrics.get("validation_status") == "PASS",
            "blockers_empty": metrics.get("blockers") == [],
            "family": row.get("model_family") == "NFL_PROP_ROLLING_FITTED_V1",
            "calibrator": row.get("calibrator_version") == "NFL_PROP_PRECALIBRATION_BOOTSTRAP_V1",
            "specialist": row.get("specialist_version") == SPECIALIST + "@1",
            "provider": row.get("provider_identity") == "WOW_PROP_FITTED_MODEL_V1",
            "license": row.get("source_license_id") == "CC-BY-4.0",
            "can_execute_false": row.get("can_execute") is False,
            "holdout_2025": metrics.get("holdout_season") == 2025,
            "chronological": metrics.get("whole_week_chronological_split") is True,
            "train_rows": int(metrics.get("train_rows") or 0) >= 1000,
            "calibration_rows": int(metrics.get("calibration_rows") or 0) >= 300,
            "holdout_rows": int(metrics.get("holdout_rows") or 0) >= 300,
            "ood_rate": float(metrics.get("holdout_ood_rate_z_gt_6") or 0.0) <= 0.08,
        }
        ratio_key = "brier_ratio_vs_naive" if stat == "ANYTIME_TD" else "mae_ratio_vs_naive"
        assertions["naive_gate"] = float(metrics.get(ratio_key, 999.0)) <= 1.03
        failed = sorted(key for key, passed in assertions.items() if not passed)
        if failed:
            raise RuntimeError(f"NFL_PROP_CERTIFICATION_GATE_FAILED:{stat}:{','.join(failed)}")
    return by_stat


def _migration(by_stat: dict[str, dict[str, Any]]) -> str:
    columns = (
        "provider_identity", "model_family", "model_artifact_version", "calibrator_version",
        "sport", "stat_type", "feature_schema_version", "feature_transform_version",
        "specialist_version", "certification_id", "lifecycle_state", "training_dataset_hash",
        "training_code_sha", "artifact_checksum", "artifact_format", "artifact_payload",
        "supported_line_min", "supported_line_max", "training_rows", "validation_metrics",
        "promoted", "active", "probability_publishable", "can_execute",
    )
    value_rows: list[str] = []
    versions: list[str] = []
    for stat in sorted(EXPECTED):
        row = by_stat[stat]
        versions.append(str(row["model_artifact_version"]))
        values = [
            row["provider_identity"], row["model_family"], row["model_artifact_version"],
            row["calibrator_version"], "NFL", stat, row["feature_schema_version"],
            row["feature_transform_version"], row["specialist_version"], row["certification_id"],
            "PROSPECTIVE_CERTIFIED", row["training_dataset_hash"], row["training_code_sha"],
            row["artifact_checksum"], row["artifact_format"], None,
            row["supported_line_min"], row["supported_line_max"], row["training_rows"], None,
            True, True, False, False,
        ]
        encoded: list[str] = []
        for index, value in enumerate(values):
            if index == 15:
                encoded.append(_jsonb(row["artifact_payload"]))
            elif index == 19:
                encoded.append(_jsonb(row["validation_metrics"]))
            else:
                encoded.append(_lit(value))
        value_rows.append("  (" + ", ".join(encoded) + ")")

    version_list = ", ".join(_lit(value) for value in versions)
    columns_sql = ", ".join(columns)
    rows_sql = ",\n".join(value_rows)
    return f"""-- WOW V17 governed NFL direct-prop activation, 2026-09-20.
-- Generated only from frozen PASS validation artifacts.
-- No sportsbook/market probability enters these model rows. can_execute=false.

begin;

update public.wow_prop_fitted_model_artifacts
set active=false, promoted=false, probability_publishable=false, can_execute=false
where upper(sport)='NFL'
  and upper(stat_type) in ('PASSING_YARDS','RUSHING_YARDS','RECEIVING_YARDS','ANYTIME_TD')
  and model_artifact_version not in ({version_list});

insert into public.wow_prop_fitted_model_artifacts (
  {columns_sql}
) values
{rows_sql}
on conflict (model_artifact_version) do update set
  provider_identity=excluded.provider_identity,
  model_family=excluded.model_family,
  calibrator_version=excluded.calibrator_version,
  sport=excluded.sport,
  stat_type=excluded.stat_type,
  feature_schema_version=excluded.feature_schema_version,
  feature_transform_version=excluded.feature_transform_version,
  specialist_version=excluded.specialist_version,
  certification_id=excluded.certification_id,
  lifecycle_state='PROSPECTIVE_CERTIFIED',
  training_dataset_hash=excluded.training_dataset_hash,
  training_code_sha=excluded.training_code_sha,
  artifact_checksum=excluded.artifact_checksum,
  artifact_format=excluded.artifact_format,
  artifact_payload=excluded.artifact_payload,
  supported_line_min=excluded.supported_line_min,
  supported_line_max=excluded.supported_line_max,
  training_rows=excluded.training_rows,
  validation_metrics=excluded.validation_metrics,
  promoted=true, active=true, probability_publishable=false, can_execute=false;

create or replace function public.wow_controlling_specialist(
  p_sport text, p_prop_type text
) returns jsonb
language plpgsql
immutable
set search_path to ''
as $function$
declare
  v_sport text := upper(trim(coalesce(p_sport,'')));
  v_key text := upper(regexp_replace(trim(coalesce(p_prop_type,'')), '[^A-Za-z0-9]+', '_', 'g'));
  v_controller text;
  v_supporting jsonb := '[]'::jsonb;
begin
  v_key := trim(both '_' from v_key);
  if v_sport='MLB' and v_key in ('1ST_INNING_PITCHES_THROWN','FIRST_INNING_PITCHES_THROWN','1ST_INNING_PITCH_COUNT','FIRST_INNING_PITCH_COUNT') then
    v_controller := 'wow.mlb-first-inning-pitch-count-expert';
    v_supporting := jsonb_build_array('wow.mlb-pitcher-failure-path-expert');
  elsif v_sport='MLB' and v_key='PITCHING_OUTS' then
    v_controller := 'wow.mlb-pitcher-outs-workload-expert';
  elsif v_sport='MLB' and v_key in ('STRIKES_THROWN','BALLS_THROWN') then
    v_controller := 'wow.mlb-pitcher-pitch-composition-expert';
  elsif v_sport='MLB' and v_key='PLATE_APPEARANCES' then
    v_controller := 'wow.mlb-batter-plate-appearances-expert';
  elsif v_sport='MLB' and v_key='PITCHER_FANTASY_SCORE' then
    v_controller := 'wow.mlb-pitcher-fantasy-score-expert';
  elsif v_sport='MLB' and (v_key like '%STRIKEOUT%' or v_key like '%PITCH_COUNT%' or v_key like '%WALK%' or v_key like '%HIT_ALLOWED%' or v_key like '%RUN_ALLOWED%') then
    v_controller := 'wow.mlb-pitcher-failure-path-expert';
  elsif v_sport='WNBA' and v_key in ('POINTS','REBOUNDS','ASSISTS','THREE_POINTERS_MADE') then
    v_controller := 'wow.wnba-player-prop-probability-expert';
  elsif v_sport='NFL' and v_key in ('PASSING_YARDS','RUSHING_YARDS','RECEIVING_YARDS','ANYTIME_TD') then
    v_controller := '{SPECIALIST}';
  else
    v_controller := 'MODEL_UNAVAILABLE';
  end if;
  return jsonb_build_object(
    'sport',v_sport,'canonical_prop_type',v_key,'controlling_specialist',v_controller,
    'supporting_specialists',v_supporting,
    'min_event_tree_simulations',case when v_controller='wow.mlb-first-inning-pitch-count-expert' then 25000 else null end,
    'can_execute',false
  );
end;
$function$;

comment on function public.wow_controlling_specialist(text,text) is
'V17 controlling-specialist router. Certified MLB/WNBA production routes plus validated NFL PASSING_YARDS/RUSHING_YARDS/RECEIVING_YARDS/ANYTIME_TD route to exact specialists. Unsupported routes fail closed. can_execute=false.';

commit;
"""


def _patch_manifest() -> None:
    path = ROOT / "v17/prop_capability_manifest.py"
    text = path.read_text(encoding="utf-8")
    if "NFL_PLAYER_PROP_EXPERT" in text:
        return
    anchor = 'WNBA_PLAYER_PROP_EXPERT = "wow.wnba-player-prop-probability-expert"\n'
    if anchor not in text:
        raise RuntimeError("NFL_PROP_MANIFEST_SPECIALIST_ANCHOR_MISSING")
    text = text.replace(anchor, anchor + f'NFL_PLAYER_PROP_EXPERT = "{SPECIALIST}"\n')
    anchor = 'WNBA_THREES_MADE = "THREE_POINTERS_MADE"\n'
    if anchor not in text:
        raise RuntimeError("NFL_PROP_MANIFEST_STAT_ANCHOR_MISSING")
    text = text.replace(
        anchor,
        anchor
        + 'NFL_PASSING_YARDS = "PASSING_YARDS"\n'
        + 'NFL_RUSHING_YARDS = "RUSHING_YARDS"\n'
        + 'NFL_RECEIVING_YARDS = "RECEIVING_YARDS"\n'
        + 'NFL_ANYTIME_TD = "ANYTIME_TD"\n',
    )
    marker = '    ("WNBA", WNBA_THREES_MADE): _wnba_candidate(WNBA_THREES_MADE),\n\n    # Fantasy Score candidate parity.'
    if marker not in text:
        raise RuntimeError("NFL_PROP_MANIFEST_LANE_ANCHOR_MISSING")
    lane_rows = '''    ("WNBA", WNBA_THREES_MADE): _wnba_candidate(WNBA_THREES_MADE),
    ("NFL", NFL_PASSING_YARDS): PropCapability(
        sport="NFL", stat_type=NFL_PASSING_YARDS, lane_status=CERTIFIED_PRODUCTION,
        controlling_specialist=NFL_PLAYER_PROP_EXPERT, route_active=True, declared_skill_status="PRODUCTION",
        exact_line_support_policy=CONTINUOUS_LINE_SUPPORT, certified_line_support_source="wow_prop_fitted_model_artifacts",
        publication_allowed=True, blocker=None, notes="Validated rolling fitted NFL passing-yards route; runtime artifact/input/calibration gates remain mandatory."),
    ("NFL", NFL_RUSHING_YARDS): PropCapability(
        sport="NFL", stat_type=NFL_RUSHING_YARDS, lane_status=CERTIFIED_PRODUCTION,
        controlling_specialist=NFL_PLAYER_PROP_EXPERT, route_active=True, declared_skill_status="PRODUCTION",
        exact_line_support_policy=CONTINUOUS_LINE_SUPPORT, certified_line_support_source="wow_prop_fitted_model_artifacts",
        publication_allowed=True, blocker=None, notes="Validated rolling fitted NFL rushing-yards route; runtime artifact/input/calibration gates remain mandatory."),
    ("NFL", NFL_RECEIVING_YARDS): PropCapability(
        sport="NFL", stat_type=NFL_RECEIVING_YARDS, lane_status=CERTIFIED_PRODUCTION,
        controlling_specialist=NFL_PLAYER_PROP_EXPERT, route_active=True, declared_skill_status="PRODUCTION",
        exact_line_support_policy=CONTINUOUS_LINE_SUPPORT, certified_line_support_source="wow_prop_fitted_model_artifacts",
        publication_allowed=True, blocker=None, notes="Validated rolling fitted NFL receiving-yards route; runtime artifact/input/calibration gates remain mandatory."),
    ("NFL", NFL_ANYTIME_TD): PropCapability(
        sport="NFL", stat_type=NFL_ANYTIME_TD, lane_status=CERTIFIED_PRODUCTION,
        controlling_specialist=NFL_PLAYER_PROP_EXPERT, route_active=True, declared_skill_status="PRODUCTION",
        exact_line_support_policy=CONTINUOUS_LINE_SUPPORT, certified_line_support_source="wow_prop_fitted_model_artifacts",
        publication_allowed=True, blocker=None, notes="Validated fitted NFL anytime-TD Bernoulli route; runtime artifact/input/calibration gates remain mandatory."),

    # Fantasy Score candidate parity.'''
    path.write_text(text.replace(marker, lane_rows), encoding="utf-8")


def _write_test() -> None:
    path = ROOT / "test_nfl_prop_certification_evidence.py"
    path.write_text('''import json\nfrom pathlib import Path\nfrom v17.prop_capability_manifest import CERTIFIED_PRODUCTION, prop_capability\n\nROOT=Path(__file__).resolve().parent\nEXPECTED={"PASSING_YARDS","RUSHING_YARDS","RECEIVING_YARDS","ANYTIME_TD"}\n\ndef _artifacts(): return json.loads((ROOT/"data/wow_nfl_prop_artifacts_v1.json").read_text())\n\ndef test_all_exact_routes_have_passed_frozen_certification_evidence():\n    rows=_artifacts(); assert {r["stat_type"] for r in rows}==EXPECTED\n    for row in rows:\n        m=row["validation_metrics"]\n        assert row["certification_eligible"] is True\n        assert m["validation_status"]=="PASS" and m["blockers"]==[]\n        assert m["holdout_season"]==2025 and m["whole_week_chronological_split"] is True\n        assert m["holdout_rows"]>=300 and m["calibration_rows"]>=300 and m["train_rows"]>=1000\n        assert m["holdout_ood_rate_z_gt_6"]<=0.08\n        assert row["source_license_id"]=="CC-BY-4.0"\n        assert row["can_execute"] is False and row["probability_publishable"] is False\n        if row["stat_type"]=="ANYTIME_TD": assert m["brier_ratio_vs_naive"]<=1.03\n        else: assert m["mae_ratio_vs_naive"]<=1.03\n\ndef test_manifest_declares_only_validated_exact_routes_as_nfl_production():\n    for stat in EXPECTED:\n        lane=prop_capability("NFL",stat)\n        assert lane.lane_status==CERTIFIED_PRODUCTION and lane.route_active and lane.publication_allowed\n        assert lane.controlling_specialist=="wow.nfl-player-prop-probability-expert"\n        assert lane.can_execute is False\n    unsupported=prop_capability("NFL","TACKLES")\n    assert unsupported.route_active is False and unsupported.publication_allowed is False\n\ndef test_activation_migration_is_exact_and_fail_closed():\n    sql=(ROOT/"migrations/20260920_nfl_direct_prop_governed_activation.sql").read_text()\n    for stat in EXPECTED: assert stat in sql\n    assert "wow.nfl-player-prop-probability-expert" in sql\n    assert "PROSPECTIVE_CERTIFIED" in sql\n    compact=sql.replace(" ","")\n    assert "probability_publishable=false" in compact and "can_execute=false" in compact\n    assert "MODEL_UNAVAILABLE" in sql\n''', encoding="utf-8")


def main() -> int:
    by_stat = _validated_artifacts()
    (ROOT / "migrations/20260920_nfl_direct_prop_governed_activation.sql").write_text(
        _migration(by_stat), encoding="utf-8"
    )
    _patch_manifest()
    _write_test()
    print("NFL_PROP_ACTIVATION_GENERATED=PASS can_execute=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
