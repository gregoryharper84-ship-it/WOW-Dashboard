from types import SimpleNamespace

import pytest

from v17 import spread_market_evidence as evidence


def test_scope_resolves_canonical_spreads_and_requested_books(monkeypatch):
    def catalog(_client, kind):
        if kind == "MARKET":
            return [
                {"provider_id": "1", "canonical_key": "h2h", "display_name": "moneyline", "active": True},
                {"provider_id": "2", "canonical_key": "spreads", "display_name": "handicap", "active": True},
            ]
        return [
            {"provider_id": "3", "display_name": "Pinnacle", "active": True},
            {"provider_id": "19", "display_name": "Draftkings", "active": True},
            {"provider_id": "23", "display_name": "Fanduel", "active": True},
        ]

    monkeypatch.setattr(evidence, "_catalog_rows", catalog)
    scope = evidence.resolve_spread_collection_scope(object())
    assert scope["market_ids"] == ("2",)
    assert scope["affiliate_ids"] == ("3", "19", "23")
    assert scope["canonical_market_key"] == "spreads"
    assert scope["prediction_authority"] is False
    assert scope["can_execute"] is False


def test_scope_fails_closed_when_spread_market_is_ambiguous(monkeypatch):
    monkeypatch.setattr(
        evidence,
        "_catalog_rows",
        lambda _client, kind: [
            {"provider_id": "2", "canonical_key": "spreads", "display_name": "handicap", "active": True},
            {"provider_id": "22", "canonical_key": "spreads", "display_name": "spread", "active": True},
        ] if kind == "MARKET" else [],
    )
    with pytest.raises(evidence.SpreadMarketEvidenceError) as exc:
        evidence.resolve_spread_collection_scope(object())
    assert exc.value.code == "SPREAD_MARKET_ID_UNRESOLVED"


def test_collection_passes_only_spread_market_and_is_evidence_only(monkeypatch):
    monkeypatch.setattr(
        evidence,
        "resolve_spread_collection_scope",
        lambda *_args, **_kwargs: {
            "market_ids": ("2",),
            "affiliate_ids": ("3", "19", "23"),
            "book_names": ("Pinnacle", "Draftkings", "Fanduel"),
            "canonical_market_key": "spreads",
            "prediction_authority": False,
            "can_execute": False,
        },
    )
    calls = []

    def collect(*_args, **kwargs):
        calls.append(kwargs)
        return {
            "status": "COMPLETE",
            "rows_written": 12,
            "datapoints": 12,
            "provider_status": 200,
            "reason_code": None,
        }

    monkeypatch.setattr(evidence.ingestor, "collect_snapshot", collect)
    result = evidence.collect_spread_snapshot(object(), sport="NFL", slate_date="2026-09-27")
    assert calls[0]["market_ids"] == ("2",)
    assert calls[0]["allow_delta"] is False
    assert calls[0]["include_all_periods"] is False
    assert result["rows_written"] == 12
    assert result["spread_line_used_as_feature"] is False
    assert result["market_probability_substitution_used"] is False
    assert result["moneyline_to_spread_conversion_used"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_collection_preserves_typed_provider_failure(monkeypatch):
    monkeypatch.setattr(
        evidence,
        "resolve_spread_collection_scope",
        lambda *_args, **_kwargs: {
            "market_ids": ("2",),
            "affiliate_ids": ("3", "19"),
            "book_names": ("Pinnacle", "Draftkings"),
        },
    )
    monkeypatch.setattr(
        evidence.ingestor,
        "collect_snapshot",
        lambda *_args, **_kwargs: {"status": "FAILED", "reason_code": "RUNDOWN_QUOTA_EXHAUSTED"},
    )
    with pytest.raises(evidence.SpreadMarketEvidenceError) as exc:
        evidence.collect_spread_snapshot(object(), sport="NFL", slate_date="2026-09-27")
    assert exc.value.code == "RUNDOWN_QUOTA_EXHAUSTED"


def test_unsupported_sport_does_not_fall_back():
    with pytest.raises(evidence.SpreadMarketEvidenceError) as exc:
        evidence.collect_spread_snapshot(SimpleNamespace(), sport="MLB", slate_date="2026-09-27")
    assert exc.value.code == "SPREAD_MARKET_SPORT_UNSUPPORTED"
