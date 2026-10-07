from __future__ import annotations

import pytest

from v17.wnba_roster_identity_fallback import (
    PROVIDER_ID,
    WNBARosterIdentityFallbackError,
    fetch_espn_wnba_team_roster,
)


class Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def test_espn_roster_fallback_is_identity_only_and_never_relabels_player_id():
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response(
            {
                "athletes": [
                    {
                        "position": {"name": "Guards"},
                        "items": [
                            {
                                "id": "espn-123",
                                "fullName": "Example Player",
                                "position": {"abbreviation": "G"},
                            }
                        ],
                    }
                ]
            }
        )

    rows = fetch_espn_wnba_team_roster(
        {"teamTricode": "NYL"},
        http_get=get,
    )
    assert len(rows) == 1
    assert rows[0]["PLAYER"] == "Example Player"
    assert rows[0]["PLAYER_ID"] == ""
    assert rows[0]["_WOW_ESPN_PLAYER_ID"] == "espn-123"
    assert rows[0]["_WOW_ROSTER_SOURCE"] == PROVIDER_ID
    assert rows[0]["_WOW_IDENTITY_ONLY"] is True
    assert "/basketball/wnba/teams/NYL/roster" in calls[0][0]


def test_espn_roster_fallback_requires_bound_team_identity():
    with pytest.raises(WNBARosterIdentityFallbackError) as exc:
        fetch_espn_wnba_team_roster({}, http_get=lambda *_a, **_k: None)
    assert exc.value.code == "ESPN_WNBA_TEAM_TRICODE_MISSING"
