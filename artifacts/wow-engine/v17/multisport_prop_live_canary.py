"""Production-safe V17 multi-sport prop canary and stress runner.

Exercises the canonical /score-pick-request boundary for certified NFL, MLB, and
WNBA prop lanes. The runner never places wagers and requires can_execute=false.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Running this file directly puts ``v17/`` (not the engine root) on sys.path.
# Fixture acquisition imports the governed producers as the ``v17`` package, so
# make the engine root importable without depending on the caller's cwd.
_ENGINE_ROOT = str(Path(__file__).resolve().parent.parent)
if _ENGINE_ROOT not in sys.path:
    sys.path.append(_ENGINE_ROOT)

ORIGIN = os.environ.get("WOW_SERVICE_URL", "https://wow-governed-probability-engine.onrender.com").rstrip("/")
AUDIENCE = os.environ.get("WOW_OIDC_AUDIENCE", "wow-v17-multiscout")
BASE_RUN = "-".join(
    part for part in (
        "multisport-prop-canary",
        os.environ.get("GITHUB_RUN_ID", "local"),
        os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
    ) if part
)

# #502 acceptance budget: retain a 25% safety margin below the historical
# 120-second interactive transport ceiling. This is an engineering latency
# contract only; it does not change sporting probability behavior.
MAX_INTERACTIVE_SECONDS = 90.0


def _row(**kwargs: Any) -> dict[str, Any]:
    row = dict(kwargs)
    row.setdefault("source_type", "NORMALIZED")
    row.setdefault("platform", "WOW_PRODUCTION_CANARY")
    return row


CONFIGS: dict[str, list[dict[str, Any]]] = {
    "NFL": [
        _row(row_key="nfl-lamb-more", event_id="2026_05_TB_DAL", event_start_time="2026-10-09T00:15:00Z", sport="NFL", league="NFL", player="CeeDee Lamb", stat_type="RECEIVING_YARDS", line=111.5, direction="MORE", opponent="TB"),
        _row(row_key="nfl-lamb-less", event_id="2026_05_TB_DAL", event_start_time="2026-10-09T00:15:00Z", sport="NFL", league="NFL", player="CeeDee Lamb", stat_type="RECEIVING_YARDS", line=111.5, direction="LESS", opponent="TB"),
        _row(row_key="nfl-spann-ford-more", event_id="2026_05_TB_DAL", event_start_time="2026-10-09T00:15:00Z", sport="NFL", league="NFL", player="Brevyn Spann-Ford", stat_type="RECEIVING_YARDS", line=8.5, direction="MORE", opponent="TB"),
        _row(row_key="nfl-spann-ford-less", event_id="2026_05_TB_DAL", event_start_time="2026-10-09T00:15:00Z", sport="NFL", league="NFL", player="Brevyn Spann-Ford", stat_type="RECEIVING_YARDS", line=8.5, direction="LESS", opponent="TB"),
    ],
    "MLB": [
        _row(row_key="mlb-mahle-more", event_id="MLB:849822", event_start_time="2026-10-07T22:00:00Z", sport="MLB", league="MLB", player="Tyler Mahle", stat_type="PITCHER_STRIKEOUTS", line=5.5, direction="MORE", opponent="LAD"),
        _row(row_key="mlb-mahle-less", event_id="MLB:849822", event_start_time="2026-10-07T22:00:00Z", sport="MLB", league="MLB", player="Tyler Mahle", stat_type="PITCHER_STRIKEOUTS", line=5.5, direction="LESS", opponent="LAD"),
        _row(row_key="mlb-glasnow-more", event_id="MLB:849822", event_start_time="2026-10-07T22:00:00Z", sport="MLB", league="MLB", player="Tyler Glasnow", stat_type="PITCHER_STRIKEOUTS", line=7.5, direction="MORE", opponent="ATL"),
        _row(row_key="mlb-glasnow-less", event_id="MLB:849822", event_start_time="2026-10-07T22:00:00Z", sport="MLB", league="MLB", player="Tyler Glasnow", stat_type="PITCHER_STRIKEOUTS", line=7.5, direction="LESS", opponent="ATL"),
    ],
    "WNBA": [
        _row(row_key="wnba-stewart-more", event_id="WNBA:1042600202", event_start_time="2026-10-07T23:30:00Z", sport="WNBA", league="WNBA", player="Breanna Stewart", stat_type="POINTS", line=20.5, direction="MORE", opponent="ATL"),
        _row(row_key="wnba-stewart-less", event_id="WNBA:1042600202", event_start_time="2026-10-07T23:30:00Z", sport="WNBA", league="WNBA", player="Breanna Stewart", stat_type="POINTS", line=20.5, direction="LESS", opponent="ATL"),
        _row(row_key="wnba-gray-more", event_id="WNBA:1042600202", event_start_time="2026-10-07T23:30:00Z", sport="WNBA", league="WNBA", player="Allisha Gray", stat_type="POINTS", line=18.5, direction="MORE", opponent="NYL"),
        _row(row_key="wnba-gray-less", event_id="WNBA:1042600202", event_start_time="2026-10-07T23:30:00Z", sport="WNBA", league="WNBA", player="Allisha Gray", stat_type="POINTS", line=18.5, direction="LESS", opponent="NYL"),
    ],
}


def future_fixture_sports(
    now: datetime | None = None,
    *,
    configs: dict[str, list[dict[str, Any]]] | None = None,
) -> tuple[str, ...]:
    """Select future-dated fixtures; never send historical IDs to production.

    ``configs`` defaults to the historical ``CONFIGS`` shape fixtures. Live runs
    pass acquired rows instead. A future timestamp does not certify player
    availability or the offer; the backend still owns exact
    event/route/settlement/terminal checks.
    """
    now = now or datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise ValueError("CANARY_CLOCK_MUST_BE_TIMEZONE_AWARE")
    fixtures = CONFIGS if configs is None else configs
    selected: list[str] = []
    for sport, rows in fixtures.items():
        starts = [
            datetime.fromisoformat(str(row["event_start_time"]).replace("Z", "+00:00"))
            for row in rows
        ]
        if all(start.utcoffset() is not None and start > now for start in starts):
            selected.append(sport)
    return tuple(selected)


def _fresh_oidc() -> str:
    url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    sep = "&" if "?" in url else "?"
    request = urllib.request.Request(
        url + sep + urllib.parse.urlencode({"audience": AUDIENCE}),
        headers={"Authorization": f"Bearer {os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        value = json.load(response).get("value")
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError("OIDC_TOKEN_MISSING")
    return value.strip()


def _post(payload: dict[str, Any], *, timeout: int = 330) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in (1, 2):
        token = _fresh_oidc()
        request = urllib.request.Request(
            ORIGIN + "/score-pick-request",
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-WOW-Caller-Class": "GITHUB_ACTIONS",
                "X-WOW-Request-ID": payload["request_id"],
                "X-WOW-Rows-In": str(len(payload["rows"])),
            },
        )
        try:
            started = time.perf_counter()
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.load(response)
                if response.status != 200:
                    raise RuntimeError(f"HTTP_{response.status}")
                if not isinstance(body, dict):
                    raise RuntimeError("CANARY_RESPONSE_NOT_OBJECT")
                body["_canary_elapsed_seconds"] = round(time.perf_counter() - started, 3)
                return body
        except urllib.error.HTTPError as exc:
            last_error = exc
            if 400 <= exc.code < 500 or attempt == 2:
                text = exc.read().decode("utf-8", errors="replace")[:4000]
                raise RuntimeError(f"HTTP_{exc.code}:{text}") from exc
        except Exception as exc:
            last_error = exc
            if attempt == 2:
                raise
        time.sleep(1.0)
    raise RuntimeError(f"CANARY_REQUEST_FAILED:{last_error}")


def _validate(
    payload: dict[str, Any],
    *,
    sport: str,
    require_publishable: bool = False,
) -> dict[str, Any]:
    if payload.get("can_execute") is not False:
        raise AssertionError(f"{sport}:can_execute")
    if payload.get("reconciliation_pass") is not True:
        raise AssertionError(f"{sport}:reconciliation")

    elapsed = float(payload.get("_canary_elapsed_seconds") or 0.0)
    if elapsed <= 0.0:
        raise AssertionError(f"{sport}:latency_measurement_missing")
    if elapsed > MAX_INTERACTIVE_SECONDS:
        raise AssertionError(
            f"{sport}:interactive_latency_seconds={elapsed:.3f}>"
            f"{MAX_INTERACTIVE_SECONDS:.3f}"
        )

    rows = payload.get("outcomes") or payload.get("rows") or []
    if len(rows) != 4:
        raise AssertionError(f"{sport}:rows={len(rows)}")

    summary: list[dict[str, Any]] = []
    failures: list[str] = []
    publishable_rows = 0
    for row in rows:
        terminal = str(row.get("terminal_status") or "")
        code = row.get("code") or row.get("terminal_label") or row.get("blocker_code")
        raw = row.get("model_probability")
        calibrated = row.get("calibrated_probability")
        lower = row.get("calibrated_probability_lower_bound")
        publishable = row.get("probability_publishable") is True
        item = {
            "row_key": row.get("row_key"),
            "terminal_status": terminal,
            "code": code,
            "blocker_code": row.get("blocker_code"),
            "model_evaluated": row.get("model_evaluated"),
            "probability_publishable": publishable,
            "model_probability": raw,
            "calibrated_probability": calibrated,
            "calibrated_lower_bound": lower,
            "can_execute": row.get("can_execute"),
            "resumed": row.get("resumed_from_durable_receipt") is True,
        }
        summary.append(item)

        if row.get("can_execute") is not False:
            failures.append(f"{item['row_key']}:can_execute")
        if terminal not in {"COMPLETED", "HELD", "REJECTED"}:
            failures.append(f"{item['row_key']}:terminal={terminal}:{code}")
        if not code and terminal != "COMPLETED":
            failures.append(f"{item['row_key']}:typed_terminal_missing")

        if publishable:
            publishable_rows += 1
            if row.get("model_evaluated") is not True:
                failures.append(f"{item['row_key']}:publishable_without_model_evaluation")
            for name, value in (("raw", raw), ("calibrated", calibrated), ("lower", lower)):
                if not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
                    failures.append(f"{item['row_key']}:{name}={value!r}")

    if require_publishable and publishable_rows == 0:
        failures.append("no_publishable_governed_probability_package")
    if failures:
        raise AssertionError(f"{sport}:" + " | ".join(failures))

    return {
        "sport": sport,
        "request_id": payload.get("request_id"),
        "elapsed_seconds": elapsed,
        "publishable_rows": publishable_rows,
        "rows": summary,
        "can_execute": False,
    }


def _request_for(
    sport: str,
    request_id: str,
    fixtures: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    rows = (CONFIGS if fixtures is None else fixtures)[sport]
    return {"request_id": request_id, "response_mode": "COMPACT", "rows": deepcopy(rows)}


def _fixture_module() -> Any:
    """Resolve the acquisition provider; ``None`` keeps live eligibility closed."""
    try:
        from v17 import pregame_canary_fixtures as module

        return module
    except Exception:
        return None


FIXTURE_BUNDLE_VERSION = 1


class FixtureBundleError(RuntimeError):
    """Typed failure for a persisted acquisition bundle that cannot be trusted."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def _write_fixture_bundle(path: str, report: dict[str, Any]) -> None:
    """Persist one acquisition report so the live step reuses the same rows."""
    bundle = {
        "bundle_version": FIXTURE_BUNDLE_VERSION,
        "fixture_source": "ACQUIRED",
        "snapshot_write_mode": report.get("snapshot_write_mode"),
        "snapshot_writes_suppressed": report.get("snapshot_writes_suppressed"),
        "acquired_at": datetime.now(timezone.utc).isoformat(),
        "sports_eligible": list(report.get("sports_eligible") or []),
        "configs": report.get("configs") or {},
        "blockers": report.get("blockers") or {},
        "can_execute": False,
    }
    Path(path).write_text(json.dumps(bundle, sort_keys=True), encoding="utf-8")


