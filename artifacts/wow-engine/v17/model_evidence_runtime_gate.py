"""Read-only fail-closed registry triad boundary for V17 fitted-model paths.

The registry reader is a separate source-owned component (PR #2537 follow-up).
Its absence must never become a passing model check, inferred certification,
a model-probability fallback, or permission to execute. This module cannot
create or verify a fitted probability; it only checks typed evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from v17.model_evidence_bundle_contract import ModelBundleScope

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

# Distinguish absent DB, absent reader, failed read and invalid contract from
# validly read but incomplete/invalid evidence. Do not label any as
# MODEL_UNAVAILABLE: specialist capability and evidence integrity differ.
TRIAD_DB_UNAVAILABLE = "MODEL_BUNDLE_DB_UNAVAILABLE"
TRIAD_READER_UNAVAILABLE = "MODEL_BUNDLE_READER_UNAVAILABLE"
TRIAD_READER_FAILED = "MODEL_BUNDLE_READER_READ_FAILED"
TRIAD_READER_INVALID = "MODEL_BUNDLE_READER_CONTRACT_INVALID"
TRIAD_EVIDENCE_UNVERIFIED = "MODEL_BUNDLE_EVIDENCE_UNVERIFIED"


@dataclass(frozen=True)
class TriadBoundaryVerdict:
    status: str
    blocker: str | None
    blockers: tuple[str, ...]
    model_invoked: bool = False
    probability_publishable: bool = False
    rank_eligible: bool = False
    can_execute: bool = False
    terminal_authority: str = TERMINAL_AUTHORITY

    def as_hold(self) -> dict[str, Any]:
        return {
            "code": self.blocker or TRIAD_EVIDENCE_UNVERIFIED,
            "blocker_code": self.blocker or TRIAD_EVIDENCE_UNVERIFIED,
            "blockers": list(self.blockers),
            "failed_contract_scope": ["MODEL_EVIDENCE_TRIAD"],
            "model_invoked": False,
            "scoring_attempted": False,
            "model_evaluated": False,
            "probability_publishable": False,
            "rank_eligible": False,
            "global_terminal_authority": TERMINAL_AUTHORITY,
            "can_execute": False,
        }


def _field(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _failure(code: str, blockers: tuple[str, ...] = ()) -> TriadBoundaryVerdict:
    return TriadBoundaryVerdict(
        status="BLOCKED", blocker=code, blockers=blockers or (code,),
    )


def assess_scoring_boundary(
    db: Any,
    scope: ModelBundleScope,
    *,
    reader: Any = None,
) -> TriadBoundaryVerdict:
    """Call the exact-model registry reader; do not infer its verification.

    Caller must supply a scope built from server-owned certified artifact
    identity, never request-side probabilities or specialist guesses. A complete
    bundle means ready for independent review, NOT automatically publishable.
    The scorer's existing certification, bounds and terminal reducer still own
    final publication. Every malformed/missing reader response fails closed.
    """
    if not isinstance(scope, ModelBundleScope):
        return _failure(TRIAD_READER_INVALID)
    if db is None:
        return _failure(TRIAD_DB_UNAVAILABLE)
    if reader is None:
        try:
            from v17.model_evidence_registry_readers import (
                assess_registry_model_bundle as reader,
            )
        except ImportError:
            return _failure(TRIAD_READER_UNAVAILABLE)
    if not callable(reader):
        return _failure(TRIAD_READER_INVALID)
    try:
        result = reader(db, scope)
    except Exception:  # no untrusted query text or credential leak into receipt
        return _failure(TRIAD_READER_FAILED)

    # Freebuff's reader contract includes a verdict and source/missing-field
    # metadata. Accept a direct contract verdict or the nested "assessment"
    # form, but never a bare PASS/boolean or claimed verified registry column.
    assessment = _field(result, "assessment", result)
    kind = _field(assessment, "model_kind")
    sport = _field(assessment, "sport")
    family = _field(assessment, "model_family")
    version = _field(assessment, "model_artifact_version")
    status = _field(assessment, "status")
    complete = _field(assessment, "bundle_complete")
    blockers = _field(assessment, "blockers")
    if (
        kind != scope.model_kind or sport != scope.sport
        or family != scope.model_family
        or version != scope.model_artifact_version
        or not isinstance(blockers, (list, tuple))
        or not all(isinstance(v, str) and v for v in blockers)
        or type(complete) is not bool
        or status not in ("READY_FOR_INDEPENDENT_REVIEW", "BLOCKED")
    ):
        return _failure(TRIAD_READER_INVALID)
    if complete is not True or blockers or status != "READY_FOR_INDEPENDENT_REVIEW":
        codes = tuple(blockers) if blockers else (TRIAD_EVIDENCE_UNVERIFIED,)
        return _failure(codes[0], codes)

    # A triad ready for independent review cannot authorize a wager/model
    # publication. A caller must still apply its independent V17 gates.
    return TriadBoundaryVerdict(
        status="READY_FOR_INDEPENDENT_REVIEW",
        blocker=None,
        blockers=(),
    )


TRIAD_CERTIFICATION_UNAVAILABLE = "MODEL_BUNDLE_CERTIFICATION_UNAVAILABLE"
TRIAD_CERTIFICATION_READ_FAILED = "MODEL_BUNDLE_CERTIFICATION_READ_FAILED"
TRIAD_CERTIFICATION_AMBIGUOUS = "MODEL_BUNDLE_CERTIFICATION_AMBIGUOUS"
TRIAD_SCOPE_IDENTITY_UNAVAILABLE = "MODEL_BUNDLE_SCOPE_IDENTITY_UNAVAILABLE"


def _db_from_event_api(event_api: Any) -> Any:
    """Return only the scorer's own authorized Supabase client."""
    get_client = getattr(event_api, "get_client", None)
    if not callable(get_client):
        return None
    return get_client()


