from ncaaf_cfbd_client import CFBDResponse
from ncaaf_cfbd_hydrator import hydrate_cfbd_season


def test_games_only_maintenance_uses_one_season_request_without_rating_calls():
    calls = []

    class FakeCFBD:
        def games(self, *, year, week=None, classification=None):
            calls.append(("games", year, week, classification))
            return CFBDResponse(
                endpoint="/games",
                params={"year": year, "classification": classification},
                rows=[
                    {
                        "id": 1,
                        "season": year,
                        "week": 4,
                        "seasonType": "regular",
                        "completed": True,
                        "startDate": "2026-09-20T17:00:00Z",
                        "homeTeam": "Alpha",
                        "awayTeam": "Beta",
                        "homePoints": 31,
                        "awayPoints": 17,
                    }
                ],
            )

        def ratings(self, *_args, **_kwargs):
            raise AssertionError("games-only NCAAF ML maintenance must not request ratings")

    rows = hydrate_cfbd_season(
        FakeCFBD(),
        season=2026,
        weeks=range(1, 21),
        rating_families=(),
        classification="fbs",
    )

    assert calls == [("games", 2026, None, "fbs")]
    assert len(rows) == 1
    assert rows[0].endpoint == "/games"
    assert rows[0].week is None
    assert rows[0].request_params == {"year": 2026, "classification": "fbs"}
    assert rows[0].response_rows[0]["week"] == 4
    assert rows[0].can_execute is False
