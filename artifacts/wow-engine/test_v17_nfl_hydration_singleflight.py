from concurrent.futures import ThreadPoolExecutor
import time

import nfl_prop_auto_hydration as subject


class _Response:
    status_code = 200
    content = b"player_display_name,season,week\nTest Player,2026,1\n"
    text = content.decode("utf-8")

    def json(self):
        raise AssertionError("CSV path should not parse JSON")


def test_live_nflverse_cache_singleflights_parallel_rows(monkeypatch):
    calls = {"n": 0}

    def fake_get(*args, **kwargs):
        calls["n"] += 1
        time.sleep(0.05)
        return _Response()

    subject._CSV_CACHE.clear()
    monkeypatch.setattr(subject.httpx, "get", fake_get)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(
                subject._cached_nflverse_rows,
                2026,
                http_get=fake_get,
                now_ts=1000.0,
            )
            for _ in range(4)
        ]
        results = [future.result() for future in futures]

    assert calls["n"] == 1
    assert all(result[0] == results[0][0] for result in results)
    assert all(result[1] == results[0][1] for result in results)