def assess_team_event_boundary(
    req: Any, *, event_api: Any, registration: Any,
    reader: Any = None,
) -> TriadBoundaryVerdict:
    """Resolve exact certified identity server-side before team-event scoring.

    No client-supplied model name or version is accepted. Zero or ambiguous
    certification rows block rather than selecting a convenient version.
    """
    try:
        db = _db_from_event_api(event_api)
    except Exception:
        return _failure(TRIAD_CERTIFICATION_READ_FAILED)
    if db is None:
        return _failure(TRIAD_DB_UNAVAILABLE)
    sport = str(getattr(registration, "sport", "") or "")
    specialist = str(getattr(registration, "controlling_specialist", "") or "")
    if not sport or not specialist:
        return _failure(TRIAD_SCOPE_IDENTITY_UNAVAILABLE)
    try:
        response = (
            db.table("wow_team_event_specialist_certifications")
            .select("sport,controlling_specialist,model_family,model_version,"
                    "feature_schema_version,artifact_sha256,"
                    "certification_status,independent_verification_status")
            .eq("sport", sport)
            .eq("controlling_specialist", specialist)
            .eq("certification_status", "CERTIFIED")
            .execute()
        )
        rows = _field(response, "data")
    except Exception:
        return _failure(TRIAD_CERTIFICATION_READ_FAILED)
    if not isinstance(rows, list):
        return _failure(TRIAD_CERTIFICATION_READ_FAILED)
    if len(rows) > 1:
        return _failure(TRIAD_CERTIFICATION_AMBIGUOUS)
    if not rows or not isinstance(rows[0], Mapping):
        return _failure(TRIAD_CERTIFICATION_UNAVAILABLE)
    cert = rows[0]
    if (
        cert.get("sport") != sport
        or cert.get("controlling_specialist") != specialist
        or cert.get("certification_status") != "CERTIFIED"
        or cert.get("independent_verification_status") != "PASS"
    ):
        return _failure(TRIAD_CERTIFICATION_UNAVAILABLE)
    model_family = cert.get("model_family")
    version = cert.get("model_version")
    schema = cert.get("feature_schema_version")
    artifact_hash = cert.get("artifact_sha256")
    if (
        not all(isinstance(x, str) and x.strip()
                for x in (model_family, version, schema))
        or not isinstance(artifact_hash, str)
        or len(artifact_hash) != 64
        or any(ch not in "0123456789abcdef" for ch in artifact_hash.lower())
    ):
        return _failure(TRIAD_SCOPE_IDENTITY_UNAVAILABLE)
    event_id = str(getattr(req, "official_event_id", "") or "")
    event_at = str(getattr(req, "event_start_time_utc", "") or "")
    if not event_id or not event_at:
        return _failure(TRIAD_SCOPE_IDENTITY_UNAVAILABLE)

    from datetime import datetime, timezone
    scope = ModelBundleScope(
        model_kind="TEAM_EVENT",
        sport=sport,
        model_family=model_family,
        model_artifact_version=version,
        feature_schema_version=schema,
        official_event_id=event_id,
        event_start_time=event_at,
        prediction_timestamp=datetime.now(timezone.utc).isoformat(),
        market_family="OUTRIGHT_WINNER",
        latest_material_update_timestamp=getattr(
            req, "latest_material_update_timestamp", None,
        ),
    )
    return assess_scoring_boundary(db, scope, reader=reader)


