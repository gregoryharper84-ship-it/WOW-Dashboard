"""Celery worker owner for NCAAF spread-forward serving-cache builds."""
from __future__ import annotations

from agent_runtime.queue import celery_app


def _transient(exc: Exception) -> bool:
    code = str(getattr(exc, "code", "") or "")
    return type(exc).__name__ == "ReadTimeout" or code in {
        "PGRST002",
        "SPREAD_FORWARD_SERVING_CACHE_REDIS_UNAVAILABLE",
        "SPREAD_FORWARD_SERVING_CACHE_SOURCE_CHANGED_DURING_BUILD",
    }


@celery_app.task(
    bind=True,
    name="wow.v17.build_ncaaf_spread_serving_cache",
    acks_late=True,
    max_retries=5,
)
def build_ncaaf_spread_serving_cache(self):
    from ledger import get_client
    from v17.spread_forward_serving_cache import (
        acquire_build_lock,
        load_ready_context,
        persist_ready_context,
        release_build_lock,
    )
    from v17.spread_forward_shadow import (
        MIN_TRAIN_ROWS,
        RIDGE_ALPHA,
        _dt,
        _ncaaf_forward_source_fingerprint,
        _ncaaf_loaded_source_fingerprint,
        load_ncaaf_forward_context,
    )
    from v17.spread_margin_forward_fit import fit_margin_distribution_artifact

    redis = None
    token = None
    try:
        redis, token = acquire_build_lock()
        if redis is None:
            return {
                "status": "BUILD_ALREADY_IN_PROGRESS",
                "code": "SPREAD_FORWARD_SERVING_CACHE_BUILD_IN_PROGRESS",
                "probability_publishable": False,
                "can_execute": False,
            }

        client = get_client()
        before = _ncaaf_forward_source_fingerprint(client)
        try:
            ready = load_ready_context(before)
        except Exception as exc:
            if getattr(exc, "code", None) != "SPREAD_FORWARD_SERVING_CACHE_BUILD_PENDING":
                raise
        else:
            return {
                "status": "READY",
                "code": "SPREAD_FORWARD_SERVING_CACHE_ALREADY_READY",
                "training_dataset_hash": ready["artifact"].training_dataset_hash,
                "probability_publishable": False,
                "can_execute": False,
            }

        replay_rows, settled_events = load_ncaaf_forward_context(client)
        loaded_fingerprint = _ncaaf_loaded_source_fingerprint(replay_rows, settled_events)
        if tuple(loaded_fingerprint) != tuple(before):
            from v17.spread_margin_challenger import SpreadChallengerUnavailable
            raise SpreadChallengerUnavailable(
                "SPREAD_FORWARD_SERVING_CACHE_SOURCE_CHANGED_DURING_BUILD",
                "NCAAF spread source changed while the worker loaded the immutable corpus",
            )

        artifact = fit_margin_distribution_artifact(
            replay_rows,
            sport="NCAAF",
            min_rows=MIN_TRAIN_ROWS,
            ridge_alpha=RIDGE_ALPHA,
        )
        after = _ncaaf_forward_source_fingerprint(client)
        if tuple(after) != tuple(before):
            from v17.spread_margin_challenger import SpreadChallengerUnavailable
            raise SpreadChallengerUnavailable(
                "SPREAD_FORWARD_SERVING_CACHE_SOURCE_CHANGED_DURING_BUILD",
                "NCAAF spread source changed while the worker fitted the immutable artifact",
            )

        latest_training_event = max(_dt(row.event_start_time) for row in replay_rows).isoformat()
        return persist_ready_context(
            artifact=artifact,
            settled_events=settled_events,
            source_fingerprint=before,
            latest_training_event=latest_training_event,
        )
    except Exception as exc:
        if _transient(exc):
            raise self.retry(exc=exc, countdown=min(120, 20 * (int(self.request.retries) + 1)))
        raise
    finally:
        release_build_lock(redis, token)


__all__ = ["build_ncaaf_spread_serving_cache"]
