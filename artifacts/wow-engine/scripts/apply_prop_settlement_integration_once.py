from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api_prod_market.py"
ACCEPTANCE = ROOT / "api_prod_market_acceptance.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one source match, got {count}")
    return text.replace(old, new, 1)


api = API.read_text()

api = replace_once(
    api,
    "from prop_fitted_provider import PropFittedProviderUnavailable\nfrom qualification_policy_v2 import classify_prop_probability\n",
    "from prop_fitted_provider import PropFittedProviderUnavailable\nfrom prop_market_audit import audit_candidate_market\nfrom prop_settlement import (\n    NO_VIG_UNAVAILABLE,\n    SETTLEMENT_RULE_UNRESOLVED,\n    SettlementRule,\n    settle_prop_probability,\n)\nfrom qualification_policy_v2 import classify_prop_probability\n",
    "api imports",
)

api = replace_once(
    api,
    "class ScorePropRequest(prod.ScorePropRequest):\n    market_side_a: Optional[MarketQuoteInput] = None\n    market_side_b: Optional[MarketQuoteInput] = None\n",
    "class SettlementRuleInput(BaseModel):\n    model_config = ConfigDict(extra=\"forbid\")\n\n    settlement_basis: str\n    boundary_operator: str\n    equality_treatment: str\n    void_treatment: str = \"RETURN_STAKE\"\n    rule_version: str\n    source: str\n    void_probability_mass: float = 0.0\n\n\nclass ScorePropRequest(prod.ScorePropRequest):\n    market_side_a: Optional[MarketQuoteInput] = None\n    market_side_b: Optional[MarketQuoteInput] = None\n    settlement_rule: Optional[SettlementRuleInput] = None\n    line_tolerance: float = 0.0\n",
    "request models",
)

helper_anchor = "def _to_market_quote(value: Optional[MarketQuoteInput]) -> Optional[MarketQuote]:\n    if value is None:\n        return None\n    return MarketQuote(**value.model_dump())\n\n\n"
helper_new = helper_anchor + '''def _to_settlement_rule(value: Optional[SettlementRuleInput]) -> Optional[SettlementRule]:
    if value is None:
        return None
    return SettlementRule(**value.model_dump())


def _candidate_american_odds(direction: str, side_a: Optional[MarketQuote], side_b: Optional[MarketQuote]) -> Optional[float]:
    target = str(direction or "").upper()
    aliases = {"MORE": {"MORE", "OVER"}, "LESS": {"LESS", "UNDER"}}
    for quote in (side_a, side_b):
        if quote is not None and str(quote.side or "").upper() in aliases.get(target, set()):
            return float(quote.american_odds)
    return None


def _settlement_lane(row: Any, req: ScorePropRequest, rule: Optional[SettlementRule], audited_a: Optional[MarketQuote], audited_b: Optional[MarketQuote]) -> dict[str, Any]:
    result = settle_prop_probability(
        direction=req.direction,
        probability_more=getattr(row, "probability_more", None),
        probability_less=getattr(row, "probability_less", None),
        equality_probability=getattr(row, "push_probability", None),
        rule=rule,
        american_odds=_candidate_american_odds(req.direction, audited_a, audited_b),
    )
    return {
        "status": result.status,
        "blocker": result.blocker,
        "p_win": result.p_win,
        "p_loss": result.p_loss,
        "p_push": result.p_push,
        "p_void": result.p_void,
        "graded_probability": result.graded_probability,
        "conditional_win_probability": result.conditional_win_probability,
        "american_odds": result.american_odds,
        "profit_multiple": result.profit_multiple,
        "break_even_unconditional": result.break_even_unconditional,
        "break_even_conditional_graded": result.break_even_conditional_graded,
        "expected_profit_per_unit_staked": result.expected_profit_per_unit_staked,
        "rule_version": result.rule_version,
        "source": result.source,
        "blocks_model_probability": False,
        "can_execute": False,
    }


def _effective_money_lane(row: Any, settlement_lane: dict[str, Any]) -> dict[str, Any]:
    lane = _money_lane(row)
    if settlement_lane.get("status") != "PASS":
        lane = dict(lane)
        lane["status"] = "HOLD"
        lane["settlement_blocker"] = settlement_lane.get("blocker") or SETTLEMENT_RULE_UNRESOLVED
    return lane


def _market_lane_with_audit(row: Any, market_audit: Any) -> dict[str, Any]:
    lane = _market_lane(row)
    lane["candidate_audit_status"] = market_audit.status
    lane["candidate_audit_blocker"] = market_audit.blocker
    if market_audit.status != "PASS":
        lane["status"] = "HOLD"
    if lane["status"] != "PASS" and not lane.get("candidate_audit_blocker"):
        lane["candidate_audit_blocker"] = NO_VIG_UNAVAILABLE
    return lane


'''
api = replace_once(api, helper_anchor, helper_new, "settlement helpers")

