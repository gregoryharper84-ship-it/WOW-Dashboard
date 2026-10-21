import copy, json, sys
SHA = "1" * 40
NOW = "2026-10-21T15:00:00Z"
START = "2026-10-25T17:00:00Z"
PRED = "2026-10-21T14:30:00Z"

def base(case_id, route, req, resp_extra=None, phase="PREGAME", stat=None):
    ev = f"EVT-{case_id}"
    r = dict(req)
    resp = {"model_status": "OK", "event_id": ev, "line": r.get("line"), "side": r.get("side"),
            "direction": r.get("direction"), "probability": 0.64, "lower_bound": 0.58,
            "publication_threshold": 0.55, "calibration_id": f"CAL-{route}-v3",
            "model_artifact": f"{route.lower()}_fitted.joblib", "model_version": "v17.3.0",
            "specialist_kind": {"NFL_MONEYLINE": "TEAM_EVENT_WIN", "NFL_SPREAD": "SCORE_MARGIN_COVER",
                                "NCAAF_SPREAD": "SCORE_MARGIN_COVER"}.get(route, "PLAYER_PROP"),
            "probability_publishable": True, "rank_eligible": True, "artifact_publishable": True,
            "prediction_phase": "PREGAME", "prediction_created_at": PRED}
    if route == "NFL_PROP":
        resp["espn_alias_verified"] = True
    if route.endswith("SPREAD"):
        resp.update(probability_source="SCORE_MARGIN_COVER", settlement_rule="FULL_GAME_INCL_OT",
                    push_rule="PUSH_VOID_REFUND")
    resp.update(resp_extra or {})
    return {
        "case_id": case_id, "route": route, "phase": phase,
        "action_route": "scoreWowTeamEventRequest" if not route.endswith("PROP") else "scoreWowPickRequest",
        "http_status": 200, "latency_ms": 840,
        "event": {"event_id": ev, "start_time": START, "status": "SCHEDULED"},
        "request": r, "response": resp,
        "receipt": {"receipt_id": f"RCPT-{case_id}", "immutable": True, "case_id": case_id, "event_id": ev,
                    "run_sha": SHA, "created_at": PRED, "probability": resp.get("probability"),
                    "line": r.get("line")},
        "rows": {"rows_in": 1, "terminal_states": [{"row_id": f"ROW-{case_id}", "state": "COMPLETED"}]},
        "money": {"price_snapshot_id": f"PX-{case_id}", "price": -120, "wager_placed": False},
    }

def expect(c, ps="PROBABILITY_ACCEPTED", stage=None, code=None, money="MONEY_AUDIT_PASSED"):
    c["expect"] = {"probability_status": ps, "first_failing_stage": stage, "exact_blocker": code}
    money = c.pop("_money", money)
    if money: c["expect"]["money_status"] = money
    return c

def unavailable(c, status, http=200):
    c["http_status"] = http
    c["response"].update(model_status=status, probability=None, lower_bound=None,
                         probability_publishable=False, rank_eligible=False, artifact_publishable=False,
                         model_artifact=None)
    c["receipt"] = {}; c["money"] = None
    c["rows"]["terminal_states"][0]["state"] = "HELD"
    c["_money"] = "MONEY_AUDIT_NOT_RUN"
    return c

ML = {"side": "HOME"}
PROP = lambda stat, line, d="MORE": {"stat": stat, "line": line, "direction": d, "player": "FIXTURE PLAYER"}
SP = lambda line, text: {"side": "HOME", "line": line, "line_text": text}
cases = []
cases.append(expect(base("nfl-ml-accept", "NFL_MONEYLINE", ML)))
c = base("nfl-ml-1896-contradiction", "NFL_MONEYLINE", ML, {"artifact_publishable": False})
cases.append(expect(c, "REJECTED", "PUBLICATION", "PUBLISHABILITY_CONTRADICTION"))
cases.append(expect(base("nfl-prop-passing-accept", "NFL_PROP", PROP("PASSING_YARDS", 245.5))))
cases.append(expect(base("nfl-prop-rushing-accept", "NFL_PROP", PROP("RUSHING_YARDS", 62.5, "LESS"))))
c = base("nfl-prop-receiving-money-absent", "NFL_PROP", PROP("RECEIVING_YARDS", 54.5)); c["money"] = None
cases.append(expect(c, "PROBABILITY_ACCEPTED", "MONEY_AUDIT", "MONEY_EVIDENCE_ABSENT", "MONEY_AUDIT_FAILED"))
c = base("nfl-prop-1894-alias-unverified", "NFL_PROP", PROP("RECEIVING_YARDS", 70.5), {"espn_alias_verified": False})
cases.append(expect(c, "REJECTED", "IDENTITY", "ESPN_ALIAS_UNVERIFIED"))
c = base("nfl-prop-threshold-weakened", "NFL_PROP", PROP("PASSING_YARDS", 230.5),
         {"publication_threshold": 0.45, "lower_bound": 0.50, "probability": 0.57})
c["receipt"]["probability"] = 0.57
cases.append(expect(c, "REJECTED", "CALIBRATION", "PUBLICATION_THRESHOLD_WEAKENED"))
c = base("nfl-prop-malformed-output", "NFL_PROP", PROP("RUSHING_YARDS", 48.5), {"probability": "0.61x"})
c["receipt"] = {}; c["money"] = None
cases.append(expect(c, "REJECTED", "MODEL", "MODEL_OUTPUT_INVALID", "MONEY_AUDIT_NOT_RUN"))
cases.append(expect(unavailable(base("nba-points-200-model-unavailable", "NBA_PROP", PROP("POINTS", 24.5)), "MODEL_UNAVAILABLE"),
                    "HELD_TYPED", "MODEL", "MODEL_UNAVAILABLE"))