def _read_fixture_bundle(path: str, module: Any) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Load a persisted bundle, re-validating provenance and row shape."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise FixtureBundleError("CANARY_FIXTURE_BUNDLE_UNREADABLE", type(exc).__name__) from exc
    try:
        bundle = json.loads(raw)
    except ValueError as exc:
        raise FixtureBundleError("CANARY_FIXTURE_BUNDLE_INVALID_JSON", type(exc).__name__) from exc
    if not isinstance(bundle, dict):
        raise FixtureBundleError("CANARY_FIXTURE_BUNDLE_INVALID_SHAPE")
    if bundle.get("bundle_version") != FIXTURE_BUNDLE_VERSION:
        raise FixtureBundleError("CANARY_FIXTURE_BUNDLE_VERSION_MISMATCH")
    if str(bundle.get("fixture_source") or "") != "ACQUIRED":
        raise FixtureBundleError("CANARY_FIXTURE_BUNDLE_NOT_ACQUIRED")
    # The no-write guarantee is part of the artifact contract: a bundle that
    # claims real snapshot writes or execution capability is not consumable.
    if str(bundle.get("snapshot_write_mode") or "") != "NO_WRITE":
        raise FixtureBundleError("CANARY_FIXTURE_BUNDLE_WRITE_MODE_NOT_NO_WRITE")
    if bundle.get("can_execute") is not False:
        raise FixtureBundleError("CANARY_FIXTURE_BUNDLE_EXECUTION_FLAG_INVALID")

    raw_configs = bundle.get("configs")
    if not isinstance(raw_configs, dict) or not raw_configs:
        raise FixtureBundleError("CANARY_FIXTURE_BUNDLE_CONFIGS_INVALID")
    fields = tuple(module.CANARY_ROW_FIELDS)
    configs: dict[str, list[dict[str, Any]]] = {}
    for sport, rows in raw_configs.items():
        # A sport named in the bundle must survive intact. Dropping a corrupt
        # sport silently would surface as an empty slate (exit 78) and read as
        # out-of-season rather than as the damaged artifact it is.
        if sport not in module.SPORTS:
            raise FixtureBundleError("CANARY_FIXTURE_BUNDLE_UNKNOWN_SPORT", str(sport))
        if not isinstance(rows, list):
            raise FixtureBundleError("CANARY_FIXTURE_BUNDLE_ROWS_INVALID", str(sport))
        kept: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(fields):
                continue
            kept.append({field: row[field] for field in fields})
        if len(kept) != module.ROWS_PER_SPORT or len(kept) != len(rows):
            raise FixtureBundleError(
                "CANARY_FIXTURE_BUNDLE_ROW_SHAPE_INVALID",
                f"{sport}:{len(kept)}/{len(rows)}",
            )
        configs[sport] = kept
    return configs, bundle


