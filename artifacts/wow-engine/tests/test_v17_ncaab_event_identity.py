import pytest

from v17.ncaab_event_identity import (
    NCAABEventIdentityError,
    resolve_ncaab_current_event_identity,
    fetch_ncaab_schedule,
    season_end_year,
)

START = "2026-11-05T00:00:00Z"


def _row(*, game_id="401999999", home="2641", away="204", start=START, valid="True"):
    return {"game_id": game_id, "home_id": home, "away_id": away,
            "game_date_time": start, "time_valid": valid}


def _resolve(rows, **kwargs):
    return resolve_ncaab_current_event_identity(
        event_start_time=START, home_team_alias="espn-2641",
        away_team_alias="204", fetcher=lambda _season: rows, **kwargs,
    )


def test_exact_team_start_match_is_only_canonical_path():
    output = _resolve([_row()])
    assert output["event_id"] == "espn-401999999"
    assert output["home_team_id"] == "espn-2641"
    assert output["away_team_id"] == "espn-204"
    assert output["identity_provider"] == "SPORTSDATAVERSE_ESPN_MBB_SCHEDULE"
    assert output["market_features_used"] is False
    assert output["prediction_authority"] is False
    assert output["can_execute"] is False


@pytest.mark.parametrize("rows,code", [
    ([], "NCAAB_CANONICAL_EVENT_NOT_FOUND"),
    ([_row(home="204", away="2641")], "NCAAB_CANONICAL_EVENT_NOT_FOUND"),
    ([_row(start="2026-11-05T02:00:00Z")], "NCAAB_CANONICAL_EVENT_NOT_FOUND"),
    ([_row(valid="False")], "NCAAB_CANONICAL_EVENT_NOT_FOUND"),
    ([_row(), _row(game_id="402000000")], "NCAAB_CANONICAL_EVENT_AMBIGUOUS"),
])
def test_no_guess_on_missing_swapped_time_invalid_or_ambiguous_match(rows, code):
    with pytest.raises(NCAABEventIdentityError) as raised:
        _resolve(rows)
    assert raised.value.code == code


@pytest.mark.parametrize("home,away,start,code", [
    ("", "204", START, "NCAAB_PROVIDER_TEAM_ALIAS_INVALID"),
    ("2641", "2641", START, "NCAAB_PROVIDER_TEAM_ALIAS_INVALID"),
    ("Texas Tech", "204", START, "NCAAB_PROVIDER_TEAM_ALIAS_INVALID"),
    ("2641", "204", "2026-11-05T00:00:00", "NCAAB_EVENT_TIME_INVALID"),
])
def test_invalid_alias_or_naive_event_time_fails_closed(home, away, start, code):
    with pytest.raises(NCAABEventIdentityError) as raised:
        resolve_ncaab_current_event_identity(
            event_start_time=start, home_team_alias=home,
            away_team_alias=away, fetcher=lambda _year: [_row()],
        )
    assert raised.value.code == code


def test_cross_year_season_matches_espn_season_end_year():
    assert season_end_year(__import__("datetime").datetime.fromisoformat("2026-11-05T00:00:00+00:00")) == 2027
    assert season_end_year(__import__("datetime").datetime.fromisoformat("2027-03-05T00:00:00+00:00")) == 2027


class _Response:
    def __init__(self, status=200, text=""):
        self.status_code = status
        self.text = text


class _Session:
    def __init__(self, response):
        self.response = response
        self.url = None

    def get(self, url, **kwargs):
        self.url = url
        return self.response


def test_sportsdataverse_429_is_typed_not_unavailable_model():
    sess = _Session(_Response(status=429))
    with pytest.raises(NCAABEventIdentityError) as raised:
        fetch_ncaab_schedule(2027, session=sess)
    assert raised.value.code == "NCAAB_SCHEDULE_SOURCE_RATE_LIMITED"
    assert "mbb_schedule_2027.csv" in sess.url


