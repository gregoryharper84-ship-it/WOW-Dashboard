from types import SimpleNamespace

import pytest

from v17.entity_alias_gateway import (
    EntityAliasGateway,
    EntityIdentityUnresolved,
    RESOLVER_RPC,
)


class _RPC:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return SimpleNamespace(data=self.payload)


class _DB:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _RPC(self.payload)


def test_exact_match_resolution_returns_governed_identity_without_probability():
    db = _DB(
        [
            {
                "canonical_player_id": "11111111-1111-1111-1111-111111111111",
                "canonical_name": "Patrick Mahomes",
                "current_team_key": "KC",
                "provider_player_id": "3139477",
                "is_resolved": True,
                "gateway_mode": "SHADOW",
                "can_execute": False,
            }
        ]
    )

    resolved = EntityAliasGateway(db).resolve_player(
        source_feed="PrizePicks",
        sport="nfl",
        raw_alias="Patrick Mahomes II",
        run_id="run-1",
        row_key="row-1",
        context_payload={"event_id": "NFL:20261002:KC:DEN"},
    )

    assert resolved.canonical_player_id == "11111111-1111-1111-1111-111111111111"
    assert resolved.canonical_name == "Patrick Mahomes"
    assert resolved.current_team_key == "KC"
    assert resolved.provider_player_id == "3139477"
    assert resolved.can_execute is False
    assert db.calls == [
        (
            RESOLVER_RPC,
            {
                "p_source_feed": "prizepicks",
                "p_sport": "NFL",
                "p_raw_alias": "Patrick Mahomes II",
                "p_run_id": "run-1",
                "p_row_key": "row-1",
                "p_context_payload": {"event_id": "NFL:20261002:KC:DEN"},
            },
        )
    ]


def test_unresolved_identity_fails_closed_with_typed_rejection():
    db = _DB(
        [
            {
                "canonical_player_id": None,
                "canonical_name": None,
                "current_team_key": None,
                "provider_player_id": None,
                "is_resolved": False,
                "gateway_mode": "SHADOW",
                "can_execute": False,
            }
        ]
    )

    with pytest.raises(EntityIdentityUnresolved) as excinfo:
        EntityAliasGateway(db).resolve_player(
            source_feed="prizepicks",
            sport="NFL",
            raw_alias="P. Mahomes",
            run_id="run-2",
            row_key="row-2",
        )

    detail = excinfo.value.as_detail()
    assert detail["code"] == "REJECTED_UNRESOLVED_IDENTITY"
    assert detail["probability_publishable"] is False
    assert detail["can_execute"] is False


def test_gateway_does_not_fuzzy_rewrite_runtime_alias():
    db = _DB(
        [
            {
                "canonical_player_id": None,
                "is_resolved": False,
                "gateway_mode": "SHADOW",
                "can_execute": False,
            }
        ]
    )

    with pytest.raises(EntityIdentityUnresolved):
        EntityAliasGateway(db).resolve_player(
            source_feed="draftkings",
            sport="NFL",
            raw_alias="P Mahomess",
        )

    _, params = db.calls[0]
    assert params["p_raw_alias"] == "P Mahomess"


def test_shadow_observation_records_unresolved_without_forcing_rejection():
    db = _DB(
        [
            {
                "canonical_player_id": None,
                "canonical_name": None,
                "current_team_key": None,
                "provider_player_id": None,
                "is_resolved": False,
                "gateway_mode": "SHADOW",
                "can_execute": False,
            }
        ]
    )

    observed = EntityAliasGateway(db).observe_player(
        source_feed="prizepicks",
        sport="NFL",
        raw_alias="P. Mahomes",
        row_key="row-shadow",
    )

    assert observed.is_resolved is False
    assert observed.gateway_mode == "SHADOW"
    assert observed.canonical_player_id is None
    assert observed.can_execute is False
