from types import SimpleNamespace

import pytest

from basketball_specialist_pipeline import load_games


class _Query:
    def __init__(self, pages):
        self.pages = pages
        self.orders = []
        self._range = (0, 999)

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def order(self, column, *_args, **_kwargs):
        self.orders.append(column)
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        page = self._range[0] // 1000
        data = self.pages[page] if page < len(self.pages) else []
        return SimpleNamespace(data=data)


class _Client:
    def __init__(self, pages):
        self.query = _Query(pages)

    def table(self, _name):
        return self.query


def _row(game_id: str, game_date: str = "2024-01-01"):
    return {
        "game_id": game_id,
        "season": 2024,
        "game_date": game_date,
        "home_team_id": "H",
        "away_team_id": "A",
        "home_score": 100,
        "away_score": 90,
    }


def test_load_games_uses_total_order_for_range_pagination():
    client = _Client([[_row("1"), _row("2")]])
    games = load_games(client, "NBA")
    assert [g.game_id for g in games] == ["1", "2"]
    assert client.query.orders[:2] == ["game_date", "game_id"]


def test_load_games_fails_closed_if_paginated_source_repeats_game_id():
    first_page = [_row(str(i), "2024-01-01") for i in range(1000)]
    second_page = [_row("999", "2024-01-02")]
    client = _Client([first_page, second_page])
    with pytest.raises(RuntimeError, match="TRAINING_PAGINATION_DUPLICATE_GAME_IDS"):
        load_games(client, "NBA")
