"""FastAPI persistence boundary for paired Core Intelligence shadow evaluation."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from fastapi import Depends, FastAPI

from v17.core_intelligence_shadow_lab import evaluate_shadow_package

CAN_EXECUTE = False


class ShadowLabBoundaryError(RuntimeError):
    def __init__(self, boundary: str, error: BaseException) -> None:
        super().__init__(f"{boundary}: {type(error).__name__}")
        self.boundary = boundary
        self.error_type = type(error).__name__
        self.__cause__ = error


def _db_call(boundary: str, call: Callable[[], Any]) -> Any:
    try:
        return call()
    except ShadowLabBoundaryError:
        raise
    except Exception as exc:
        raise ShadowLabBoundaryError(boundary, exc) from exc


def evaluate_and_persist_shadow_package(db: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    challenger_id = str(payload.get("challenger_id") or "").strip()
    target_key = str(payload.get("target_key") or "").strip()
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("SHADOW_ROWS_LIST_REQUIRED")
    evidence, audit, review = evaluate_shadow_package(
        challenger_id=challenger_id,
        target_key=target_key,
        rows=rows,
        source_review_pass=bool(payload.get("source_review_pass")),
        certification_replay_pass=bool(payload.get("certification_replay_pass")),
        min_holdout_n=int(payload.get("min_holdout_n") or 100),
        min_brier_improvement=float(payload.get("min_brier_improvement") or 0.01),
    )
    _db_call(
        "wow_intelligence_shadow_rows.append",
        lambda: db.table("wow_intelligence_shadow_rows").upsert(
            [row.as_dict() for row in evidence],
            on_conflict="shadow_row_id",
            ignore_duplicates=True,
        ).execute(),
    )
    _db_call(
        "wow_intelligence_promotion_reviews.append_shadow_review",
        lambda: db.table("wow_intelligence_promotion_reviews").upsert(
            review.as_dict(),
            on_conflict="review_id",
            ignore_duplicates=True,
        ).execute(),
    )
    return {
        "status": "SHADOW_EVALUATION_COMPLETE",
        "audit": audit.as_dict(),
        "promotion_review": review.as_dict(),
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def install_shadow_lab_routes(
    app: FastAPI,
    *,
    get_client_fn: Callable[[], Any],
    auth_dependency: Any | None = None,
) -> bool:
    if getattr(app.state, "v17_shadow_lab_routes_installed", False):
        return True
    dependencies = [Depends(auth_dependency)] if callable(auth_dependency) else []

    @app.post(
        "/v17/intelligence/challenger-lab/shadow-evaluate",
        operation_id="evaluateWowV17ShadowChallenger",
        dependencies=dependencies,
    )
    def shadow_evaluate(payload: dict[str, Any]):
        return evaluate_and_persist_shadow_package(get_client_fn(), payload)

    app.state.v17_shadow_lab_routes_installed = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "ShadowLabBoundaryError",
    "evaluate_and_persist_shadow_package",
    "install_shadow_lab_routes",
]
