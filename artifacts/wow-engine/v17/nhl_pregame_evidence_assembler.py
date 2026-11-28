"""Assemble fail-closed NHL Action pregame evidence for one upcoming game.

Composes existing identity, GameCenter capture, two-provider starter
confirmation, injury presence, OT/SO settlement vocabulary, and a
content-addressed source snapshot id. This module:

- never scores or publishes a probability
- never invents confirmed starters from GameCenter participation
- never ships a default trusted-provider registry (source-rights decision)
- never flips ``probability_publishable`` or ``can_execute``

Missing injury reports, missing/invalid dual-provider starter receipts, or a
failed official snapshot capture produce typed holds. A package that clears
every assembler gate remains research/Action-input evidence only until
independent source review and certification register the fitted candidate.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

import requests

from v17.nhl_certified_scoring_adapter import (
    NHLCertifiedScoringError,
    resolve_server_confirmed_goalies,
)
from v17.nhl_fitted_specialist import (
    FULL_GAME_SETTLEMENT,
    SETTLEMENT_CONTRACT,
)
from v17.nhl_pregame_gamecenter_capture import capture_nhl_pregame_gamecenter
from v17.nhl_starter_goalie_confirmation import TrustedProvider

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
DEFAULT_SETTLEMENT_BASIS = SETTLEMENT_CONTRACT
ACCEPTED_ASSEMBLER_SETTLEMENT = frozenset({FULL_GAME_SETTLEMENT, SETTLEMENT_CONTRACT})
# Same anchored schedule URL contract as nhl_pregame_gamecenter_capture.
_SCHEDULE_URL = re.compile(
    r"https://api-web\.nhle\.com/v1/club-schedule-season/[A-Z]{3}/\d{8}\Z"
)


def _held(
    blockers: Sequence[str],
    *,
    official_event_id: str | None = None,
    evidence: Mapping[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    codes = sorted({str(code) for code in blockers if str(code).strip()})
    return {
        "status": "HELD",
        "code": codes[0] if len(codes) == 1 else "MODEL_INPUTS_INSUFFICIENT",
        "blockers": codes,
        "sport": "NHL",
        "league": "NHL",
        "official_event_id": official_event_id,
        "sport_specific_evidence": dict(evidence or {}),
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "can_execute": CAN_EXECUTE,
        "derived_server_side": True,
        "fitted_specialist_invoked": False,
        "certification_claimed": False,
        **extra,
    }


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def assemble_nhl_pregame_action_evidence(
    *,
    resolved_identity: Mapping[str, Any],
    as_of_utc: datetime,
    starter_provider_receipts: Sequence[Any] | None = None,
    trusted_providers: Mapping[str, TrustedProvider] | None = None,
    attestation_key: bytes | None = None,
    injury_report: Mapping[str, Any] | None = None,
    settlement_basis: str = DEFAULT_SETTLEMENT_BASIS,
    get: Callable[..., Any] = requests.get,
    capture: Callable[..., Mapping[str, Any]] = capture_nhl_pregame_gamecenter,
) -> dict[str, Any]:
    """Build one-game Action evidence or return a typed hold package.

    ``trusted_providers`` and ``attestation_key`` must be supplied by the
    server-owned rights/ops path. With none supplied, starter confirmation
    holds closed — this assembler does not invent OFFICIAL providers.
    """
    if as_of_utc.tzinfo is None:
        raise ValueError("as_of_utc must be timezone-aware")
    now = as_of_utc.astimezone(timezone.utc)
    if not isinstance(resolved_identity, Mapping):
        return _held(["NHL_CANONICAL_IDENTITY_RECEIPT_MISSING"])

    event_id = str(resolved_identity.get("event_id") or "").strip()
    home = str(resolved_identity.get("home_team") or "").strip().upper()
    away = str(resolved_identity.get("away_team") or "").strip().upper()
    start_raw = resolved_identity.get("event_start_time")
    try:
        start = datetime.fromisoformat(str(start_raw or "").replace("Z", "+00:00"))
        if start.tzinfo is None:
            raise ValueError("naive")
        start = start.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return _held(["NHL_EVENT_TIME_INVALID"], official_event_id=event_id or None)

    # Identity verification is stamped at resolve time. Allow a tiny clock skew so
    # an as_of captured just before resolve does not false-hold the GameCenter gate.
    try:
        verified_at = datetime.fromisoformat(
            str(resolved_identity.get("identity_verified_at") or "").replace("Z", "+00:00")
        )
        if verified_at.tzinfo is not None:
            verified_at = verified_at.astimezone(timezone.utc)
            skew = (verified_at - now).total_seconds()
            if 0 < skew <= 2.0:
                now = verified_at
    except (TypeError, ValueError, OverflowError):
        pass

    blockers: list[str] = []
    if not event_id:
        blockers.append("NHL_EVENT_IDENTITY_UNVERIFIED")
    if start <= now:
        blockers.append("NHL_EVENT_NOT_PREGAME")
    if not isinstance(settlement_basis, str) or settlement_basis not in ACCEPTED_ASSEMBLER_SETTLEMENT:
        blockers.append("NHL_SETTLEMENT_BASIS_UNVERIFIED")
        settlement = DEFAULT_SETTLEMENT_BASIS
    else:
        settlement = settlement_basis

    capture_result = capture(
        event_id,
        resolved_identity=resolved_identity,
        as_of_utc=now,
        get=get,
    )
    content_sha = str(capture_result.get("source_content_sha256") or "").strip().lower()
    retrieved_at = str(capture_result.get("retrieved_at_utc") or "").strip()
    snapshot_ok = (
        str(capture_result.get("gamecenter_source_status") or "")
        == "OFFICIAL_PREGAME_CAPTURED_RESEARCH_ONLY"
        and len(content_sha) == 64
        and all(ch in "0123456789abcdef" for ch in content_sha)
        and bool(retrieved_at)
    )
    if not snapshot_ok:
        blockers.append(
            str(capture_result.get("code") or "NHL_SOURCE_SNAPSHOT_MISSING")
        )

    evidence: dict[str, Any] = {
        "official_event_id": event_id,
        "home_team": home,
        "away_team": away,
        "event_start_time_utc": _iso(start),
        "settlement_basis": settlement,
        "season": str(resolved_identity.get("season") or "").strip() or None,
    }
    if snapshot_ok:
        evidence["source_snapshot_id"] = "sha256:" + content_sha
        evidence["source_snapshot_timestamp"] = retrieved_at
        evidence["source_content_sha256"] = content_sha
        evidence["source_provider"] = "NHL_PUBLIC_WEB_API"
    else:
        blockers.append("NHL_SOURCE_SNAPSHOT_MISSING")

    neutral_site = resolved_identity.get("neutral_site")
    if isinstance(neutral_site, bool):
        evidence["neutral_site"] = neutral_site
    else:
        blockers.append("NHL_NEUTRAL_SITE_UNVERIFIED")

    if not isinstance(injury_report, Mapping) or not injury_report:
        blockers.append("NHL_INJURY_REPORT_UNAVAILABLE")
    else:
        evidence["injury_report"] = dict(injury_report)

    starter_blockers: list[str] = []
    if (
        not isinstance(trusted_providers, Mapping)
        or not trusted_providers
        or attestation_key is None
        or not isinstance(starter_provider_receipts, (list, tuple))
        or not starter_provider_receipts
    ):
        starter_blockers.append("NHL_STARTER_RECEIPTS_INCOMPLETE")
        if not isinstance(trusted_providers, Mapping) or not trusted_providers:
            starter_blockers.append("NHL_STARTER_SOURCE_NOT_TRUSTED")
    else:
        try:
            confirmed = resolve_server_confirmed_goalies(
                {**evidence, "starter_provider_receipts": starter_provider_receipts},
                now_utc=now,
                trusted_providers=trusted_providers,
                attestation_key=attestation_key,
            )
            evidence["goalie_status"] = str(confirmed["goalie_status"])
            evidence["goalie_identity_snapshot_id"] = str(
                confirmed["goalie_identity_snapshot_id"]
            )
            evidence["goalie_status_observed_at"] = str(confirmed["verified_at"])
            evidence["home_goalie_id"] = confirmed.get("home_goalie_id")
            evidence["away_goalie_id"] = confirmed.get("away_goalie_id")
            evidence["starter_provider_receipts"] = list(starter_provider_receipts)
            evidence["goalie_confirmation"] = {
                "derived_server_side": True,
                "provider_receipts": confirmed.get("provider_receipts"),
            }
        except NHLCertifiedScoringError as exc:
            starter_blockers.extend(exc.blockers)
            if exc.detail:
                evidence["starter_hold_detail"] = str(exc.detail)

    blockers.extend(starter_blockers)
    # GameCenter never confirms starters; keep that explicit when dual receipts absent.
    if "NHL_STARTING_GOALIE_NOT_CONFIRMED" not in blockers and (
        "goalie_status" not in evidence
        or str(evidence.get("goalie_status") or "").upper() != "CONFIRMED"
    ):
        if any(
            code.startswith("NHL_STARTER_") or code == "NHL_STARTING_GOALIE_NOT_CONFIRMED"
            for code in starter_blockers
        ):
            blockers.append("NHL_STARTING_GOALIE_NOT_CONFIRMED")

    unique = sorted(set(blockers))
    if unique:
        return _held(
            unique,
            official_event_id=event_id or None,
            evidence=evidence,
            gamecenter_capture_code=capture_result.get("code"),
            gamecenter_source_status=capture_result.get("gamecenter_source_status"),
        )

    return {
        "status": "ASSEMBLED",
        "code": "NHL_PREGAME_ACTION_EVIDENCE_ASSEMBLED",
        "blockers": [],
        "sport": "NHL",
        "league": "NHL",
        "official_event_id": event_id,
        "sport_specific_evidence": evidence,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "can_execute": CAN_EXECUTE,
        "derived_server_side": True,
        "fitted_specialist_invoked": False,
        "certification_claimed": False,
        "gamecenter_source_status": capture_result.get("gamecenter_source_status"),
        "assembled_at_utc": _iso(now),
    }


# Server-owned Action seams. Production never invents trusted providers, HMAC
# keys, injury reports, or starter receipts. Tests may install fixtures.
_ACTION_SEAMS: dict[str, Any] = {
    "trusted_providers": None,
    "attestation_key": None,
    "injury_report": None,
    "starter_provider_receipts": None,
    "get": None,
    "capture": None,
}


def install_nhl_pregame_action_seams(
    *,
    trusted_providers: Mapping[str, TrustedProvider] | None = None,
    attestation_key: bytes | None = None,
    injury_report: Mapping[str, Any] | None = None,
    starter_provider_receipts: Sequence[Any] | None = None,
    get: Callable[..., Any] | None = None,
    capture: Callable[..., Mapping[str, Any]] | None = None,
) -> None:
    """Install server-owned pregame Action seams (tests/ops only)."""
    _ACTION_SEAMS["trusted_providers"] = trusted_providers
    _ACTION_SEAMS["attestation_key"] = attestation_key
    _ACTION_SEAMS["injury_report"] = injury_report
    _ACTION_SEAMS["starter_provider_receipts"] = starter_provider_receipts
    _ACTION_SEAMS["get"] = get
    _ACTION_SEAMS["capture"] = capture


def reset_nhl_pregame_action_seams() -> None:
    """Clear Action seams so production fail-closed defaults apply."""
    for key in list(_ACTION_SEAMS):
        _ACTION_SEAMS[key] = None


def _schedule_url_ok(value: Any) -> bool:
    token = str(value or "").strip()
    return isinstance(value, str) and _SCHEDULE_URL.fullmatch(token) is not None


def _identity_receipt_complete(identity: Mapping[str, Any]) -> bool:
    """True when GameCenter capture can accept this as a canonical receipt."""
    match_sha = str(identity.get("identity_match_sha256") or "").strip().lower()
    return (
        str(identity.get("identity_provider") or "") == "NHL_PUBLIC_WEB_API"
        and _schedule_url_ok(identity.get("identity_source"))
        and len(match_sha) == 64
        and all(ch in "0123456789abcdef" for ch in match_sha)
        and bool(str(identity.get("event_id") or "").strip())
        and identity.get("can_execute") is False
        and identity.get("prediction_authority") is False
        and identity.get("market_features_used") is False
    )


def _resolved_identity_from_request(req: Any) -> dict[str, Any]:
    """Build assembler identity from request + server-owned NHL resolution.

    Discovery evidence often carries ``canonical_identity_*`` fields without the
    schedule URL GameCenter capture requires. Prefer an explicit receipt when
    complete; otherwise resolve through ``resolve_nhl_current_event_identity``
    rather than trusting incomplete caller/discovery keys.
    """
    evidence = getattr(req, "sport_specific_evidence", None)
    evidence_map = dict(evidence) if isinstance(evidence, Mapping) else {}
    receipt = evidence_map.get("canonical_identity_receipt")
    if isinstance(receipt, Mapping):
        # Nested Multiscout/discovery receipt already in assembler shape.
        nested = {
            **dict(receipt),
            "market_features_used": False,
            "prediction_authority": False,
            "can_execute": CAN_EXECUTE,
        }
        if evidence_map.get("neutral_site") is not None and "neutral_site" not in nested:
            nested["neutral_site"] = evidence_map.get("neutral_site")
        if evidence_map.get("season") is not None and "season" not in nested:
            nested["season"] = evidence_map.get("season")
        if _identity_receipt_complete(nested):
            return nested

    event_id = str(
        evidence_map.get("event_id")
        or evidence_map.get("official_event_id")
        or evidence_map.get("canonical_event_id")
        or getattr(req, "official_event_id", "")
        or ""
    ).strip()
    start = (
        getattr(req, "event_start_time_utc", None)
        or evidence_map.get("event_start_time_utc")
        or evidence_map.get("event_start_time")
        or evidence_map.get("canonical_identity_event_start_utc")
    )
    verified_at = (
        evidence_map.get("identity_verified_at")
        or evidence_map.get("canonical_identity_verified_at")
        or getattr(req, "latest_material_update_timestamp", None)
    )
    identity = {
        "event_id": event_id,
        "home_team": str(
            getattr(req, "home_team", "") or evidence_map.get("home_team") or ""
        ).strip().upper(),
        "away_team": str(
            getattr(req, "away_team", "") or evidence_map.get("away_team") or ""
        ).strip().upper(),
        "event_start_time": start,
        "identity_verified_at": verified_at,
        "neutral_site": evidence_map.get("neutral_site"),
        "season": evidence_map.get("season") or getattr(req, "season", None),
        "market_features_used": False,
        "prediction_authority": False,
        "can_execute": CAN_EXECUTE,
    }
    # Map discovery canonical_* aliases into the assembler receipt shape.
    if "identity_provider" in evidence_map:
        identity["identity_provider"] = evidence_map["identity_provider"]
    elif evidence_map.get("canonical_identity_source") == "NHL_PUBLIC_WEB_API":
        # Discovery stores the provider name under canonical_identity_source.
        identity["identity_provider"] = "NHL_PUBLIC_WEB_API"
    if "identity_source" in evidence_map:
        identity["identity_source"] = evidence_map["identity_source"]
    if "identity_match_sha256" in evidence_map:
        identity["identity_match_sha256"] = evidence_map["identity_match_sha256"]
    elif "canonical_identity_match_sha256" in evidence_map:
        identity["identity_match_sha256"] = evidence_map["canonical_identity_match_sha256"]

    if _identity_receipt_complete(identity):
        return identity

    # Server-owned resolve: discovery often drops the schedule URL, so refresh.
    from v17.nhl_event_identity import (
        NHLEventIdentityError,
        resolve_nhl_current_event_identity,
    )

    session = _ACTION_SEAMS.get("get")
    try:
        kwargs: dict[str, Any] = {
            "event_start_time": str(start or ""),
            "home_team": identity["home_team"],
            "away_team": identity["away_team"],
        }
        if callable(session):
            # resolve_nhl_current_event_identity accepts session.get-compatible objects.
            class _SessionAdapter:
                def get(self, *args: Any, **kwargs: Any) -> Any:
                    return session(*args, **kwargs)

            kwargs["session"] = _SessionAdapter()
        resolution = resolve_nhl_current_event_identity(**kwargs)
    except NHLEventIdentityError as exc:
        from v17.nhl_fitted_specialist import NHLFittedSpecialistError

        raise NHLFittedSpecialistError(
            "MODEL_INPUTS_INSUFFICIENT",
            [str(getattr(exc, "code", None) or "NHL_CANONICAL_IDENTITY_RECEIPT_MISMATCH")],
            {"reason": "NHL_SERVER_IDENTITY_RESOLVE_FAILED"},
        ) from exc
    except Exception as exc:  # noqa: BLE001 - typed hold, never invent identity
        from v17.nhl_fitted_specialist import NHLFittedSpecialistError

        raise NHLFittedSpecialistError(
            "MODEL_INPUTS_INSUFFICIENT",
            ["NHL_CANONICAL_IDENTITY_RECEIPT_MISMATCH"],
            {"reason": type(exc).__name__},
        ) from exc

    resolved = {
        "event_id": str(resolution.get("event_id") or "").strip(),
        "home_team": str(resolution.get("home_team") or identity["home_team"]).strip().upper(),
        "away_team": str(resolution.get("away_team") or identity["away_team"]).strip().upper(),
        "event_start_time": resolution.get("event_start_time") or start,
        "identity_provider": resolution.get("identity_provider"),
        "identity_source": resolution.get("identity_source"),
        "identity_match_sha256": resolution.get("identity_match_sha256"),
        "identity_verified_at": resolution.get("identity_verified_at"),
        "neutral_site": identity.get("neutral_site"),
        "season": identity.get("season"),
        "market_features_used": False,
        "prediction_authority": False,
        "can_execute": CAN_EXECUTE,
    }
    if not _identity_receipt_complete(resolved):
        from v17.nhl_fitted_specialist import NHLFittedSpecialistError

        raise NHLFittedSpecialistError(
            "MODEL_INPUTS_INSUFFICIENT",
            ["NHL_CANONICAL_IDENTITY_RECEIPT_MISMATCH"],
            {"reason": "NHL_SERVER_IDENTITY_RECEIPT_INCOMPLETE"},
        )
    return resolved


def hydrate_nhl_team_event_request_evidence(
    req: Any,
    *,
    as_of_utc: datetime | None = None,
) -> dict[str, Any]:
    """Run the Action assembler against a TeamEventRequest before scoring.

    Replaces caller ``sport_specific_evidence`` only when the assembler returns
    ``ASSEMBLED``. A ``HELD`` package raises ``NHLFittedSpecialistError`` with
    the typed blockers. Missing server seams remain fail-closed — this never
    invents trusted providers, attestation keys, or injury reports.
    """
    from v17.nhl_fitted_specialist import NHLFittedSpecialistError

    now = as_of_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("as_of_utc must be timezone-aware")

    kwargs: dict[str, Any] = {
        "resolved_identity": _resolved_identity_from_request(req),
        "as_of_utc": now,
        "trusted_providers": _ACTION_SEAMS.get("trusted_providers"),
        "attestation_key": _ACTION_SEAMS.get("attestation_key"),
        "injury_report": _ACTION_SEAMS.get("injury_report"),
        "starter_provider_receipts": _ACTION_SEAMS.get("starter_provider_receipts"),
    }
    get_fn = _ACTION_SEAMS.get("get")
    capture_fn = _ACTION_SEAMS.get("capture")
    if callable(get_fn):
        kwargs["get"] = get_fn
    if callable(capture_fn):
        kwargs["capture"] = capture_fn

    package = assemble_nhl_pregame_action_evidence(**kwargs)
    if str(package.get("status") or "") != "ASSEMBLED":
        blockers = package.get("blockers")
        codes = (
            sorted({str(code) for code in blockers if str(code).strip()})
            if isinstance(blockers, (list, tuple))
            else ["NHL_PREGAME_ACTION_EVIDENCE_HELD"]
        )
        raise NHLFittedSpecialistError(
            "MODEL_INPUTS_INSUFFICIENT",
            codes or ["NHL_PREGAME_ACTION_EVIDENCE_HELD"],
            {
                "assembler_status": package.get("status"),
                "assembler_code": package.get("code"),
                "official_event_id": package.get("official_event_id"),
            },
        )

    evidence = package.get("sport_specific_evidence")
    if not isinstance(evidence, Mapping) or not evidence:
        raise NHLFittedSpecialistError(
            "MODEL_INPUTS_INSUFFICIENT",
            ["NHL_PREGAME_ACTION_EVIDENCE_EMPTY"],
        )
    # Assign assembled evidence onto the request object used by the scorer.
    try:
        req.sport_specific_evidence = dict(evidence)
    except Exception as exc:  # noqa: BLE001 - immutable request objects fail closed
        raise NHLFittedSpecialistError(
            "MODEL_INPUTS_INSUFFICIENT",
            ["NHL_PREGAME_ACTION_EVIDENCE_ASSIGNMENT_FAILED"],
            {"reason": type(exc).__name__},
        ) from exc
    return package


__all__ = [
    "ACCEPTED_ASSEMBLER_SETTLEMENT",
    "CAN_EXECUTE",
    "DEFAULT_SETTLEMENT_BASIS",
    "PROBABILITY_PUBLISHABLE",
    "TrustedProvider",
    "assemble_nhl_pregame_action_evidence",
    "hydrate_nhl_team_event_request_evidence",
    "install_nhl_pregame_action_seams",
    "reset_nhl_pregame_action_seams",
]
