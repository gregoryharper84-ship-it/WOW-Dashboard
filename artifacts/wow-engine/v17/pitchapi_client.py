"""PitchAPI authenticated transport adapter for WOW V17 soccer research.

Transport contract (issue #2880 / WOW-PITCHAPI-20261126):

- HTTPS origin pinned to ``https://api.pitchapi.dev``
- ``GET /v1/leagues`` with no trailing slash
- ``X-API-KEY`` from stripped ``PITCHAPI_API_KEY`` and ``User-Agent: wow-engine/1.0``
- 15s timeout; redirects refused (no credential forwarding)
- bounded response body; schema-validated envelope
- typed HTTP errors including 403 and 429 with ``Retry-After``
- no paid retries; evidence only; ``can_execute=false``

This adapter never persists raw provider payloads, never fits or certifies a
model, and never substitutes PitchAPI output for OpenFootball or any other
controlling specialist. Upstream storage/training rights remain subject to
independent review (#204 / #2629).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlencode, urlsplit

from v17.native_research.extract import ResultRow, LEAGUE_NAMES
from v17.native_research.verify import cross_check, results_freshness

PITCHAPI_ORIGIN = "https://api.pitchapi.dev"
LEAGUES_PATH = "/v1/leagues"
USER_AGENT = "wow-engine/1.0"
DEFAULT_TIMEOUT_S = 15.0
MAX_RESPONSE_BYTES = 2_000_000
MAX_RETRY_AFTER_S = 300
CAN_EXECUTE = False
PROVIDER = "PITCHAPI"
SCHEMA_VERSION = "WOW_PITCHAPI_TRANSPORT_V1"
LEAGUE_ID_RE = re.compile(r"^l_[A-Za-z0-9]{6}$")

# WOW soccer lane codes -> PitchAPI catalogue name (case-insensitive match).
WOW_TO_PITCHAPI_LEAGUE_NAME = {
    "EPL": "Premier League",
    "LALIGA": "La Liga",
    "BUNDESLIGA": "Bundesliga",
    "SERIE_A": "Serie A",
    "LIGUE_1": "Ligue 1",
}

TransportFn = Callable[[urllib.request.Request, float], "PitchAPIRawResponse"]


class PitchAPIError(RuntimeError):
    """Typed PitchAPI transport failure. Never embeds credential material."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int | None = None,
        retry_after_seconds: int | None = None,
        request_id: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds
        self.request_id = request_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "http_status": self.http_status,
            "retry_after_seconds": self.retry_after_seconds,
            "request_id": self.request_id,
            "provider": PROVIDER,
            "can_execute": CAN_EXECUTE,
            "prediction_authority": False,
        }


@dataclass(frozen=True)
class PitchAPIRawResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]
    final_url: str


@dataclass(frozen=True)
class LeagueSummary:
    id: str
    name: str
    country_code: str | None
    seasons: tuple[str, ...]
    is_free: bool | None


def credential_class(api_key: str | None) -> str:
    """Classify key prefix only. Never returns key material."""
    key = (api_key or "").strip()
    if not key:
        return "MISSING"
    if key.startswith("pk_test_"):
        return "TEST"
    if key.startswith("pk_live_"):
        return "LIVE"
    return "UNKNOWN"


def _now_iso(now: datetime | None = None) -> str:
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _retry_after_seconds(value: object) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        seconds = int(text)
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = int((when - datetime.now(timezone.utc)).total_seconds())
    return max(0, min(seconds, MAX_RETRY_AFTER_S))


