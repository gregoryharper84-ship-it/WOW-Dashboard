from v17 import multisport_prop_live_canary as subject


def test_multisport_live_canary_contract_is_four_rows_per_supported_sport():
    assert set(subject.CONFIGS) == {"NFL", "MLB", "WNBA"}
    for sport, rows in subject.CONFIGS.items():
        assert len(rows) == 4
        assert len({row["row_key"] for row in rows}) == 4
        assert all(row["sport"] == sport for row in rows)
        assert all(row["direction"] in {"MORE", "LESS"} for row in rows)
        assert all(row["platform"] == "WOW_PRODUCTION_CANARY" for row in rows)
        assert all(row["source_type"] == "NORMALIZED" for row in rows)


def test_multisport_live_canary_preserves_nonexecution():
    assert subject.ORIGIN.startswith("https://")
    assert subject.CONFIGS["MLB"][0]["event_id"].startswith("MLB:")
    assert subject.CONFIGS["WNBA"][0]["event_id"].startswith("WOW:WNBA:")
