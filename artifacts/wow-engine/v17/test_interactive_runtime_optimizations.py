from __future__ import annotations

import prop_fitted_provider
import v17.interactive_runtime_optimizations as subject


class Prod:
    def __init__(self):
        self.calls = 0

    def _runtime_capability(self, key):
        self.calls += 1
        return {"capability_key": key, "capability_status": "AVAILABLE", "can_execute": False}


class Market:
    def __init__(self):
        self.prod = Prod()
        self.route_calls = 0

    def _prop_route_artifact(self, sport, stat):
        self.route_calls += 1
        return {"ok": True, "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY", "sport": sport, "stat_type": stat, "can_execute": False}


def test_positive_registry_reads_are_reused_within_interactive_burst(monkeypatch):
    subject.clear_interactive_runtime_caches()
    market = Market()
    resolver_calls = {"n": 0}
    artifact = {"artifact": "ready"}

    def resolver(client, *, sport, stat_type, feature_schema_version):
        resolver_calls["n"] += 1
        return artifact

    monkeypatch.setattr(prop_fitted_provider, "resolve_certified_artifact", resolver)
    assert subject.install_interactive_runtime_optimizations(market) is True

    assert market.prod._runtime_capability("PROP")["capability_status"] == "AVAILABLE"
    assert market.prod._runtime_capability("PROP")["capability_status"] == "AVAILABLE"
    assert market.prod.calls == 1

    assert market._prop_route_artifact("NFL", "RECEIVING_YARDS")["ok"] is True
    assert market._prop_route_artifact("NFL", "RECEIVING_YARDS")["ok"] is True
    assert market.route_calls == 1

    client = object()
    first = prop_fitted_provider.resolve_certified_artifact(
        client, sport="NFL", stat_type="RECEIVING_YARDS", feature_schema_version="PROP_FEATURES_V1"
    )
    second = prop_fitted_provider.resolve_certified_artifact(
        client, sport="NFL", stat_type="RECEIVING_YARDS", feature_schema_version="PROP_FEATURES_V1"
    )
    assert first == artifact
    assert second == artifact
    assert resolver_calls["n"] == 1


def test_unavailable_capability_is_never_cached(monkeypatch):
    subject.clear_interactive_runtime_caches()

    class BlockedProd(Prod):
        def _runtime_capability(self, key):
            self.calls += 1
            return {"capability_key": key, "capability_status": "UNAVAILABLE", "can_execute": False}

    market = Market()
    market.prod = BlockedProd()
    monkeypatch.setattr(prop_fitted_provider, "resolve_certified_artifact", lambda *args, **kwargs: None)
    assert subject.install_interactive_runtime_optimizations(market) is True
    market.prod._runtime_capability("PROP")
    market.prod._runtime_capability("PROP")
    assert market.prod.calls == 2
