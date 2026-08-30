from types import SimpleNamespace

from prop_settlement import SettlementRule
from prop_settlement_registry import (
    SETTLEMENT_PROVIDER_UNRESOLVED,
    SETTLEMENT_RULE_CONFLICT,
    SETTLEMENT_RULE_UNRESOLVED,
    normalize_provider,
    resolve_prop_settlement_rule,
)


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []

    def select(self, _columns):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def execute(self):
        rows = self.rows
        for key, value in self.filters:
            rows = [row for row in rows if row.get(key) == value]
        return SimpleNamespace(data=rows)


class FakeClient:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "wow_prop_settlement_rule_registry"
        return FakeQuery(self.rows)


class BrokenClient:
    def table(self, _name):
        raise RuntimeError("registry unavailable")


def _row(**overrides):
    row = {
        "rule_id": "11111111-1111-1111-1111-111111111111",
        "provider": "PRIZEPICKS",
        "sport": "MLB",
        "stat_type": "*",
        "period": "FULL_GAME",
        "direction": "MORE",
        "settlement_basis": "PRIZEPICKS_OFFICIAL_SCORING",
        "boundary_operator": "GT",
        "equality_treatment": "PUSH",
        "void_treatment": "REMOVE_LEG_REPRICE",
        "money_semantics": "LINEUP_CONTEXT_REQUIRED",
        "rule_version": "PRIZEPICKS_PLAYER_PICKS_2026_08_11",
        "source_ref": "https://www.prizepicks.com/help-center/dnps-reboots-and-ties",
        "source_hash": "abc123",
        "effective_from": "2026-08-11T00:00:00+00:00",
        "effective_to": None,
        "lifecycle_state": "REVIEWED_CERTIFIED",
        "can_execute": False,
    }
    row.update(overrides)
    return row


def _resolve(rows, **kwargs):
    params = {
        "client": FakeClient(rows),
        "provider": "PrizePicks",
        "sport": "MLB",
        "stat_type": "PITCHER_STRIKEOUTS",
        "period": "FULL_GAME",
        "direction": "MORE",
        "event_start_time": "2026-08-30T23:20:00+00:00",
    }
    params.update(kwargs)
    return resolve_prop_settlement_rule(**params)


def test_provider_alias_normalization_is_server_owned():
    assert normalize_provider("Prize Picks") == "PRIZEPICKS"
    assert normalize_provider("prizepicks") == "PRIZEPICKS"


def test_certified_db_wildcard_rule_hydrates_exact_candidate():
    result = _resolve([_row()])
    assert result.status == "PASS"
    assert result.authority == "SERVER_DB_REGISTRY_REVIEWED_CERTIFIED"
    assert result.rule is not None
    assert result.rule.boundary_operator == "GT"
    assert result.rule.equality_treatment == "PUSH"
    assert result.rule.void_treatment == "REMOVE_LEG_REPRICE"
    assert result.rule.money_semantics == "LINEUP_CONTEXT_REQUIRED"
    assert result.observed_rule_status == "NOT_SUPPLIED"
    assert result.can_execute is False


def test_code_certified_rule_hydrates_when_db_has_no_rule():
    result = _resolve([])
    assert result.status == "PASS"
    assert result.authority == "SERVER_CODE_REGISTRY_REVIEWED_CERTIFIED"
    assert result.provider == "PRIZEPICKS"
    assert result.rule_version == "PRIZEPICKS_PLAYER_PICKS_2026_08_11"
    assert result.rule is not None
    assert result.rule.money_semantics == "LINEUP_CONTEXT_REQUIRED"
    assert result.can_execute is False


def test_code_certified_rule_hydrates_when_db_registry_is_unreachable():
    result = _resolve([], client=BrokenClient())
    assert result.status == "PASS"
    assert result.authority == "SERVER_CODE_REGISTRY_REVIEWED_CERTIFIED"
    assert result.rule is not None
    assert result.source_ref.startswith("https://www.prizepicks.com/")
    assert result.source_hash


def test_exact_stat_db_rule_beats_db_wildcard():
    result = _resolve([
        _row(rule_id="wild"),
        _row(
            rule_id="exact",
            stat_type="PITCHER_STRIKEOUTS",
            rule_version="EXACT_V1",
            source_hash="exacthash",
        ),
    ])
    assert result.status == "PASS"
    assert result.rule_id == "exact"
    assert result.rule_version == "EXACT_V1"


def test_missing_provider_fails_closed():
    result = _resolve([_row()], provider=None)
    assert result.status == "HOLD"
    assert result.blocker == SETTLEMENT_PROVIDER_UNRESOLVED
    assert result.rule is None


def test_effective_date_before_any_reviewed_rule_fails_closed():
    result = _resolve(
        [_row(effective_from="2026-09-01T00:00:00+00:00")],
        event_start_time="2026-08-01T23:20:00+00:00",
    )
    assert result.status == "HOLD"
    assert result.blocker == SETTLEMENT_RULE_UNRESOLVED


def test_unknown_provider_without_certified_rule_fails_closed():
    result = _resolve([], provider="UNKNOWN_BOOK")
    assert result.status == "HOLD"
    assert result.blocker == SETTLEMENT_RULE_UNRESOLVED


def test_conflicting_active_certified_db_rules_fail_closed():
    result = _resolve([
        _row(rule_id="a"),
        _row(rule_id="b", equality_treatment="LOSS", rule_version="CONFLICT_V2"),
    ])
    assert result.status == "HOLD"
    assert result.blocker == SETTLEMENT_RULE_CONFLICT


def test_caller_observed_rule_is_cross_check_only_and_conflict_is_quarantined():
    observed = SettlementRule(
        settlement_basis="PRIZEPICKS_OFFICIAL_SCORING",
        boundary_operator="GE",
        equality_treatment="WIN",
        void_treatment="RETURN_STAKE",
        rule_version="CALLER_INVENTED",
        source="CALLER",
    )
    result = _resolve([_row()], observed_rule=observed)
    assert result.status == "HOLD"
    assert result.blocker == SETTLEMENT_RULE_CONFLICT
    assert result.observed_rule_status == "CONFLICT_QUARANTINED"
    assert result.rule is None