def _select_fixtures(fixtures_from: str | None = None) -> dict[str, Any]:
    """Choose persisted, live-acquired or historical rows; never a silent downgrade."""
    module = _fixture_module()
    if module is None:
        return {"source": "ACQUIRED", "configs": None, "report": {
            "status": "LIVE_CANARY_ACQUISITION_FAILED",
            "code": "CANARY_FIXTURE_PROVIDER_UNAVAILABLE",
        }}
    if fixtures_from:
        try:
            configs, bundle = _read_fixture_bundle(fixtures_from, module)
        except FixtureBundleError as exc:
            return {"source": "ACQUIRED", "configs": None, "report": {
                "status": "LIVE_CANARY_ACQUISITION_FAILED",
                "code": exc.code,
                "detail": exc.detail,
                "path": fixtures_from,
            }}
        return {"source": "ACQUIRED", "configs": configs, "report": bundle,
                "fixture_input": "BUNDLE"}
    if not module.acquisition_enabled():
        return {"source": "STATIC", "configs": CONFIGS, "report": None}
    report = module.acquire_canary_rows()
    if report.get("status") == "ACQUISITION_FAILED":
        return {"source": "ACQUIRED", "configs": None, "report": report}
    return {"source": "ACQUIRED", "configs": report["configs"], "report": report,
            "fixture_input": "LIVE"}


