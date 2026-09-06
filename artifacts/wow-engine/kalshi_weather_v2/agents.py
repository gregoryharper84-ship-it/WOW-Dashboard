from __future__ import annotations

from dataclasses import asdict
from math import isclose

from .models import AgentResult, ContractSnapshot, MarketSnapshot, ProbabilityPackage, WeatherEvidenceSnapshot


class ContractSettlementAgent:
    """Owns contract identity and settlement fidelity only."""

    name = "KALSHI_CONTRACT_SETTLEMENT_AGENT"

    REQUIRED_TEXT_FIELDS = (
        "market_title",
        "contract_title",
        "ticker",
        "lane",
        "yes_condition",
        "no_condition",
        "location",
        "metric",
        "units",
        "observation_window",
        "timezone",
        "settlement_source",
        "rounding_convention",
        "trace_measurement_rules",
        "market_close_time",
        "rule_snapshot_id",
    )

    def evaluate(self, contract: ContractSnapshot, evidence: WeatherEvidenceSnapshot) -> AgentResult:
        blockers: list[str] = []
        for field_name in self.REQUIRED_TEXT_FIELDS:
            value = getattr(contract, field_name)
            if not isinstance(value, str) or not value.strip():
                blockers.append(f"CONTRACT_FIELD_MISSING:{field_name}")

        location_type = (contract.settlement_location_type or "").strip().upper()
        if location_type == "STATION":
            if not contract.settlement_station_id or not contract.settlement_station_name:
                blockers.append("SETTLEMENT_STATION_UNRESOLVED")
        elif location_type == "COORDINATE":
            if contract.settlement_latitude is None or contract.settlement_longitude is None:
                blockers.append("SETTLEMENT_COORDINATE_UNRESOLVED")
            else:
                if not (-90.0 <= float(contract.settlement_latitude) <= 90.0):
                    blockers.append("SETTLEMENT_LATITUDE_INVALID")
                if not (-180.0 <= float(contract.settlement_longitude) <= 180.0):
                    blockers.append("SETTLEMENT_LONGITUDE_INVALID")
        else:
            blockers.append("SETTLEMENT_LOCATION_TYPE_UNSUPPORTED")

        if not evidence.settlement_source_verified:
            blockers.append("SETTLEMENT_SOURCE_NOT_VERIFIED")
        if not evidence.station_identity_verified:
            blockers.append("SETTLEMENT_LOCATION_NOT_VERIFIED")

        if blockers:
            return AgentResult(
                agent=self.name,
                ok=False,
                code="NO_PLAY_SETTLEMENT_AMBIGUITY",
                blockers=tuple(blockers),
                payload={"contract": asdict(contract)},
            )

        return AgentResult(
            agent=self.name,
            ok=True,
            code="SETTLEMENT_IDENTITY_PASS",
            payload={
                "ticker": contract.ticker,
                "lane": contract.lane,
                "settlement_source": contract.settlement_source,
                "settlement_location_type": location_type,
                "settlement_station_id": contract.settlement_station_id,
                "settlement_latitude": contract.settlement_latitude,
                "settlement_longitude": contract.settlement_longitude,
                "rule_snapshot_id": contract.rule_snapshot_id,
            },
        )


class WeatherProbabilityAgent:
    """Owns weather evidence and the independent calibrated probability package."""

    name = "KALSHI_WEATHER_PROBABILITY_AGENT"

    def evaluate(self, evidence: WeatherEvidenceSnapshot, probability: ProbabilityPackage) -> AgentResult:
        blockers: list[str] = []
        warnings: list[str] = []

        if not evidence.evidence_complete:
            blockers.append("WEATHER_EVIDENCE_INCOMPLETE")
        if not evidence.temporal_provenance_verified:
            blockers.append("TEMPORAL_PROVENANCE_NOT_VERIFIED")
        if not evidence.source_snapshot_ids:
            blockers.append("SOURCE_SNAPSHOT_ID_MISSING")

        if probability.market_price_used_as_input:
            blockers.append("MARKET_PRICE_SUBSTITUTION_PROHIBITED")

        if probability.p_yes is None or probability.p_no is None:
            blockers.append("MODEL_PROBABILITY_MISSING")
        else:
            if not (0.0 <= probability.p_yes <= 1.0 and 0.0 <= probability.p_no <= 1.0):
                blockers.append("MODEL_PROBABILITY_OUT_OF_RANGE")
            if not isclose(probability.p_yes + probability.p_no, 1.0, abs_tol=1e-6):
                blockers.append("YES_NO_PROBABILITY_INCOHERENT")

        if probability.lower_bound_yes is None or probability.upper_bound_yes is None:
            blockers.append("UNCERTAINTY_INTERVAL_MISSING")
        elif probability.p_yes is not None:
            if not (0.0 <= probability.lower_bound_yes <= probability.p_yes <= probability.upper_bound_yes <= 1.0):
                blockers.append("UNCERTAINTY_INTERVAL_INVALID")

        if not probability.coherent:
            blockers.append("PROBABILITY_SET_NOT_COHERENT")
        if not probability.calibrated:
            blockers.append("CALIBRATION_NOT_READY")
        if not probability.calibration_method:
            blockers.append("CALIBRATION_METHOD_MISSING")
        if not probability.threshold_distance:
            blockers.append("THRESHOLD_DISTANCE_MISSING")

        if evidence.disagreement_magnitude is not None and evidence.disagreement_magnitude > 0:
            warnings.append("MODEL_DISAGREEMENT_PRESENT")

        if blockers:
            return AgentResult(
                agent=self.name,
                ok=False,
                code="NO_PLAY_DATA_INSUFFICIENT",
                blockers=tuple(blockers),
                warnings=tuple(warnings),
                payload={"probability_source": probability.probability_source},
            )

        return AgentResult(
            agent=self.name,
            ok=True,
            code="WEATHER_PROBABILITY_PASS",
            warnings=tuple(warnings),
            payload={
                "p_yes": probability.p_yes,
                "p_no": probability.p_no,
                "central_estimate": probability.central_estimate,
                "lower_bound_yes": probability.lower_bound_yes,
                "upper_bound_yes": probability.upper_bound_yes,
                "threshold_distance": probability.threshold_distance,
                "calibration_method": probability.calibration_method,
                "probability_source": probability.probability_source,
            },
        )