api = replace_once(
    api,
    "def _probability_qualification(row: Any, market_lane: dict[str, Any], money_lane: dict[str, Any]) -> dict[str, Any]:",
    "def _probability_qualification(row: Any, market_lane: dict[str, Any], money_lane: dict[str, Any], settlement_lane: dict[str, Any]) -> dict[str, Any]:",
    "qualification signature",
)
api = replace_once(
    api,
    "    if money_lane.get(\"status\") != \"PASS\":\n        blockers.append(\"PAYOUT_UNRESOLVED\")\n    terminal = reduce_prop_terminal(\n",
    "    if money_lane.get(\"status\") != \"PASS\":\n        blockers.append(\"PAYOUT_UNRESOLVED\")\n    if settlement_lane.get(\"status\") != \"PASS\":\n        blockers.append(\"SETTLEMENT_RULE_UNRESOLVED\")\n    terminal = reduce_prop_terminal(\n",
    "qualification settlement blocker",
)

pre_model_anchor = "    scored_at = datetime.now(timezone.utc).isoformat()\n    inference_request = _server_owned_inference_request(req, evidence, scored_at)\n    effective_snapshot_id = str(evidence.get(\"source_snapshot_id\") or req.source_snapshot_id)\n    try:\n"
pre_model_new = "    scored_at = datetime.now(timezone.utc).isoformat()\n    inference_request = _server_owned_inference_request(req, evidence, scored_at)\n    effective_snapshot_id = str(evidence.get(\"source_snapshot_id\") or req.source_snapshot_id)\n    raw_market_a = _to_market_quote(req.market_side_a)\n    raw_market_b = _to_market_quote(req.market_side_b)\n    settlement_rule = _to_settlement_rule(req.settlement_rule)\n    market_audit = audit_candidate_market(\n        event_id=req.event_id,\n        participant=str(evidence.get(\"player\") or req.player),\n        stat=req.stat_type,\n        period=_prop_period(req.stat_type),\n        line=req.line,\n        settlement_rule=settlement_rule,\n        side_a=raw_market_a,\n        side_b=raw_market_b,\n        line_tolerance=req.line_tolerance,\n    )\n    try:\n"
api = replace_once(api, pre_model_anchor, pre_model_new, "pre-model market audit")

api = replace_once(
    api,
    "            market_side_a=_to_market_quote(req.market_side_a),\n            market_side_b=_to_market_quote(req.market_side_b),\n",
    "            market_side_a=market_audit.side_a,\n            market_side_b=market_audit.side_b,\n",
    "audited market inputs",
)

api = replace_once(
    api,
    "    market_lane = _market_lane(result.row)\n    money_lane = _money_lane(result.row)\n    probability_qualification = _probability_qualification(result.row, market_lane, money_lane)\n",
    "    market_lane = _market_lane_with_audit(result.row, market_audit)\n    settlement_lane = _settlement_lane(result.row, req, settlement_rule, market_audit.side_a, market_audit.side_b)\n    money_lane = _effective_money_lane(result.row, settlement_lane)\n    probability_qualification = _probability_qualification(result.row, market_lane, money_lane, settlement_lane)\n",
    "post-model lanes",
)

api = replace_once(
    api,
    "            \"MARKET\": market_lane,\n            \"MONEY\": money_lane,\n",
    "            \"MARKET\": market_lane,\n            \"SETTLEMENT\": settlement_lane,\n            \"MONEY\": money_lane,\n",
    "settlement objective lane",
)

API.write_text(api)

acceptance = ACCEPTANCE.read_text()
acceptance = replace_once(
    acceptance,
    "from ledger import record_outcome\n",
    "from ledger import record_outcome\nfrom prop_settlement import settlement_self_acceptance\n",
    "acceptance import",
)
acceptance = replace_once(
    acceptance,
    "    _logger.warning(\n        \"WOW_PROP_SELF_ACCEPTANCE result=PASS directions=MORE,LESS auth=PASS \"\n        \"acquisition_fail_closed=PASS specialist_invoked=false zero_probability_leak=true \"\n        \"settlement_math=NOT_PROVEN model_path=NOT_PROVEN can_execute=false\"\n    )\n",
    "    settlement_math = \"PROVEN\" if settlement_self_acceptance() else \"FAILED\"\n    if settlement_math != \"PROVEN\":\n        _logger.error(\"WOW_PROP_SELF_ACCEPTANCE result=FAIL settlement_math=FAILED can_execute=false\")\n        return\n    _logger.warning(\n        \"WOW_PROP_SELF_ACCEPTANCE result=PASS directions=MORE,LESS auth=PASS \"\n        \"acquisition_fail_closed=PASS specialist_invoked=false zero_probability_leak=true \"\n        \"settlement_math=PROVEN model_path=NOT_PROVEN_IN_THIS_PROBE can_execute=false\"\n    )\n",
    "acceptance settlement proof",
)
ACCEPTANCE.write_text(acceptance)
print("prop settlement integration applied")