def _option(name: str) -> str | None:
    """Read ``--name value`` or ``--name=value`` from argv."""
    argv = sys.argv
    for index, item in enumerate(argv):
        if item == name and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith(name + "="):
            return item.split("=", 1)[1]
    return None


def _check_live_fixtures(emit_path: str | None = None) -> int:
    """Report live eligibility without authenticating or posting a row."""
    selection = _select_fixtures()
    if selection["configs"] is None:
        print(json.dumps({
            "status": "LIVE_CANARY_ACQUISITION_FAILED",
            "fixture_source": "ACQUIRED",
            "bundle_emitted": False,
            "detail": selection["report"],
            "future_fixture_sports": [],
            "live_coverage_certified": False,
            "can_execute": False,
        }))
        return 75
    configs, source = selection["configs"], selection["source"]
    if source == "STATIC":
        sports = future_fixture_sports()
        print(json.dumps({
            "status": "FUTURE_STATIC_FIXTURES_PRESENT" if sports else "LIVE_CANARY_NOT_RUN_STALE_STATIC_FIXTURES",
            "fixture_source": "STATIC",
            "bundle_emitted": False,
            "future_fixture_sports": list(sports),
            "live_coverage_certified": False,
            "can_execute": False,
        }))
        return 0 if sports else 78

    report = selection["report"]
    sports = future_fixture_sports(configs=configs)
    output: dict[str, Any] = {
        "status": {
            "ACQUIRED": "FUTURE_ACQUIRED_ROWS_PRESENT",
            "NO_FUTURE_EVENT": "LIVE_CANARY_NOT_RUN_NO_FUTURE_EVENT",
        }.get(str(report.get("status")), "LIVE_CANARY_ACQUISITION_FAILED"),
        "fixture_source": "ACQUIRED",
        "bundle_emitted": False,
        "future_fixture_sports": list(sports),
        "snapshot_write_mode": report.get("snapshot_write_mode"),
        "snapshot_writes_suppressed": report.get("snapshot_writes_suppressed"),
        "sports": report.get("sports"),
        "blockers": report.get("blockers"),
        "live_coverage_certified": False,
        "can_execute": False,
    }
    code = 0 if sports else 78
    if output["status"] == "LIVE_CANARY_ACQUISITION_FAILED":
        code = 75
    elif emit_path and code == 0:
        # Persist only rows this run proved eligible. A failed write is a real
        # acquisition failure, never a silent re-acquire later.
        try:
            _write_fixture_bundle(emit_path, report)
            output.update({"bundle_emitted": True, "bundle_path": emit_path})
        except OSError as exc:
            output.update({
                "status": "LIVE_CANARY_ACQUISITION_FAILED",
                "code": "CANARY_FIXTURE_BUNDLE_WRITE_FAILED",
                "detail": type(exc).__name__,
                "bundle_path": emit_path,
            })
            code = 75
    print(json.dumps(output))
    return code


