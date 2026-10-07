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
from typing import Any

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


def _request_for(sport: str, request_id: str) -> dict[str, Any]:
    return {"request_id": request_id, "response_mode": "COMPACT", "rows": deepcopy(CONFIGS[sport])}


def run_primary() -> list[dict[str, Any]]:
    outputs = []
    for sport in ("NFL", "MLB", "WNBA"):
        request_id = f"{BASE_RUN}-{sport.lower()}-primary"
        outputs.append(
            _validate(
                _post(_request_for(sport, request_id)),
                sport=sport,
                require_publishable=(sport == "MLB"),
            )
        )
    return outputs


def run_stress() -> dict[str, Any]:
    jobs: list[tuple[str, str]] = []
    for sport in ("NFL", "MLB", "WNBA"):
        for n in (1, 2):
            jobs.append((sport, f"{BASE_RUN}-{sport.lower()}-stress-{n}"))
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = {
            pool.submit(
                lambda s=sport, rid=request_id: _validate(
                    _post(_request_for(s, rid)),
                    sport=s,
                    require_publishable=(s == "MLB"),
                ),
            ): (sport, request_id)
            for sport, request_id in jobs
        }
        for future in as_completed(pending):
            results.append(future.result())

    replay_results = []
    for sport in ("NFL", "MLB", "WNBA"):
        rid = f"{BASE_RUN}-{sport.lower()}-stress-1"
        replay_results.append(
            _validate(
                _post(_request_for(sport, rid)),
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
    assert set(CONFIGS) == {"NFL", "MLB", "WNBA"}
    now = datetime.now(timezone.utc)
    for sport, rows in CONFIGS.items():
        assert len(rows) == 4, (sport, len(rows))
        assert len({row["row_key"] for row in rows}) == 4
        assert all(row["sport"] == sport for row in rows)
        assert all(row["direction"] in {"MORE", "LESS"} for row in rows)
        assert all(row["platform"] == "WOW_PRODUCTION_CANARY" for row in rows)
        for row in rows:
            start = datetime.fromisoformat(str(row["event_start_time"]).replace("Z", "+00:00"))
            if start <= now:
                raise AssertionError(
                    f"{sport}:STALE_CANARY_EVENT:{row['event_id']}:{row['event_start_time']}"
                )
    print(json.dumps({"status": "SELF_TEST_PASS", "sports": sorted(CONFIGS), "rows": 12, "can_execute": False}))


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode == "self-test":
        self_test()
        return 0
    output: dict[str, Any] = {"can_execute": False}
    if mode in {"primary", "all"}:
        output["primary"] = run_primary()
    if mode in {"stress", "all"}:
        output["stress"] = run_stress()
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