def assess_prop_action_boundary(
    row: Any, *, market_api: Any, route: Mapping[str, Any],
    canonical_stat: str, reader: Any = None,
) -> TriadBoundaryVerdict:
    """Require an exact trusted prop identity before Action-side scoring.

    The Action's player name is NOT a canonical player ID. Registry or
    server-owned route must provide player_id. Never convert 7+ to 7.0.
    """
    if not isinstance(route, Mapping):
        return _failure(TRIAD_SCOPE_IDENTITY_UNAVAILABLE)
    player_id = route.get("player_id")
    model_family = route.get("model_family")
    model_version = route.get("model_artifact_version")
    schema_version = route.get("feature_schema_version")
    if not all(isinstance(x, str) and x.strip()
               for x in (player_id, model_family, model_version, schema_version)):
        return _failure(TRIAD_SCOPE_IDENTITY_UNAVAILABLE)
    # The existing Action schema carries a numeric line. Its exact textual
    # identity (such as "7+") is unrepresentable; reject non-numeric lines
    # rather than silently substitute a neighbouring numeric threshold.
    line = getattr(row, "line", None)
    if type(line) not in (float, int):
        return _failure(TRIAD_SCOPE_IDENTITY_UNAVAILABLE)
    try:
        db = market_api.prod.get_client()
    except Exception:
        return _failure(TRIAD_DB_UNAVAILABLE)
    if db is None:
        return _failure(TRIAD_DB_UNAVAILABLE)
    from datetime import datetime, timezone
    period = market_api._prop_period(canonical_stat)
    scope = ModelBundleScope(
        model_kind="PLAYER_PROP",
        sport=str(getattr(row, "sport", "")).upper(),
        model_family=model_family,
        model_artifact_version=model_version,
        feature_schema_version=schema_version,
        official_event_id=str(getattr(row, "event_id", "") or ""),
        event_start_time=str(getattr(row, "event_start_time", "") or ""),
        prediction_timestamp=datetime.now(timezone.utc).isoformat(),
        market_family="PLAYER_PROP",
        player_id=player_id,
        stat_type=canonical_stat,
        period=period,
        line=str(line),
        direction=str(getattr(row, "direction", "") or "").upper(),
    )
    return assess_scoring_boundary(db, scope, reader=reader)


__all__ = [
    "CAN_EXECUTE", "TERMINAL_AUTHORITY", "TriadBoundaryVerdict",
    "TRIAD_DB_UNAVAILABLE", "TRIAD_READER_UNAVAILABLE",
    "TRIAD_READER_FAILED", "TRIAD_READER_INVALID",
    "TRIAD_EVIDENCE_UNVERIFIED", "assess_scoring_boundary",
    "TRIAD_CERTIFICATION_UNAVAILABLE", "TRIAD_CERTIFICATION_READ_FAILED",
    "TRIAD_CERTIFICATION_AMBIGUOUS", "TRIAD_SCOPE_IDENTITY_UNAVAILABLE",
    "assess_team_event_boundary", "assess_prop_action_boundary",
]
