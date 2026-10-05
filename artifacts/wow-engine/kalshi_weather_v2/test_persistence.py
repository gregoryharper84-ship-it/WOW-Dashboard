from types import SimpleNamespace

import pytest

from kalshi_weather_v2.persistence import KalshiWeatherPersistence, KalshiWeatherPersistenceError, content_id
from kalshi_weather_v2.portfolio_risk import (
    DependenceMode,
    PortfolioPosition,
    PortfolioScenarioEngine,
    PositionSide,
    WeatherScenario,
    build_weather_event,
)
from kalshi_weather_v2.validation_meta import (
    BaselineKind,
    BaselinePrediction,
    ComplexityGatePolicy,
    ProbabilityValidationSample,
    SettlementRedTeamCategory,
    SettlementRedTeamFixture,
    SettlementRedTeamObservation,
    ValidationMetaLayer,
)
from kalshi_weather_v2.probability_change_ledger import (
    AttributionDomain,
    ProbabilityAttributionComponent,
    build_probability_change_record,
)


class FakeQuery:
    def __init__(self, table):
        self.table = table
        self.filters = []
        self.insert_row = None
        self.limit_n = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def lte(self, key, value):
        self.filters.append(("lte:" + key, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        self.limit_n = value
        return self

    def insert(self, row):
        self.insert_row = dict(row)
        return self

    def execute(self):
        if self.insert_row is not None:
            pk = self.table.primary_key
            key = self.insert_row[pk]
            if key in self.table.rows:
                raise RuntimeError("duplicate")
            self.table.rows[key] = dict(self.insert_row)
            return SimpleNamespace(data=[dict(self.insert_row)])

        rows = list(self.table.rows.values())
        for key, value in self.filters:
            if key.startswith("lte:"):
                field = key[4:]
                rows = [row for row in rows if row.get(field) <= value]
            else:
                rows = [row for row in rows if row.get(key) == value]
        if self.limit_n is not None:
            rows = rows[: self.limit_n]
        return SimpleNamespace(data=[dict(row) for row in rows])


class FakeTable:
    def __init__(self, primary_key):
        self.primary_key = primary_key
        self.rows = {}


class FakeClient:
    def __init__(self):
        self.tables = {
            "wow_kalshi_weather_predictions": FakeTable("prediction_id"),
            "wow_kalshi_weather_outcomes": FakeTable("outcome_id"),
            "wow_kalshi_weather_probability_changes": FakeTable("probability_change_id"),
            "wow_kalshi_weather_portfolio_risk_snapshots": FakeTable("risk_snapshot_id"),
            "wow_kalshi_weather_validation_meta_reports": FakeTable("report_id"),
            "wow_runtime_capabilities": FakeTable("capability_key"),
        }

    def table(self, name):
        table = self.tables.setdefault(name, FakeTable("id"))
        return FakeQuery(table)


def test_content_id_is_deterministic_and_prefix_scoped():
    payload = {"b": 2, "a": 1}
    assert content_id("x", payload) == content_id("x", {"a": 1, "b": 2})
    assert content_id("x", payload) != content_id("y", payload)


def test_insert_exact_retry_returns_existing_identical_row():
    client = FakeClient()
    store = KalshiWeatherPersistence(client)
    row = {"prediction_id": "p1", "ticker": "T", "can_execute": False}
    first = store._insert_exact("wow_kalshi_weather_predictions", "prediction_id", row)
    second = store._insert_exact("wow_kalshi_weather_predictions", "prediction_id", row)
    assert first == second
    assert len(client.tables["wow_kalshi_weather_predictions"].rows) == 1


def test_insert_exact_identity_collision_fails_closed():
    client = FakeClient()
    store = KalshiWeatherPersistence(client)
    store._insert_exact(
        "wow_kalshi_weather_predictions",
        "prediction_id",
        {"prediction_id": "p1", "ticker": "T1", "can_execute": False},
    )
    with pytest.raises(KalshiWeatherPersistenceError) as exc:
        store._insert_exact(
            "wow_kalshi_weather_predictions",
            "prediction_id",
            {"prediction_id": "p1", "ticker": "T2", "can_execute": False},
        )
    assert exc.value.code == "KALSHI_WEATHER_IDENTITY_COLLISION"


def test_missing_runtime_capability_fails_closed():
    client = FakeClient()
    row = KalshiWeatherPersistence(client).load_runtime_capability()
    assert row["capability_status"] == "UNAVAILABLE"
    assert row["can_execute"] is False


def test_outcome_scoring_does_not_require_publication():
    client = FakeClient()
    store = KalshiWeatherPersistence(client)
    row = store.persist_outcome(
        outcome_id="o1",
        prediction_id="p1",
        settled_at="2026-09-10T18:00:00Z",
        settlement_source_name="Synoptic Data",
        settlement_source_url=None,
        settled_value=82.1,
        yes_outcome=True,
        settlement_payload={"status": "normal"},
        p_yes=0.75,
    )
    assert abs(row["brier_score"] - 0.0625) < 1e-12
    assert row["log_loss"] > 0


def test_probability_change_persistence_is_idempotent_and_append_only_shaped():
    client = FakeClient()
    store = KalshiWeatherPersistence(client)
    component = ProbabilityAttributionComponent(
        component_id="station",
        domain=AttributionDomain.STATION_TRAJECTORY,
        label="Station trajectory",
        delta_probability=0.04,
        evidence_ids=("asos-1",),
        available_at="2026-10-04T16:01:00Z",
        method="POINT_IN_TIME_RECOMPUTE_V1",
    )
    record = build_probability_change_record(
        ticker="KXHIGHDFW-TEST",
        previous_prediction_id="p-before",
        current_prediction_id="p-after",
        before_decision_time="2026-10-04T15:55:00Z",
        after_decision_time="2026-10-04T16:05:00Z",
        p_yes_before=0.70,
        p_yes_after=0.74,
        components=(component,),
        market_context_snapshot_ids=("market-1",),
    )
    first = store.persist_probability_change(record)
    second = store.persist_probability_change(record)
    assert first == second
    assert first["can_execute"] is False
    assert first["attribution_components"][0]["domain"] == "STATION_TRAJECTORY"
    assert len(client.tables["wow_kalshi_weather_probability_changes"].rows) == 1


def test_portfolio_risk_persistence_is_idempotent_and_non_executable():
    client = FakeClient()
    store = KalshiWeatherPersistence(client)
    event = build_weather_event(
        lane="DAILY_HIGH_TEMPERATURE",
        settlement_source="The Weather Company",
        settlement_location_code="CLIDFW",
        observation_window="2026-10-04 local day",
        metric="daily_max_temperature",
        units="F",
        region_id="DFW",
        factor_ids=(),
    )
    position = PortfolioPosition(
        position_id="p1",
        ticker="KXHIGHDFW-TEST",
        rule_snapshot_id="rule-1",
        prediction_id="prediction-1",
        market_snapshot_id="market-1",
        prediction_time="2026-10-04T16:00:00Z",
        market_time="2026-10-04T16:01:00Z",
        event=event,
        side=PositionSide.YES,
        quantity=10,
        entry_cost_per_contract=0.40,
        cost_basis_verified=True,
        threshold_lower=85,
        model_p_yes=0.60,
    )
    scenario = WeatherScenario(
        scenario_id="scenario-1",
        available_at="2026-10-04T16:02:00Z",
        weight=1,
        event_values={event.event_key: 90},
        evidence_ids=("weather-scenario-1",),
        method="GOVERNED_WEATHER_SCENARIO_TEST",
    )
    snapshot = PortfolioScenarioEngine().evaluate(
        as_of_time="2026-10-04T16:05:00Z",
        positions=(position,),
        scenarios=(scenario,),
        dependence_mode=DependenceMode.SAME_EVENT_EXACT,
        fractional_kelly_multiplier=0.25,
    )
    first = store.persist_portfolio_risk(snapshot)
    second = store.persist_portfolio_risk(snapshot)
    assert first == second
    assert first["can_execute"] is False
    assert first["market_price_used_as_weather_input"] is False
    assert first["risk_state_used_as_weather_input"] is False
    assert len(client.tables["wow_kalshi_weather_portfolio_risk_snapshots"].rows) == 1


def test_validation_meta_persistence_is_idempotent_and_non_executable():
    client = FakeClient()
    store = KalshiWeatherPersistence(client)
    baseline = BaselinePrediction(
        baseline_id="nbm",
        kind=BaselineKind.NBM,
        version="v1",
        p_yes=0.60,
        available_at="2026-10-04T15:00:00Z",
        evidence_ids=("nbm-evidence",),
    )
    samples = (
        ProbabilityValidationSample(
            sample_id="a",
            prediction_id="prediction-a",
            decision_time="2026-10-04T16:00:00Z",
            settled_at="2026-10-04T18:00:00Z",
            champion_p_yes=0.80,
            yes_outcome=True,
            confidence_score=0.90,
            champion_evidence_ids=("champion-a",),
            baselines=(baseline,),
            lane="HOURLY_TEMPERATURE",
        ),
        ProbabilityValidationSample(
            sample_id="b",
            prediction_id="prediction-b",
            decision_time="2026-10-04T16:00:00Z",
            settled_at="2026-10-04T18:00:00Z",
            champion_p_yes=0.20,
            yes_outcome=False,
            confidence_score=0.90,
            champion_evidence_ids=("champion-b",),
            baselines=(baseline,),
            lane="HOURLY_TEMPERATURE",
        ),
    )
    fixture = SettlementRedTeamFixture(
        fixture_id="source-conflict",
        version="v1",
        category=SettlementRedTeamCategory.SETTLEMENT_SOURCE_CONFLICT,
        expected_terminal_code="NO_PLAY_SETTLEMENT_AMBIGUITY",
        evidence_ids=("fixture-evidence",),
        description="Conflicting settlement sources fail closed.",
    )
    report = ValidationMetaLayer().evaluate(
        as_of_time="2026-10-04T19:00:00Z",
        probability_samples=samples,
        selective_confidence_thresholds=(0.0, 0.8),
        counterfactual_samples=(),
        red_team_fixtures=(fixture,),
        red_team_observations=(
            SettlementRedTeamObservation(
                fixture_identity=fixture.identity,
                observed_terminal_code="NO_PLAY_SETTLEMENT_AMBIGUITY",
                observed_at="2026-10-04T18:30:00Z",
                evidence_ids=("observed-evidence",),
            ),
        ),
        complexity_policy=ComplexityGatePolicy(
            policy_id="VALIDATION_COMPLEXITY_GATE",
            version="research-v1",
            evidence_id="holdout-evidence",
            minimum_holdout_n=2,
            minimum_brier_advantage=0.0,
            minimum_log_loss_advantage=0.0,
            minimum_red_team_pass_rate=1.0,
            promotion_confidence_threshold=0.0,
            minimum_selected_coverage=0.5,
            maximum_selected_brier=0.25,
            required_meteorological_baseline_kinds=(BaselineKind.NBM,),
        ),
    )
    first = store.persist_validation_meta(report)
    second = store.persist_validation_meta(report)
    assert first == second
    assert first["can_execute"] is False
    assert first["market_price_used_as_weather_input"] is False
    assert len(client.tables["wow_kalshi_weather_validation_meta_reports"].rows) == 1