def _header_map(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    if hasattr(headers, "items"):
        return {str(k).lower(): str(v) for k, v in headers.items()}
    return {}


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001,N802
        request_id = None
        if headers is not None:
            request_id = headers.get("X-Request-Id") or headers.get("x-request-id")
        raise PitchAPIError(
            "PITCHAPI_REDIRECT_REFUSED",
            f"PitchAPI refused redirect HTTP {code} to prevent credential forwarding.",
            http_status=int(code),
            request_id=request_id,
        )


def _default_transport(request: urllib.request.Request, timeout_s: float) -> PitchAPIRawResponse:
    opener = urllib.request.build_opener(_RefuseRedirects())
    try:
        with opener.open(request, timeout=timeout_s) as response:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise PitchAPIError(
                        "PITCHAPI_RESPONSE_TOO_LARGE",
                        f"PitchAPI response exceeded {MAX_RESPONSE_BYTES} bytes.",
                        http_status=getattr(response, "status", None),
                        request_id=_header_map(getattr(response, "headers", None)).get("x-request-id"),
                    )
                chunks.append(chunk)
            body = b"".join(chunks)
            status = int(getattr(response, "status", 200))
            headers = _header_map(getattr(response, "headers", None))
            final_url = str(getattr(response, "geturl", lambda: request.full_url)())
            return PitchAPIRawResponse(status=status, body=body, headers=headers, final_url=final_url)
    except PitchAPIError:
        raise
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read(MAX_RESPONSE_BYTES + 1)
        except Exception:  # noqa: BLE001
            body = b""
        if len(body) > MAX_RESPONSE_BYTES:
            raise PitchAPIError(
                "PITCHAPI_RESPONSE_TOO_LARGE",
                f"PitchAPI error body exceeded {MAX_RESPONSE_BYTES} bytes.",
                http_status=int(exc.code),
            ) from exc
        headers = _header_map(exc.headers)
        return PitchAPIRawResponse(
            status=int(exc.code),
            body=body,
            headers=headers,
            final_url=str(exc.geturl() or request.full_url),
        )
    except urllib.error.URLError as exc:
        # urllib wraps socket/TimeoutError as URLError.reason; classify before generic failure.
        reason = getattr(exc, "reason", None)
        if isinstance(reason, TimeoutError) or (
            isinstance(reason, OSError) and "timed out" in str(reason).lower()
        ):
            raise PitchAPIError("PITCHAPI_TIMEOUT", "PitchAPI request timed out.") from exc
        raise PitchAPIError("PITCHAPI_REQUEST_FAILED", "PitchAPI request failed.") from exc
    except TimeoutError as exc:
        raise PitchAPIError("PITCHAPI_TIMEOUT", "PitchAPI request timed out.") from exc


def _assert_pinned_https(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        raise PitchAPIError("PITCHAPI_ORIGIN_NOT_HTTPS", "Only HTTPS PitchAPI origins are allowed.")
    if parts.username or parts.password:
        raise PitchAPIError("PITCHAPI_URL_CREDENTIALS_FORBIDDEN", "URL-embedded credentials are forbidden.")
    host = (parts.hostname or "").lower().rstrip(".")
    if host != "api.pitchapi.dev":
        raise PitchAPIError("PITCHAPI_ORIGIN_NOT_APPROVED", "Only api.pitchapi.dev is approved.")
    if parts.port not in (None, 443):
        raise PitchAPIError("PITCHAPI_ORIGIN_NOT_APPROVED", "Non-default HTTPS ports are not approved.")


def _season_for_pitchapi(wow_season: str | None, now: datetime | None = None) -> str:
    """Map WOW ``2025-26`` / ``2025/2026`` forms to PitchAPI ``2025/2026``."""
    if wow_season:
        text = str(wow_season).strip().replace("-", "/")
        parts = [p for p in text.split("/") if p]
        if len(parts) == 2 and len(parts[0]) == 4 and len(parts[1]) == 2:
            start = int(parts[0])
            return f"{start}/{start + 1}"
        if len(parts) == 2 and len(parts[0]) == 4 and len(parts[1]) == 4:
            return f"{parts[0]}/{parts[1]}"
        if len(parts) == 1 and len(parts[0]) == 4:
            return parts[0]
    stamp = now or datetime.now(timezone.utc)
    year = stamp.year if stamp.month >= 7 else stamp.year - 1
    return f"{year}/{year + 1}"


def _select_probe_season(seasons: Sequence[str], now: datetime | None = None) -> str:
    """Prefer the season that corresponds to ``now``; otherwise the latest catalogue season."""
    preferred = _season_for_pitchapi(None, now)
    normalized = [_season_for_pitchapi(s) for s in seasons if isinstance(s, str) and s.strip()]
    if preferred in normalized:
        return preferred
    if normalized:
        return max(normalized)
    return preferred


def openfootball_season_token(pitchapi_season: str) -> str:
    """Map PitchAPI ``2026/2027`` (or ``2026-27``) to OpenFootball ``2026-27``."""
    normalized = _season_for_pitchapi(pitchapi_season)
    parts = normalized.split("/")
    if len(parts) == 2 and len(parts[0]) == 4 and len(parts[1]) == 4:
        return f"{parts[0]}-{parts[1][2:]}"
    if len(parts) == 1 and len(parts[0]) == 4:
        return parts[0]
    raise PitchAPIError("PITCHAPI_SEASON_UNMAPPABLE", f"Cannot map season {pitchapi_season!r} to OpenFootball.")


def openfootball_result_rows_from_payload(payload: Any) -> list[ResultRow]:
    """Convert an OpenFootball competition JSON body into ResultRow evidence."""
    if not isinstance(payload, Mapping):
        raise PitchAPIError("OPENFOOTBALL_SCHEMA_INVALID", "OpenFootball body must be an object.")
    matches = payload.get("matches")
    if not isinstance(matches, list):
        raise PitchAPIError("OPENFOOTBALL_SCHEMA_INVALID", "OpenFootball matches must be a list.")
    rows: list[ResultRow] = []
    for match in matches:
        if not isinstance(match, Mapping):
            continue
        day_text = match.get("date")
        home = match.get("team1")
        away = match.get("team2")
        if not isinstance(day_text, str) or not isinstance(home, str) or not isinstance(away, str):
            continue
        try:
            day = date.fromisoformat(day_text[:10])
        except ValueError:
            continue
        score = match.get("score")
        if isinstance(score, Mapping):
            score = score.get("ft")
        hg = ag = None
        if isinstance(score, list) and len(score) >= 2 and all(isinstance(v, (int, float)) for v in score[:2]):
            hg, ag = int(score[0]), int(score[1])
        rows.append(ResultRow(day=day, home=home.strip(), away=away.strip(), home_goals=hg, away_goals=ag))
    return rows


# Keep the OpenFootball path map local so crosswalk tests do not import the
# full soccer candidate module (and its optional runtime deps) just for codes.
OPENFOOTBALL_BASE_URL = "https://raw.githubusercontent.com/openfootball/football.json/master"
OPENFOOTBALL_COMPETITIONS = {
    "EPL": "en.1",
    "BUNDESLIGA": "de.1",
    "LALIGA": "es.1",
    "SERIE_A": "it.1",
    "LIGUE_1": "fr.1",
}


def load_openfootball_result_rows(
    wow_league: str,
    pitchapi_season: str,
    *,
    getter: Callable[[str], Any] | None = None,
) -> tuple[list[ResultRow], dict[str, Any]]:
    """Bounded single-file OpenFootball pull for canonical crosswalk evidence only."""
    code = OPENFOOTBALL_COMPETITIONS.get(wow_league.upper())
    if not code:
        raise PitchAPIError("OPENFOOTBALL_LEAGUE_UNSUPPORTED", f"No OpenFootball mapping for {wow_league}.")
    season_token = openfootball_season_token(pitchapi_season)
    url = f"{OPENFOOTBALL_BASE_URL}/{season_token}/{code}.json"

    def _default_getter(target: str) -> Any:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(target, headers={"User-Agent": USER_AGENT}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                status = int(getattr(response, "status", 200))
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if int(exc.code) == 404:
                raise PitchAPIError(
                    "OPENFOOTBALL_SEASON_NOT_FOUND",
                    f"OpenFootball file missing for {season_token}.",
                ) from exc
            raise PitchAPIError("OPENFOOTBALL_HTTP_ERROR", f"OpenFootball HTTP {exc.code}.") from exc
        except Exception as exc:  # noqa: BLE001
            raise PitchAPIError("OPENFOOTBALL_REQUEST_FAILED", "OpenFootball request failed.") from exc
        if status != 200:
            raise PitchAPIError("OPENFOOTBALL_HTTP_ERROR", f"OpenFootball HTTP {status}.")
        if len(body) > MAX_RESPONSE_BYTES:
            raise PitchAPIError("OPENFOOTBALL_RESPONSE_TOO_LARGE", "OpenFootball response exceeded bound.")
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PitchAPIError("OPENFOOTBALL_INVALID_JSON", "OpenFootball response was not valid JSON.") from exc

    payload = (getter or _default_getter)(url)
    rows = openfootball_result_rows_from_payload(payload)
    meta = {
        "source": "OPENFOOTBALL:football.json",
        "url": url,
        "season_token": season_token,
        "competition_code": code,
        "row_count": len(rows),
        "scored_rows": sum(1 for row in rows if row.scored),
        "persisted": False,
    }
    return rows, meta


def canonical_event_crosswalk(
    pitchapi_rows: Sequence[ResultRow],
    reference_rows: Sequence[ResultRow],
    *,
    min_overlap: int = 10,
) -> dict[str, Any]:
    """Score-consistent event pairing against OpenFootball. Never persists or clears blockers."""
    check = cross_check(pitchapi_rows, reference_rows, min_overlap=min_overlap)
    status = str(check.get("status") or "UNKNOWN")
    return {
        "status": status,
        "reference_source": "OPENFOOTBALL:football.json",
        "overlap": check.get("overlap"),
        "conflict_count": check.get("conflict_count"),
        "conflicts": check.get("conflicts") or [],
        "min_overlap_required": min_overlap,
        "pitchapi_scored_rows": sum(1 for row in pitchapi_rows if row.scored),
        "reference_scored_rows": sum(1 for row in reference_rows if row.scored),
        "persisted": False,
        "production_use_authorized": False,
        "can_execute": CAN_EXECUTE,
        "note": (
            "Evidence-only pairing. A CONSISTENT result does not authorize storage, "
            "training, or clearing SOCCER_SOURCE_RESULTS_STALE."
        ),
    }


def _parse_leagues_envelope(payload: Any) -> list[LeagueSummary]:
    if not isinstance(payload, Mapping):
        raise PitchAPIError("PITCHAPI_ENVELOPE_INVALID", "PitchAPI leagues response must be a JSON object.")
    if "error" in payload and payload.get("data") is None:
        err = payload.get("error") if isinstance(payload.get("error"), Mapping) else {}
        code = str(err.get("code") or "PITCHAPI_PROVIDER_ERROR")
        raise PitchAPIError(code, str(err.get("message") or "PitchAPI returned an error envelope."))
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise PitchAPIError("PITCHAPI_ENVELOPE_INVALID", "PitchAPI success envelope requires data object.")
    leagues = data.get("leagues")
    if not isinstance(leagues, list):
        raise PitchAPIError("PITCHAPI_ENVELOPE_INVALID", "PitchAPI data.leagues must be a list.")
    out: list[LeagueSummary] = []
    for row in leagues:
        if not isinstance(row, Mapping):
            raise PitchAPIError("PITCHAPI_LEAGUE_SCHEMA_INVALID", "League row must be an object.")
        league_id = row.get("id")
        name = row.get("name")
        if not isinstance(league_id, str) or not LEAGUE_ID_RE.match(league_id):
            raise PitchAPIError("PITCHAPI_LEAGUE_SCHEMA_INVALID", "League id failed PitchAPI id contract.")
        if not isinstance(name, str) or not name.strip():
            raise PitchAPIError("PITCHAPI_LEAGUE_SCHEMA_INVALID", "League name is required.")
        seasons_raw = row.get("seasons") or []
        if not isinstance(seasons_raw, list) or any(not isinstance(s, str) for s in seasons_raw):
            raise PitchAPIError("PITCHAPI_LEAGUE_SCHEMA_INVALID", "League seasons must be a string list.")
        country = row.get("country_code")
        if country is not None and not isinstance(country, str):
            raise PitchAPIError("PITCHAPI_LEAGUE_SCHEMA_INVALID", "country_code must be a string when present.")
        is_free = row.get("is_free")
        if is_free is not None and not isinstance(is_free, bool):
            raise PitchAPIError("PITCHAPI_LEAGUE_SCHEMA_INVALID", "is_free must be a bool when present.")
        out.append(
            LeagueSummary(
                id=league_id,
                name=name.strip(),
                country_code=country,
                seasons=tuple(seasons_raw),
                is_free=is_free,
            )
        )
    return out


def _match_to_result_row(match: Mapping[str, Any]) -> ResultRow | None:
    day_text = match.get("date")
    home = match.get("home_team") if isinstance(match.get("home_team"), Mapping) else None
    away = match.get("away_team") if isinstance(match.get("away_team"), Mapping) else None
    if not isinstance(day_text, str) or home is None or away is None:
        return None
    try:
        day = date.fromisoformat(day_text[:10])
    except ValueError:
        return None
    home_name = home.get("name")
    away_name = away.get("name")
    if not isinstance(home_name, str) or not isinstance(away_name, str):
        return None
    status = str(match.get("status") or "").lower()
    hg = match.get("score_home")
    ag = match.get("score_away")
    scored = status == "finished" and isinstance(hg, int) and isinstance(ag, int)
    return ResultRow(
        day=day,
        home=home_name,
        away=away_name,
        home_goals=hg if scored else None,
        away_goals=ag if scored else None,
    )


def _parse_matches_envelope(payload: Any) -> tuple[Mapping[str, Any], list[ResultRow]]:
    if not isinstance(payload, Mapping):
        raise PitchAPIError("PITCHAPI_ENVELOPE_INVALID", "PitchAPI matches response must be a JSON object.")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise PitchAPIError("PITCHAPI_ENVELOPE_INVALID", "PitchAPI matches envelope requires data object.")
    league = data.get("league") if isinstance(data.get("league"), Mapping) else {}
    matches = data.get("matches")
    if not isinstance(matches, list):
        raise PitchAPIError("PITCHAPI_ENVELOPE_INVALID", "PitchAPI data.matches must be a list.")
    rows: list[ResultRow] = []
    for item in matches:
        if not isinstance(item, Mapping):
            raise PitchAPIError("PITCHAPI_MATCH_SCHEMA_INVALID", "Match row must be an object.")
        # Predicted lineups are a separate endpoint; never infer confirmation from listings.
        if "lineup" in item or "lineups" in item:
            raise PitchAPIError(
                "PITCHAPI_POST_KICKOFF_LEAKAGE_GUARD",
                "Unexpected lineup payload on matches listing; refuse to avoid predicted/confirmed confusion.",
            )
        row = _match_to_result_row(item)
        if row is not None:
            rows.append(row)
    return league, rows


class PitchAPIClient:
    """Allowlisted read-only PitchAPI client."""

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_S,
        transport: TransportFn | None = None,
    ):
        key = str(api_key or "").strip()
        if not key:
            raise PitchAPIError("PITCHAPI_API_KEY_MISSING", "PITCHAPI_API_KEY is required.")
        self.api_key = key
        self.timeout_seconds = float(timeout_seconds)
        self.transport = transport or _default_transport
        self.credential_class = credential_class(key)

    @classmethod
    def from_environment(cls, *, transport: TransportFn | None = None) -> "PitchAPIClient":
        return cls(api_key=os.environ.get("PITCHAPI_API_KEY", ""), transport=transport)

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-KEY": self.api_key,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

    def _get(self, path: str, *, query: Mapping[str, Any] | None = None) -> tuple[Any, PitchAPIRawResponse]:
        # Exact catalogue path is `/v1/leagues` with no trailing slash; matches use `/v1/leagues/{id}/matches`.
        allowed = path == LEAGUES_PATH or bool(re.match(r"^/v1/leagues/l_[A-Za-z0-9]{6}/matches$", path))
        if not allowed:
            code = "PITCHAPI_TRAILING_SLASH_FORBIDDEN" if path == "/v1/leagues/" else "PITCHAPI_ENDPOINT_NOT_ALLOWLISTED"
            raise PitchAPIError(code, f"Path {path!r} is not approved.")
        url = f"{PITCHAPI_ORIGIN}{path}"
        if query:
            clean = {str(k): v for k, v in query.items() if v is not None}
            if clean:
                url = f"{url}?{urlencode(clean)}"
        _assert_pinned_https(url)
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        raw = self.transport(request, self.timeout_seconds)
        _assert_pinned_https(raw.final_url.split("?")[0] if "?" in raw.final_url else raw.final_url)
        request_id = raw.headers.get("x-request-id") or raw.headers.get("x-pitchapi-request-id")
        if raw.status == 429:
            raise PitchAPIError(
                "PITCHAPI_RATE_LIMITED",
                "PitchAPI rate-limited the request.",
                http_status=429,
                retry_after_seconds=_retry_after_seconds(raw.headers.get("retry-after")),
                request_id=request_id,
            )
        if raw.status == 403:
            raise PitchAPIError(
                "PITCHAPI_FORBIDDEN",
                "PitchAPI returned HTTP 403.",
                http_status=403,
                request_id=request_id,
            )
        if raw.status != 200:
            raise PitchAPIError(
                "PITCHAPI_HTTP_ERROR",
                f"PitchAPI returned HTTP {raw.status}.",
                http_status=raw.status,
                request_id=request_id,
            )
        try:
            payload = json.loads(raw.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PitchAPIError(
                "PITCHAPI_INVALID_JSON",
                "PitchAPI response was not valid JSON.",
                http_status=raw.status,
                request_id=request_id,
            ) from exc
        return payload, raw

    def list_leagues(self) -> tuple[list[LeagueSummary], PitchAPIRawResponse]:
        payload, raw = self._get(LEAGUES_PATH)
        return _parse_leagues_envelope(payload), raw

    def list_league_matches(
        self,
        league_id: str,
        *,
        season: str | None = None,
        status: str | None = None,
    ) -> tuple[Mapping[str, Any], list[ResultRow], PitchAPIRawResponse]:
        if not isinstance(league_id, str) or not LEAGUE_ID_RE.match(league_id):
            raise PitchAPIError("PITCHAPI_LEAGUE_ID_INVALID", "League id failed PitchAPI id contract.")
        if status is not None and status not in {"upcoming", "all"}:
            # Default (omitted) is played/finished only — preferred for results freshness.
            raise PitchAPIError("PITCHAPI_STATUS_NOT_ALLOWLISTED", f"Match status {status!r} is not approved.")
        path = f"{LEAGUES_PATH}/{league_id}/matches"
        query: dict[str, Any] = {}
        if season:
            query["season"] = season
        if status:
            query["status"] = status
        payload, raw = self._get(path, query=query or None)
        league, rows = _parse_matches_envelope(payload)
        return league, rows, raw


def find_wow_league(leagues: Sequence[LeagueSummary], wow_league: str) -> LeagueSummary | None:
    target = WOW_TO_PITCHAPI_LEAGUE_NAME.get(wow_league.upper())
    if not target:
        return None
    preferred_country = {
        "EPL": "ENG",
        "LALIGA": "ESP",
        "BUNDESLIGA": "GER",
        "SERIE_A": "ITA",
        "LIGUE_1": "FRA",
    }.get(wow_league.upper())
    exact = [row for row in leagues if row.name.casefold() == target.casefold()]
    if preferred_country:
        country_hits = [
            row for row in exact if (row.country_code or "").strip().upper() == preferred_country
        ]
        if len(country_hits) == 1:
            return country_hits[0]
        # Name-only fallback is limited to rows without an explicit country.
        # An explicit mismatch (e.g. RSA Premier League for EPL) is rejected.
        unknown_country = [row for row in exact if not (row.country_code or "").strip()]
        if len(unknown_country) == 1:
            return unknown_country[0]
        return None
    if len(exact) == 1:
        return exact[0]
    return None


def rights_handoff_stub(*, credential_cls: str, observed_at: str) -> dict[str, Any]:
    """Evidence-only rights placeholder. Never authorizes storage or training."""
    return {
        "source_id": "pitchapi:api.pitchapi.dev",
        "provider": PROVIDER,
        "assessed_at": observed_at,
        "research_access": "AUTHENTICATED_API",
        "credential_class": credential_cls,
        "storage_permitted": "UNRESOLVED",
        "model_training_permitted": "UNRESOLVED",
        "production_use_permitted": "PENDING_INDEPENDENT_REVIEW",
        "production_use_authorized": False,
        "review_status": "PENDING_INDEPENDENT_REVIEW",
        "notes": (
            "Authenticated PitchAPI connectivity is verified separately from "
            "upstream rights clearance for storage, fitting, and betting research."
        ),
        "can_execute": CAN_EXECUTE,
    }


def league_discovery_canary(
    *,
    client: PitchAPIClient | None = None,
    wow_league: str = "EPL",
    now: datetime | None = None,
    probe_matches: bool = True,
    reference_rows: Sequence[ResultRow] | None = None,
    openfootball_getter: Callable[[str], Any] | None = None,
    perform_crosswalk: bool = True,
) -> dict[str, Any]:
    """Authenticated league-discovery receipt. Never logs or returns the API key."""
    observed_at = _now_iso(now)
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "provider": PROVIDER,
        "endpoint": f"{PITCHAPI_ORIGIN}{LEAGUES_PATH}",
        "observed_at": observed_at,
        "status": "STARTED",
        "http_status": None,
        "byte_count": None,
        "body_sha256": None,
        "league_count": None,
        "request_id": None,
        "credential_class": credential_class(os.environ.get("PITCHAPI_API_KEY")),
        "user_agent": USER_AGENT,
        "evidence_only": True,
        "prediction_authority": False,
        "production_use_authorized": False,
        "persisted": False,
        "can_execute": CAN_EXECUTE,
    }
    try:
        client = client or PitchAPIClient.from_environment()
        receipt["credential_class"] = client.credential_class
        leagues, raw = client.list_leagues()
        request_id = raw.headers.get("x-request-id") or raw.headers.get("x-pitchapi-request-id")
        receipt.update(
            status="OK",
            http_status=raw.status,
            byte_count=len(raw.body),
            body_sha256=hashlib.sha256(raw.body).hexdigest(),
            league_count=len(leagues),
            request_id=request_id,
            rights=rights_handoff_stub(credential_cls=client.credential_class, observed_at=observed_at),
        )
        matched = find_wow_league(leagues, wow_league)
        receipt["wow_league"] = wow_league
        receipt["wow_league_name"] = LEAGUE_NAMES.get(wow_league.upper(), wow_league)
        if matched is None:
            receipt["match_probe"] = {
                "status": "LEAGUE_NOT_FOUND",
                "wow_league": wow_league,
            }
            return receipt
        receipt["pitchapi_league"] = {
            "id": matched.id,
            "name": matched.name,
            "country_code": matched.country_code,
            "seasons": list(matched.seasons),
        }
        if not probe_matches:
            return receipt
        # Match probe is optional evidence. Failures must not overwrite a successful
        # league-discovery receipt (HTTP/bytes/hash/request_id stay catalogue-scoped).
        season = _select_probe_season(matched.seasons, now)
        try:
            league_meta, rows, match_raw = client.list_league_matches(matched.id, season=season)
            fresh = results_freshness(rows, now or datetime.now(timezone.utc))
            crosswalk: dict[str, Any] = {
                "status": "NOT_PERFORMED",
                "persisted": False,
                "production_use_authorized": False,
                "can_execute": CAN_EXECUTE,
            }
            if perform_crosswalk:
                try:
                    ref_rows = list(reference_rows) if reference_rows is not None else None
                    ref_meta: dict[str, Any] | None = None
                    if ref_rows is None:
                        ref_rows, ref_meta = load_openfootball_result_rows(
                            wow_league,
                            season,
                            getter=openfootball_getter,
                        )
                    crosswalk = canonical_event_crosswalk(rows, ref_rows)
                    if ref_meta is not None:
                        crosswalk["reference"] = ref_meta
                except PitchAPIError as xref_exc:
                    crosswalk = {
                        "status": "FAILED",
                        "code": xref_exc.code,
                        "persisted": False,
                        "production_use_authorized": False,
                        "can_execute": CAN_EXECUTE,
                        "error": xref_exc.to_dict(),
                    }
                except Exception:  # noqa: BLE001
                    crosswalk = {
                        "status": "FAILED",
                        "code": "PITCHAPI_CROSSWALK_FAILED",
                        "persisted": False,
                        "production_use_authorized": False,
                        "can_execute": CAN_EXECUTE,
                    }
            receipt["match_probe"] = {
                "status": "OK",
                "endpoint": f"{PITCHAPI_ORIGIN}{LEAGUES_PATH}/{matched.id}/matches",
                "season": season,
                "http_status": match_raw.status,
                "byte_count": len(match_raw.body),
                "body_sha256": hashlib.sha256(match_raw.body).hexdigest(),
                "request_id": match_raw.headers.get("x-request-id")
                or match_raw.headers.get("x-pitchapi-request-id"),
                "result_rows": len(rows),
                "scored_rows": sum(1 for row in rows if row.scored),
                "freshness": fresh,
                "fresh_relative_to_soccer_source_results_stale": fresh.get("status") == "FRESH",
                "league_meta_name": league_meta.get("name") if isinstance(league_meta, Mapping) else None,
                "persisted": False,
                "canonical_event_crosswalk": crosswalk,
                "note": (
                    "Freshness and optional OpenFootball crosswalk are evidence only. "
                    "They never authorize storage/training or clear SOCCER_SOURCE_RESULTS_STALE."
                ),
            }
        except PitchAPIError as probe_exc:
            receipt["match_probe"] = {
                "status": "FAILED",
                "endpoint": f"{PITCHAPI_ORIGIN}{LEAGUES_PATH}/{matched.id}/matches",
                "season": season,
                "code": probe_exc.code,
                "http_status": probe_exc.http_status,
                "request_id": probe_exc.request_id,
                "retry_after_seconds": probe_exc.retry_after_seconds,
                "persisted": False,
                "error": probe_exc.to_dict(),
            }
        except Exception:  # noqa: BLE001 - never expose exception class names in receipts
            receipt["match_probe"] = {
                "status": "FAILED",
                "endpoint": f"{PITCHAPI_ORIGIN}{LEAGUES_PATH}/{matched.id}/matches",
                "season": season,
                "code": "PITCHAPI_MATCH_PROBE_FAILED",
                "persisted": False,
            }
        return receipt
    except PitchAPIError as exc:
        receipt.update(
            status="FAILED",
            code=exc.code,
            http_status=exc.http_status,
            request_id=exc.request_id,
            retry_after_seconds=exc.retry_after_seconds,
            error=exc.to_dict(),
        )
        return receipt
    except Exception:  # noqa: BLE001 - never expose exception class names in receipts
        receipt.update(
            status="FAILED",
            code="PITCHAPI_CANARY_FAILED",
        )
        return receipt


def soccer_pitchapi_probe_after_forward(
    forward: Mapping[str, Any] | None = None,
    *,
    client: PitchAPIClient | None = None,
    now: datetime | None = None,
    openfootball_getter: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Soccer maintenance hook: evidence-only PitchAPI canary for stale leagues.

    Never raises into the maintenance caller. Never clears
    ``SOCCER_SOURCE_RESULTS_STALE``. Never persists provider payloads.
    """
    forward = forward or {}
    stale = [
        league
        for league, rec in sorted((forward.get("leagues") or {}).items())
        if isinstance(rec, Mapping) and (rec.get("blocked_by_code") or {}).get("SOCCER_SOURCE_RESULTS_STALE")
    ]
    wow_league = stale[0] if stale else "EPL"
    receipt = league_discovery_canary(
        client=client,
        wow_league=wow_league,
        now=now,
        openfootball_getter=openfootball_getter,
        perform_crosswalk=True,
    )
    receipt["stale_leagues"] = stale
    receipt["selected_stale_league"] = wow_league if stale else None
    receipt["blocker_progress"] = {
        "SOCCER_SOURCE_RESULTS_STALE": "EVIDENCE_ONLY_PROBE",
        "cleared": False,
        "note": "PitchAPI probe cannot erase the OpenFootball stale-results blocker.",
    }
    return receipt
