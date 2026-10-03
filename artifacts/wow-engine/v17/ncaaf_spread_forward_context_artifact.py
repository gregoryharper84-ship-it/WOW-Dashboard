"""Off-web immutable NCAAF spread-forward context artifact.

The production web process must not page the full NCAAF training corpus and fit
the spread challenger during startup. Protected GitHub Actions build the exact
same research artifact from direct PostgreSQL, persist one append-only,
checksummed context row, and the web process only loads/validates that compact
row.

This is serving/runtime infrastructure only. It does not change fitted-model
math, coefficients, residuals, features, line semantics, calibration,
certification, publication, ranking, or execution authority.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from v17.spread_margin_challenger import MarginDistributionArtifact, MarginTrainingRow, SpreadChallengerUnavailable, _dt
from v17.spread_margin_forward_fit import fit_margin_distribution_artifact
from v17.spread_margin_replay import (
    NCAAF_PERSISTED_FEATURE_MODEL_FAMILY,
    NCAAF_PERSISTED_FEATURE_SCHEMA_VERSION,
    _ncaaf_events_from_games,
    adapt_ncaaf_persisted_rows,
)

SPORT = "NCAAF"
CONTEXT_SCHEMA_VERSION = "wow.v17.ncaaf_spread_forward_context.v1"
CONTEXT_TABLE = "wow_ncaaf_spread_forward_context_artifacts"
MIN_TRAIN_ROWS = 300
RIDGE_ALPHA = 4.0
BUILDER_ID = "GITHUB_ACTIONS_DIRECT_POSTGRES"


@dataclass(frozen=True)
class PersistedNcaafSpreadForwardContext:
    artifact: MarginDistributionArtifact
    settled_events: tuple[dict[str, Any], ...]
    latest_training_event: datetime
    source_fingerprint: tuple[str | None, str | None, str | None, str | None]
    context_checksum: str
    built_at: str | None = None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _context_checksum(document: Mapping[str, Any]) -> str:
    return sha256(_canonical_json(document).encode("utf-8")).hexdigest()


def _source_fingerprint(
    replay_rows: Sequence[MarginTrainingRow],
    settled_events: Sequence[Mapping[str, Any]],
) -> tuple[str | None, str | None, str | None, str | None]:
    latest_feature = max(
        replay_rows,
        key=lambda row: (_dt(row.event_start_time), str(row.event_id)),
    )
    dated_games = [row for row in settled_events if row.get("event_start_time") is not None]
    latest_game = max(
        dated_games,
        key=lambda row: (_dt(row["event_start_time"]), str(row.get("event_id") or "")),
    )
    return (
        str(latest_feature.event_id),
        str(latest_feature.event_start_time),
        str(latest_game.get("event_id")),
        str(latest_game.get("event_start_time")),
    )


def _source_fingerprint_payload(
    fingerprint: tuple[str | None, str | None, str | None, str | None],
) -> dict[str, str | None]:
    return {
        "feature_event_id": fingerprint[0],
        "feature_event_start_time": fingerprint[1],
        "game_event_id": fingerprint[2],
        "game_event_start_time": fingerprint[3],
    }


def _fingerprint_from_payload(value: Mapping[str, Any]) -> tuple[str | None, str | None, str | None, str | None]:
    return (
        str(value.get("feature_event_id")) if value.get("feature_event_id") is not None else None,
        str(value.get("feature_event_start_time")) if value.get("feature_event_start_time") is not None else None,
        str(value.get("game_event_id")) if value.get("game_event_id") is not None else None,
        str(value.get("game_event_start_time")) if value.get("game_event_start_time") is not None else None,
    )


def context_document(
    *,
    artifact_payload: Mapping[str, Any],
    settled_events: Sequence[Mapping[str, Any]],
    source_fingerprint: tuple[str | None, str | None, str | None, str | None],
    latest_training_event: str,
) -> dict[str, Any]:
    return {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "sport": SPORT,
        "artifact_payload": dict(artifact_payload),
        "settled_events": [dict(row) for row in settled_events],
        "source_fingerprint": _source_fingerprint_payload(source_fingerprint),
        "latest_training_event": str(latest_training_event),
        "probability_publishable": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
    }


def _artifact_from_payload(payload: Mapping[str, Any]) -> MarginDistributionArtifact:
    names = {field.name for field in fields(MarginDistributionArtifact)}
    missing = sorted(name for name in names if name not in payload)
    if missing:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_CONTEXT_ARTIFACT_INVALID",
            "persisted spread context artifact is missing required fields: " + ",".join(missing),
        )
    raw = {name: payload[name] for name in names}
    for name in ("feature_names", "scaler_mean", "scaler_scale", "coefficients", "calibration_residuals"):
        raw[name] = tuple(raw[name])
    artifact = MarginDistributionArtifact(**raw)
    governed = artifact.payload()
    if (
        artifact.sport != SPORT
        or float(artifact.ridge_alpha) != RIDGE_ALPHA
        or governed.get("probability_publishable") is not False
        or governed.get("automatic_certification") is not False
        or governed.get("automatic_promotion") is not False
        or governed.get("can_execute") is not False
    ):
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_CONTEXT_ARTIFACT_INVALID",
            "persisted spread context artifact violates V17 research-only governance",
        )
    return artifact


def persisted_context_from_row(row: Mapping[str, Any]) -> PersistedNcaafSpreadForwardContext:
    if str(row.get("schema_version") or "") != CONTEXT_SCHEMA_VERSION:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_CONTEXT_ARTIFACT_SCHEMA_MISMATCH",
            "persisted NCAAF spread context schema is not supported",
        )
    if str(row.get("sport") or "").upper() != SPORT:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_CONTEXT_ARTIFACT_INVALID",
            "persisted spread context sport is not NCAAF",
        )
    for field_name in ("probability_publishable", "automatic_certification", "automatic_promotion", "can_execute"):
        if row.get(field_name) is not False:
            raise SpreadChallengerUnavailable(
                "SPREAD_FORWARD_CONTEXT_ARTIFACT_INVALID",
                f"persisted spread context governance field must remain false: {field_name}",
            )

    artifact_payload = row.get("artifact_payload")
    settled_events = row.get("settled_events")
    source_payload = row.get("source_fingerprint")
    latest_training_event = row.get("latest_training_event")
    if not isinstance(artifact_payload, Mapping) or not isinstance(settled_events, list) or not settled_events:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_CONTEXT_ARTIFACT_INVALID",
            "persisted spread context payload/history is missing",
        )
    if not isinstance(source_payload, Mapping) or latest_training_event is None:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_CONTEXT_ARTIFACT_INVALID",
            "persisted spread context source fingerprint is missing",
        )

    fingerprint = _fingerprint_from_payload(source_payload)
    document = context_document(
        artifact_payload=artifact_payload,
        settled_events=settled_events,
        source_fingerprint=fingerprint,
        latest_training_event=str(latest_training_event),
    )
    expected_checksum = _context_checksum(document)
    observed_checksum = str(row.get("context_checksum") or "")
    if observed_checksum != expected_checksum:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_CONTEXT_ARTIFACT_CHECKSUM_MISMATCH",
            "persisted NCAAF spread context checksum mismatch",
        )

    artifact = _artifact_from_payload(artifact_payload)
    if str(row.get("training_dataset_hash") or "") != artifact.training_dataset_hash:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_CONTEXT_ARTIFACT_INVALID",
            "persisted context training dataset hash disagrees with artifact",
        )
    if int(row.get("settled_event_count") or 0) != len(settled_events):
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_CONTEXT_ARTIFACT_INVALID",
            "persisted context settled-event count mismatch",
        )

    return PersistedNcaafSpreadForwardContext(
        artifact=artifact,
        settled_events=tuple(dict(item) for item in settled_events),
        latest_training_event=_dt(latest_training_event),
        source_fingerprint=fingerprint,
        context_checksum=observed_checksum,
        built_at=str(row.get("built_at")) if row.get("built_at") is not None else None,
    )


def load_latest_persisted_context(client: Any) -> PersistedNcaafSpreadForwardContext:
    try:
        rows = (
            client.table(CONTEXT_TABLE)
            .select(
                "context_checksum,schema_version,sport,model_family,feature_schema_version,"
                "training_dataset_hash,source_fingerprint,latest_training_event,artifact_payload,"
                "settled_events,settled_event_count,built_at,probability_publishable,"
                "automatic_certification,automatic_promotion,can_execute"
            )
            .eq("sport", SPORT)
            .order("built_at", desc=True)
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception as exc:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_CONTEXT_ARTIFACT_READ_FAILED",
            f"persisted NCAAF spread context read failed: {type(exc).__name__}",
        ) from exc
    if not rows:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_CONTEXT_ARTIFACT_UNAVAILABLE",
            "persisted NCAAF spread forward context has not been built",
        )
    return persisted_context_from_row(dict(rows[0]))


def _read_direct_training_rows(cur: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cur.execute(
        """
        select official_event_id,event_start_time,feature_as_of,feature_schema_version,
               model_family,features,source_manifest,market_features_used,can_execute
          from public.wow_d1_training_rows
         where sport=%s and model_family=%s and feature_schema_version=%s
         order by official_event_id
        """,
        (SPORT, NCAAF_PERSISTED_FEATURE_MODEL_FAMILY, NCAAF_PERSISTED_FEATURE_SCHEMA_VERSION),
    )
    feature_rows = [dict(row) for row in cur.fetchall()]
    cur.execute(
        """
        select training_game_id,official_event_id,season,event_start_time,home_team,away_team,
               home_points,away_points,result_source,result_source_timestamp,can_execute
          from public.wow_ncaaf_training_games
         order by official_event_id
        """
    )
    game_rows = [dict(row) for row in cur.fetchall()]
    return feature_rows, game_rows


def build_context_row(feature_rows: Sequence[Mapping[str, Any]], game_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    replay_rows = adapt_ncaaf_persisted_rows(feature_rows, game_rows)
    settled_events = _ncaaf_events_from_games(game_rows)
    if not replay_rows:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_TRAINING_UNAVAILABLE",
            "NCAAF spread replay rows are unavailable",
        )
    if not settled_events:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_HISTORY_UNAVAILABLE",
            "NCAAF settled sporting history is unavailable",
        )
    latest_feature = max(_dt(row.event_start_time) for row in replay_rows)
    latest_settled = max(_dt(row["event_start_time"]) for row in settled_events)
    if latest_feature != latest_settled:
        raise SpreadChallengerUnavailable(
            "SPREAD_REPLAY_PERSISTED_NCAAF_FEATURES_STALE",
            "immutable NCAAF dynamic team-state rows lag the latest settled training game",
        )

    artifact = fit_margin_distribution_artifact(
        replay_rows,
        sport=SPORT,
        min_rows=MIN_TRAIN_ROWS,
        ridge_alpha=RIDGE_ALPHA,
    )
    fingerprint = _source_fingerprint(replay_rows, settled_events)
    artifact_payload = artifact.payload()
    document = context_document(
        artifact_payload=artifact_payload,
        settled_events=settled_events,
        source_fingerprint=fingerprint,
        latest_training_event=latest_feature.isoformat(),
    )
    checksum = _context_checksum(document)
    return {
        "context_checksum": checksum,
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "sport": SPORT,
        "model_family": artifact.model_family,
        "feature_schema_version": artifact.feature_schema_version,
        "training_dataset_hash": artifact.training_dataset_hash,
        "source_fingerprint": _source_fingerprint_payload(fingerprint),
        "latest_training_event": latest_feature,
        "artifact_payload": artifact_payload,
        "settled_events": settled_events,
        "settled_event_count": len(settled_events),
        "builder": BUILDER_ID,
        "probability_publishable": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
    }


def persist_context_from_database_url(database_url: str) -> dict[str, Any]:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    if not str(database_url or "").strip():
        raise RuntimeError("WOW_SCOUT_DATABASE_URL_UNCONFIGURED")

    with psycopg.connect(database_url, connect_timeout=15, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("set statement_timeout = '300s'")
            feature_rows, game_rows = _read_direct_training_rows(cur)
            row = build_context_row(feature_rows, game_rows)
            cur.execute(
                f"""
                insert into public.{CONTEXT_TABLE}
                    (context_checksum,schema_version,sport,model_family,feature_schema_version,
                     training_dataset_hash,source_fingerprint,latest_training_event,artifact_payload,
                     settled_events,settled_event_count,builder,probability_publishable,
                     automatic_certification,automatic_promotion,can_execute)
                values
                    (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false,false,false,false)
                on conflict (context_checksum) do nothing
                returning context_checksum
                """,
                (
                    row["context_checksum"],
                    row["schema_version"],
                    row["sport"],
                    row["model_family"],
                    row["feature_schema_version"],
                    row["training_dataset_hash"],
                    Jsonb(row["source_fingerprint"]),
                    row["latest_training_event"],
                    Jsonb(row["artifact_payload"]),
                    Jsonb(row["settled_events"]),
                    row["settled_event_count"],
                    row["builder"],
                ),
            )
            inserted = cur.fetchone() is not None
        conn.commit()

    return {
        "status": "READY",
        "code": "SPREAD_FORWARD_CONTEXT_ARTIFACT_PERSISTED",
        "sport": SPORT,
        "context_checksum": row["context_checksum"],
        "training_dataset_hash": row["training_dataset_hash"],
        "latest_training_event": row["latest_training_event"].isoformat(),
        "settled_event_count": row["settled_event_count"],
        "inserted": inserted,
        "builder": BUILDER_ID,
        "probability_publishable": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    args = parser.parse_args()
    receipt = persist_context_from_database_url(os.environ.get("WOW_SCOUT_DATABASE_URL", ""))
    rendered = json.dumps(receipt, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