def run_primary(
    *,
    sports: tuple[str, ...],
    fixtures: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    outputs = []
    for sport in sports:
        request_id = f"{BASE_RUN}-{sport.lower()}-primary"
        outputs.append(
            _validate(
                _post(_request_for(sport, request_id, fixtures)),
                sport=sport,
                require_publishable=(sport == "MLB"),
            )
        )
    return outputs


def run_stress(
    *,
    sports: tuple[str, ...],
    fixtures: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    jobs: list[tuple[str, str]] = []
    for sport in sports:
        for n in (1, 2):
            jobs.append((sport, f"{BASE_RUN}-{sport.lower()}-stress-{n}"))
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = {
            pool.submit(
                lambda s=sport, rid=request_id: _validate(
                    _post(_request_for(s, rid, fixtures)),
                    sport=s,
                    require_publishable=(s == "MLB"),
                ),
            ): (sport, request_id)
            for sport, request_id in jobs
        }
        for future in as_completed(pending):
            results.append(future.result())

    replay_results = []
    for sport in sports:
        rid = f"{BASE_RUN}-{sport.lower()}-stress-1"
        replay_results.append(
            _validate(
                _post(_request_for(sport, rid, fixtures)),
                sport=sport,
                require_publishable=(sport == "MLB"),
            )
        )

    return {
        "stress_batches": len(results),
        "stress_rows": len(results) * 4,
        "replay_batches": len(replay_results),
        "replay_rows": len(replay_results) * 4,
        "results": sorted(results, key=lambda x: (x["sport"], x["request_id"])),
        "replays": replay_results,
        "can_execute": False,
    }


def self_test() -> None:
    """Validate fixture shape, not historical timestamps as live eligibility."""
    assert set(CONFIGS) == {"NFL", "MLB", "WNBA"}
    for sport, rows in CONFIGS.items():
        assert len(rows) == 4, (sport, len(rows))
        assert len({row["row_key"] for row in rows}) == 4
        assert all(row["sport"] == sport for row in rows)
        assert all(row["direction"] in {"MORE", "LESS"} for row in rows)
        assert all(row["platform"] == "WOW_PRODUCTION_CANARY" for row in rows)
        for row in rows:
            start = datetime.fromisoformat(str(row["event_start_time"]).replace("Z", "+00:00"))
            assert start.utcoffset() is not None, (sport, row["event_id"])
    print(json.dumps({
        "status": "SELF_TEST_PASS",
        "sports": sorted(CONFIGS),
        "rows": 12,
        "future_fixture_sports": list(future_fixture_sports()),
        "live_coverage_certified": False,
        "can_execute": False,
    }))


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode == "self-test":
        self_test()
        return 0
    if mode == "check-live-fixtures":
        return _check_live_fixtures(_option("--emit-fixtures"))
    if mode not in {"primary", "stress", "all"}:
        print(json.dumps({"status": "UNKNOWN_CANARY_MODE", "can_execute": False}))
        return 2

    bundle_input = _option("--fixtures-from")
    selection = _select_fixtures() if bundle_input is None else _select_fixtures(fixtures_from=bundle_input)
    if selection["configs"] is None:
        # Acquisition was requested and could not produce rows. Fail closed at a
        # distinct code; never retry with historical fixtures.
        print(json.dumps({
            "status": "LIVE_CANARY_ACQUISITION_FAILED",
            "fixture_source": selection["source"],
            "detail": selection["report"],
            "future_fixture_sports": [],
            "live_coverage_certified": False,
            "can_execute": False,
        }))
        return 75

    configs = selection["configs"]
    source = selection["source"]
    sports = future_fixture_sports() if source == "STATIC" else future_fixture_sports(configs=configs)
    if not sports:
        # Direct CLI calls fail closed instead of silently reporting live success.
        # The workflow separately records a skipped live step when this occurs.
        status = (
            "LIVE_CANARY_NOT_RUN_STALE_STATIC_FIXTURES"
            if source == "STATIC"
            else "LIVE_CANARY_NOT_RUN_NO_FUTURE_EVENT"
        )
        print(json.dumps({
            "status": status,
            "fixture_source": source,
            "detail": selection["report"],
            "future_fixture_sports": [],
            "live_coverage_certified": False,
            "can_execute": False,
        }))
        return 78
    output: dict[str, Any] = {
        "can_execute": False,
        "fixture_source": source,
        "future_fixture_sports": list(sports),
        "live_coverage_certified": False,
    }
    if selection.get("fixture_input"):
        output["fixture_input"] = selection["fixture_input"]
    if source == "STATIC":
        if mode in {"primary", "all"}:
            output["primary"] = run_primary(sports=sports)
        if mode in {"stress", "all"}:
            output["stress"] = run_stress(sports=sports)
    else:
        if mode in {"primary", "all"}:
            output["primary"] = run_primary(sports=sports, fixtures=configs)
        if mode in {"stress", "all"}:
            output["stress"] = run_stress(sports=sports, fixtures=configs)
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
