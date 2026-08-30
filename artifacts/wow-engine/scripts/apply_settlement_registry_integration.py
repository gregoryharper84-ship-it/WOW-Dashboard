from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one anchor, found {count}: {old[:100]!r}")
    path.write_text(text.replace(old, new, 1))


# MarketQuote carries provider identity only as routing/provenance metadata.
market = ROOT / "market.py"
replace_once(
    market,
    "    event_id: str\n\n\ndef american_to_implied_prob",
    "    event_id: str\n    provider: Optional[str] = None\n\n\ndef american_to_implied_prob",
)

api = ROOT / "api_prod_market.py"
replace_once(
    api,
    "from prop_settlement import (\n    NO_VIG_UNAVAILABLE,\n    SETTLEMENT_RULE_UNRESOLVED,\n    SettlementRule,\n    settle_prop_probability,\n)\n",
    "from prop_settlement import (\n    LINEUP_PAYOUT_CONTEXT_REQUIRED,\n    NO_VIG_UNAVAILABLE,\n    SETTLEMENT_RULE_UNRESOLVED,\n    SettlementRule,\n    settle_prop_probability,\n)\nfrom prop_settlement_registry import (\n    SETTLEMENT_RULE_CONFLICT,\n    SettlementRuleResolution,\n    normalize_provider,\n    resolve_prop_settlement_rule,\n)\n",
)
replace_once(
    api,
    "    period: str\n    event_id: str\n\n\nclass SettlementRuleInput",
    "    period: str\n    event_id: str\n    provider: Optional[str] = None\n\n\nclass SettlementRuleInput",
)
replace_once(
    api,
    "    source: str\n    void_probability_mass: float = 0.0\n\n\nclass ScorePropRequest",
    "    source: str\n    void_probability_mass: float = 0.0\n    money_semantics: str = \"FIXED_ODDS_RETURN_STAKE\"\n\n\nclass ScorePropRequest",
)
replace_once(
    api,
    "    market_side_b: Optional[MarketQuoteInput] = None\n    settlement_rule: Optional[SettlementRuleInput] = None\n",
    "    market_side_b: Optional[MarketQuoteInput] = None\n    settlement_provider: Optional[str] = None\n    # Backward-compatible caller observation only. This can cross-check but\n    # never becomes the operative server rule.\n    settlement_rule: Optional[SettlementRuleInput] = None\n",
)
replace_once(
    api,
    "def _settlement_lane(row: Any, req: ScorePropRequest, rule: Optional[SettlementRule], audited_a: Optional[MarketQuote], audited_b: Optional[MarketQuote]) -> dict[str, Any]:\n",
    "def _settlement_lane(row: Any, req: ScorePropRequest, rule: Optional[SettlementRule], resolution: SettlementRuleResolution, audited_a: Optional[MarketQuote], audited_b: Optional[MarketQuote]) -> dict[str, Any]:\n",
)
replace_once(
    api,
    "        \"status\": result.status,\n        \"blocker\": result.blocker,\n",
    "        \"status\": result.status,\n        \"blocker\": resolution.blocker if resolution.status != \"PASS\" else result.blocker,\n        \"rule_resolution_status\": resolution.status,\n        \"rule_authority\": resolution.authority,\n        \"provider\": resolution.provider,\n        \"rule_id\": resolution.rule_id,\n        \"source_ref\": resolution.source_ref,\n        \"source_hash\": resolution.source_hash,\n        \"observed_rule_status\": resolution.observed_rule_status,\n",
)
replace_once(
    api,
    "        \"source\": result.source,\n        \"blocks_model_probability\": False,\n",
    "        \"source\": result.source,\n        \"void_treatment\": result.void_treatment,\n        \"money_semantics\": result.money_semantics,\n        \"money_context_required\": result.money_context_required,\n        \"blocks_model_probability\": False,\n",
)
replace_once(
    api,
    "    if settlement_lane.get(\"status\") != \"PASS\":\n        lane = dict(lane)\n        lane[\"status\"] = \"HOLD\"\n        lane[\"settlement_blocker\"] = settlement_lane.get(\"blocker\") or SETTLEMENT_RULE_UNRESOLVED\n    return lane\n",
    "    if settlement_lane.get(\"status\") != \"PASS\":\n        lane = dict(lane)\n        lane[\"status\"] = \"HOLD\"\n        lane[\"settlement_blocker\"] = settlement_lane.get(\"blocker\") or SETTLEMENT_RULE_UNRESOLVED\n    elif settlement_lane.get(\"money_context_required\") is True:\n        lane = dict(lane)\n        lane[\"status\"] = \"HOLD\"\n        lane[\"settlement_blocker\"] = LINEUP_PAYOUT_CONTEXT_REQUIRED\n    return lane\n",
)
old_score = '''    raw_market_a = _to_market_quote(req.market_side_a)\n    raw_market_b = _to_market_quote(req.market_side_b)\n    settlement_rule = _to_settlement_rule(req.settlement_rule)\n    market_audit = audit_candidate_market(\n'''
new_score = '''    raw_market_a = _to_market_quote(req.market_side_a)\n    raw_market_b = _to_market_quote(req.market_side_b)\n    observed_rule = _to_settlement_rule(req.settlement_rule)\n\n    provider_candidates = [\n        normalize_provider(req.settlement_provider),\n        normalize_provider(getattr(raw_market_a, \"provider\", None)),\n        normalize_provider(getattr(raw_market_b, \"provider\", None)),\n    ]\n    provider_set = {value for value in provider_candidates if value}\n    if len(provider_set) > 1:\n        settlement_resolution = SettlementRuleResolution(\n            status=\"HOLD\", blocker=SETTLEMENT_RULE_CONFLICT, rule=None,\n            authority=None, provider=None, rule_id=None, rule_version=None,\n            source_ref=None, source_hash=None, money_semantics=None,\n            observed_rule_status=\"NOT_EVALUATED_PROVIDER_CONFLICT\", can_execute=False,\n        )\n    else:\n        settlement_resolution = resolve_prop_settlement_rule(\n            client=prod.get_client(),\n            provider=next(iter(provider_set), None),\n            sport=req.sport,\n            stat_type=req.stat_type,\n            period=_prop_period(req.stat_type),\n            direction=req.direction,\n            event_start_time=req.event_start_time,\n            observed_rule=observed_rule,\n        )\n    settlement_rule = settlement_resolution.rule\n    market_audit = audit_candidate_market(\n'''
replace_once(api, old_score, new_score)
replace_once(
    api,
    "    settlement_lane = _settlement_lane(result.row, req, settlement_rule, market_audit.side_a, market_audit.side_b)\n",
    "    settlement_lane = _settlement_lane(result.row, req, settlement_rule, settlement_resolution, market_audit.side_a, market_audit.side_b)\n",
)

pick = ROOT / "pick_request_runtime.py"
replace_once(
    pick,
    "                \"money_lane_status\": row.money_lane_status,\n            }\n",
    "                \"money_lane_status\": row.money_lane_status,\n            }\n            if row.platform is not None:\n                # Platform identifies which reviewed server rule to hydrate;\n                # it never supplies settlement semantics itself.\n                request_payload[\"settlement_provider\"] = row.platform\n",
)

print("settlement registry integration applied")
