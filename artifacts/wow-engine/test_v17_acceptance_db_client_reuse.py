from __future__ import annotations

import threading

import api_ncaaf_acceptance as subject


def _clear_main_thread_client() -> None:
    if hasattr(subject._DB_CLIENT_LOCAL, "client"):
        delattr(subject._DB_CLIENT_LOCAL, "client")


def test_db_client_reuses_one_client_per_worker_thread(monkeypatch):
    _clear_main_thread_client()
    created = []

    def make_client():
        client = object()
        created.append(client)
        return client

    monkeypatch.setattr(subject.base.market_api.prod, "get_client", make_client)

    try:
        first = subject._db_client()
        second = subject._db_client()
        assert first is second
        assert created == [first]

        worker_clients = []

        def worker():
            worker_first = subject._db_client()
            worker_second = subject._db_client()
            worker_clients.extend([worker_first, worker_second])

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()

        assert len(created) == 2
        assert worker_clients[0] is worker_clients[1]
        assert worker_clients[0] is not first
    finally:
        _clear_main_thread_client()