cases.append(expect(unavailable(base("nba-pra-200-model-unavailable", "NBA_PROP", PROP("POINTS_REBOUNDS", 33.5)), "MODEL_UNAVAILABLE"),
                    "HELD_TYPED", "MODEL", "MODEL_UNAVAILABLE"))
c = unavailable(base("nba-points-scorer-failed", "NBA_PROP", PROP("POINTS", 19.5)), None, http=503)
c["response"].pop("model_status")
cases.append(expect(c, "HELD_TYPED", "MODEL", "MODEL_SCORER_FAILED"))
cases.append(expect(unavailable(base("nba-points-inputs-insufficient", "NBA_PROP", PROP("POINTS", 21.5)), "MODEL_INPUTS_INSUFFICIENT"),
                    "HELD_TYPED", "MODEL", "MODEL_INPUTS_INSUFFICIENT"))
cases.append(expect(base("nfl-spread-accept-integer-push", "NFL_SPREAD", SP(-3.0, "-3"))))
cases.append(expect(base("ncaaf-spread-accept", "NCAAF_SPREAD", SP(-6.5, "-6.5"))))
c = base("ncaaf-1h-spread-unsupported-hold", "NCAAF_SPREAD", SP(-3.5, "-3.5"))
c["response"].update(route_status="UNSUPPORTED", model_status=None, probability=None, lower_bound=None,
                     probability_publishable=False, rank_eligible=False, artifact_publishable=False)
c["receipt"] = {}; c["money"] = None; c["rows"]["terminal_states"][0]["state"] = "HELD"; c["_money"] = "MONEY_AUDIT_NOT_RUN"
cases.append(expect(c, "HELD_TYPED", "ROUTE", "UNSUPPORTED_ROUTE_HOLD"))
c = base("nfl-spread-borrowed-ml", "NFL_SPREAD", SP(-2.5, "-2.5"), {"probability_source": "MONEYLINE"})
cases.append(expect(c, "REJECTED", "MODEL", "PROBABILITY_BORROWED"))
c = base("nfl-spread-unsigned-line", "NFL_SPREAD", SP(3.5, "3.5"))
cases.append(expect(c, "REJECTED", "IDENTITY", "SPREAD_LINE_UNSIGNED"))
c = base("nfl-spread-push-rule-missing", "NFL_SPREAD", SP(-7.0, "-7"), {"push_rule": None})
cases.append(expect(c, "REJECTED", "CALIBRATION", "PUSH_RULE_MISSING"))
c = base("nfl-ml-stale-receipt", "NFL_MONEYLINE", ML); c["receipt"]["created_at"] = "2026-10-20T01:00:00Z"
c["response"]["prediction_created_at"] = "2026-10-20T01:00:00Z"
cases.append(expect(c, "REJECTED", "RECEIPT", "RECEIPT_STALE"))
c = base("nfl-ml-foreign-receipt", "NFL_MONEYLINE", ML); c["receipt"]["case_id"] = "some-other-case"
cases.append(expect(c, "REJECTED", "RECEIPT", "RECEIPT_FOREIGN"))
c = base("nfl-ml-run-sha-mismatch", "NFL_MONEYLINE", ML); c["receipt"]["run_sha"] = "2" * 40
cases.append(expect(c, "REJECTED", "ENVIRONMENT", "RUN_SHA_MISMATCH"))
c = base("nfl-prop-identity-conflict", "NFL_PROP", PROP("PASSING_YARDS", 260.5)); c["response"]["event_id"] = "EVT-OTHER"
cases.append(expect(c, "REJECTED", "IDENTITY", "IDENTITY_CONFLICT"))
c = base("nfl-ml-live-unrecognized-event", "NFL_MONEYLINE", ML, phase="LIVE")
c["event"].update(status="IN_PROGRESS", live_event_recognized=False)
cases.append(expect(c, "REJECTED", "LIFECYCLE", "LIVE_EVENT_UNRECOGNIZED", None))
c = base("nfl-ml-live-pregame-reuse", "NFL_MONEYLINE", ML, phase="LIVE")
c["event"].update(status="IN_PROGRESS", live_event_recognized=True)
cases.append(expect(c, "REJECTED", "LIFECYCLE", "PREGAME_REUSED_IN_LIVE", None))
c = base("nfl-prop-pregame-event-started", "NFL_PROP", PROP("RUSHING_YARDS", 70.5))
c["event"].update(status="IN_PROGRESS", start_time="2026-10-21T13:00:00Z")
cases.append(expect(c, "REJECTED", "LIFECYCLE", "EVENT_STARTED_INVALIDATED", None))
c = base("nfl-ml-row-terminated-twice", "NFL_MONEYLINE", ML)
c["rows"] = {"rows_in": 1, "terminal_states": [{"row_id": "R1", "state": "COMPLETED"}, {"row_id": "R1", "state": "HELD"}]}
cases.append(expect(c, "REJECTED", "RECONCILIATION", "RECONCILIATION_COUNT_MISMATCH"))

bundle = {"harness_version": "V17_MODEL_ACCEPTANCE_HARNESS_V1", "evaluated_at": NOW, "can_execute": False,
          "environment": {"mode": "FIXTURE", "environment": "ci-fixture", "dry_run": True, "can_execute": False,
                          "github_sha": SHA, "render_live_sha": None, "render_deploy_id": None,
                          "supabase_project_ref": None,
                          "production_binding_evidence": "NOT_PRODUCTION_EVIDENCE_FIXTURE_ONLY"},
          "cases": cases}
json.dump(bundle, open(sys.argv[1], "w"), indent=2, sort_keys=True); open(sys.argv[1], "a").write("\n")