class MarketCalibrationAuditor:
    """Audits executable price and verified friction-adjusted economics only."""

    name = "KALSHI_MARKET_CALIBRATION_AUDITOR"

    def evaluate(self, probability: ProbabilityPackage, market: MarketSnapshot) -> AgentResult:
        blockers: list[str] = []
        warnings: list[str] = []

        if probability.p_yes is None or probability.p_no is None:
            blockers.append("MODEL_PROBABILITY_REQUIRED_BEFORE_MARKET_AUDIT")
        if not market.market_open:
            blockers.append("MARKET_NOT_OPEN")
        if not market.orderbook_nonempty:
            blockers.append("ORDERBOOK_EMPTY")
        if not market.executable_price_verified:
            blockers.append("EXECUTABLE_PRICE_NOT_VERIFIED")
        if market.price_time is None:
            blockers.append("MARKET_PRICE_TIMESTAMP_MISSING")

        for field_name, value in (("yes_price", market.yes_price), ("no_price", market.no_price)):
            if value is not None and not (0.0 <= value <= 1.0):
                blockers.append(f"MARKET_PRICE_OUT_OF_RANGE:{field_name}")
        if market.yes_price is None and market.no_price is None:
            blockers.append("NO_EXECUTABLE_PRICE")

        # A raw executable price is sufficient to discuss market state, but
        # official edge qualification requires the exact verified break-even
        # after applicable fees/frictions. Do not silently assume fees are 0.
        if not market.fee_known:
            blockers.append("FEE_SCHEDULE_UNRESOLVED")
        if not market.friction_model_verified:
            blockers.append("FRICTION_MODEL_UNVERIFIED")

        for field_name, value in (
            ("yes_effective_break_even", market.yes_effective_break_even),
            ("no_effective_break_even", market.no_effective_break_even),
        ):
            if value is not None and not (0.0 <= value <= 1.0):
                blockers.append(f"BREAK_EVEN_OUT_OF_RANGE:{field_name}")

        if market.yes_price is not None and market.yes_effective_break_even is None:
            blockers.append("YES_EFFECTIVE_BREAK_EVEN_MISSING")
        if market.no_price is not None and market.no_effective_break_even is None:
            blockers.append("NO_EFFECTIVE_BREAK_EVEN_MISSING")

        payload: dict[str, float | bool | None] = {
            "yes_price": market.yes_price,
            "no_price": market.no_price,
            "fee_known": market.fee_known,
            "friction_model_verified": market.friction_model_verified,
            "yes_effective_break_even": market.yes_effective_break_even,
            "no_effective_break_even": market.no_effective_break_even,
        }

        if probability.p_yes is not None and market.yes_price is not None:
            raw_edge_yes = probability.p_yes - market.yes_price
            payload["raw_edge_yes"] = raw_edge_yes
            payload["pre_fee_ev_yes_per_share"] = raw_edge_yes
            if market.yes_price > 0:
                payload["pre_fee_ev_yes_per_1_risked"] = raw_edge_yes / market.yes_price
        if probability.p_no is not None and market.no_price is not None:
            raw_edge_no = probability.p_no - market.no_price
            payload["raw_edge_no"] = raw_edge_no
            payload["pre_fee_ev_no_per_share"] = raw_edge_no
            if market.no_price > 0:
                payload["pre_fee_ev_no_per_1_risked"] = raw_edge_no / market.no_price

        if probability.p_yes is not None and market.yes_effective_break_even is not None:
            payload["friction_adjusted_raw_edge_yes"] = probability.p_yes - market.yes_effective_break_even
            if probability.lower_bound_yes is not None:
                payload["uncertainty_adjusted_edge_yes"] = probability.lower_bound_yes - market.yes_effective_break_even

        if probability.p_no is not None and market.no_effective_break_even is not None:
            payload["friction_adjusted_raw_edge_no"] = probability.p_no - market.no_effective_break_even
            if probability.upper_bound_yes is not None:
                conservative_no = 1.0 - probability.upper_bound_yes
                payload["uncertainty_adjusted_edge_no"] = conservative_no - market.no_effective_break_even

        if blockers:
            return AgentResult(
                agent=self.name,
                ok=False,
                code="MARKET_AUDIT_HELD",
                blockers=tuple(dict.fromkeys(blockers)),
                warnings=tuple(warnings),
                payload=payload,
            )

        return AgentResult(
            agent=self.name,
            ok=True,
            code="MARKET_AUDIT_PASS",
            warnings=tuple(warnings),
            payload=payload,
        )