def test_sportsdataverse_schema_drift_is_typed():
    sess = _Session(_Response(text="id,other\n1,x\n"))
    with pytest.raises(NCAABEventIdentityError) as raised:
        fetch_ncaab_schedule(2027, session=sess)
    assert raised.value.code == "NCAAB_SCHEDULE_SOURCE_SCHEMA_DRIFT"


def test_bad_schedule_time_not_evidence_of_identity():
    with pytest.raises(NCAABEventIdentityError) as raised:
        _resolve([_row(start="2026-11-04T19:00:00")])
    assert raised.value.code == "NCAAB_CANONICAL_EVENT_NOT_FOUND"


def test_oversized_untrusted_team_id_rejected_before_lookup():
    with pytest.raises(NCAABEventIdentityError) as raised:
        resolve_ncaab_current_event_identity(
            event_start_time=START, home_team_alias="9" * 10000,
            away_team_alias="204", fetcher=lambda _year: [_row()],
        )
    assert raised.value.code == "NCAAB_PROVIDER_TEAM_ALIAS_INVALID"


def test_duplicate_identical_schedule_row_is_one_event():
    assert _resolve([_row(), _row()])["event_id"] == "espn-401999999"


def test_concurrent_identity_lookups_do_not_corrupt_cached_schedule(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import v17.ncaab_event_identity as identity

    identity.reset_cache()
    monkeypatch.setattr(identity, "fetch_ncaab_schedule", lambda _year: [_row()])
    def lookup(_index):
        return identity.resolve_ncaab_current_event_identity(
            event_start_time=START, home_team_alias="2641", away_team_alias="204",
        )["event_id"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(lookup, range(24))) == ["espn-401999999"] * 24
    identity.reset_cache()


def test_unavailable_source_is_cached_per_season_during_shared_scan(monkeypatch):
    import v17.ncaab_event_identity as identity

    identity.reset_cache()
    calls = []

    def fail(_season):
        calls.append(_season)
        raise identity.NCAABEventIdentityError("NCAAB_SCHEDULE_SOURCE_HTTP_FAILED")

    monkeypatch.setattr(identity, "fetch_ncaab_schedule", fail)
    for _ in range(26):
        with pytest.raises(NCAABEventIdentityError) as raised:
            identity.resolve_ncaab_current_event_identity(
                event_start_time=START, home_team_alias="2641",
                away_team_alias="204",
            )
        assert raised.value.code == "NCAAB_SCHEDULE_SOURCE_HTTP_FAILED"
    assert calls == [2027], "one failed source call must not block 26 candidate rows"
    identity.reset_cache()
    with pytest.raises(NCAABEventIdentityError):
        identity.resolve_ncaab_current_event_identity(
            event_start_time=START, home_team_alias="2641", away_team_alias="204",
        )
    assert calls == [2027, 2027]
    identity.reset_cache()


def test_success_clears_expired_negative_cache(monkeypatch):
    import v17.ncaab_event_identity as identity

    identity.reset_cache()
    identity._FAILURE_CACHE[2027] = (-1.0, "NCAAB_SCHEDULE_SOURCE_RATE_LIMITED")
    monkeypatch.setattr(identity, "fetch_ncaab_schedule", lambda _year: [_row()])
    actual = identity.resolve_ncaab_current_event_identity(
        event_start_time=START, home_team_alias="2641", away_team_alias="204",
    )
    assert actual["event_id"] == "espn-401999999"
    assert 2027 not in identity._FAILURE_CACHE
    identity.reset_cache()


def test_published_sportsdataverse_schedule_contract_can_reconcile_csv():
    # Producer columns documented by hoopR-mbb-data/docs/datasets/schedules.md.
    csv_text = (
        "id,game_id,home_id,away_id,game_date_time,time_valid,date,status_type_state\n"
        "401999999,401999999,2641,204,2026-11-04T19:00:00-05:00,True,2026-11-04,pre\n"
    )
    rows = fetch_ncaab_schedule(
        2027, session=_Session(_Response(text=csv_text))
    )
    result = _resolve(rows)
    assert result["event_id"] == "espn-401999999"
    assert result["prediction_authority"] is False
