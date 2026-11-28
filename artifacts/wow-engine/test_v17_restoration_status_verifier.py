"""Regression coverage for the read-only V17 runtime restoration verifier.

Two invariants matter most:

* a supplied literal such as ``"c0ffee1"`` can never produce
  ``RUNTIME_RESTORED``, including by constructing ``ReadResult`` directly with a
  ``live:`` source;
* the SQL that now answers ``MODEL_CERTIFIED`` stays read-only and keeps the
  same forward-evidence floor as the governing module.
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
import unittest

from v17.restoration_status_verifier import (
    CAN_EXECUTE,
    PRIORITY_SPORTS,
    TERMINAL_AUTHORITY,
    ReadResult,
    assess_runtime,
    is_live_source,
    read_github_main_sha,
    read_render_service_commit,
    read_runtime_terminal_receipt,
    report_digest,
)

CAPTURED = "2026-11-07T06:00:00Z"
MAIN = "372bef19a04a1c053ad6ab3f649bdbcaf62c10b3"
REPO = "gregoryharper84-ship-it/WOW-Dashboard"
MODULE_DIR = pathlib.Path(__file__).resolve().parent / "v17"
SQL_PATH = MODULE_DIR / "model_certification_verdicts.sql"


def transport_for(**routes: tuple[int, object]):
    def transport(url: str) -> tuple[int, object]:
        if url not in routes:
            raise AssertionError(f"unexpected url: {url}")
        return routes[url]

    return transport


def github_route(sha: str = MAIN) -> tuple[str, tuple[int, object]]:
    return (f"https://api.github.com/repos/{REPO}/commits/main", (200, {"sha": sha}))


def render_route(service_id: str, sha: str = MAIN) -> tuple[str, tuple[int, object]]:
    return (
        f"https://api.render.com/v1/services/{service_id}/deploys?limit=1",
        (200, [{"deploy": {"id": "dep-1", "commit": {"id": sha}}, "cursor": "c1"}]),
    )


def receipt_route(
    origin: str = "https://engine.example", path: str = "/internal/v17/score"
) -> tuple[str, tuple[int, object]]:
    return (
        origin + path,
        (
            200,
            {
                "can_execute": False,
                "terminal_authority": TERMINAL_AUTHORITY,
                "disposition": "MODEL_SCORED",
                "scored_rows": 1,
                "prediction_receipt_id": "rcpt-abc123",
                "deployed_commit": MAIN,
            },
        ),
    )


def live_runtime_evidence(
    *, web_sha: str = MAIN, worker_sha: str = MAIN, receipt_patch: dict | None = None
):
    """Read all four runtime sources through their real readers."""
    origin, path = "https://engine.example", "/internal/v17/score"
    routes = dict(
        [
            github_route(),
            render_route("srv-web", web_sha),
            render_route("srv-worker", worker_sha),
            receipt_route(origin, path),
        ]
    )
    if receipt_patch:
        status, payload = routes[origin + path]
        merged = dict(payload)
        merged.update(receipt_patch)
        routes[origin + path] = (status, merged)
    transport = transport_for(**routes)
    return {
        "github_main": read_github_main_sha(transport, repo=REPO, captured_at=CAPTURED),
        "render_web": read_render_service_commit(transport, service_id="srv-web", captured_at=CAPTURED),
        "render_worker": read_render_service_commit(
            transport, service_id="srv-worker", captured_at=CAPTURED
        ),
        "runtime_receipt": read_runtime_terminal_receipt(
            transport, origin=origin, path=path, captured_at=CAPTURED
        ),
    }


def sql_body(path: pathlib.Path) -> str:
    """The SQL with line comments stripped, for read-only assertions."""
    return "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())


QUERY_MARKERS = {
    "NFL": "wow_nfl_forward_calibration_health",
    "NBA": "wow_basketball_team_event_certification",
    "NCAAF": "wow_ncaaf_fitted_model_artifacts",
}


def sql_statements() -> list[str]:
    return [part.strip() for part in sql_body(SQL_PATH).split(";") if part.strip()]


def query_for(sport: str) -> str:
    """Select a verdict query by the relation it reads, never by position."""
    marker = QUERY_MARKERS[sport]
    matches = [statement for statement in sql_statements() if marker in statement.lower()]
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one query touching {marker}, found {len(matches)}")
    return matches[0]


def verdict_expression(statement: str, alias: str = "model_certified") -> str:
    """The predicate or CASE a query assigns to ``alias``."""
    marker = re.search(rf"\bas\s+{alias}\b", statement, re.IGNORECASE)
    if marker is None:
        raise AssertionError(f"statement does not assign {alias}")
    body = statement[: marker.start()].rstrip()
    if body.lower().endswith("end"):
        depth = 0
        for token in reversed(list(re.finditer(r"\b(?:case|end)\b", body, re.IGNORECASE))):
            depth += 1 if token.group(0).lower() == "end" else -1
            if depth == 0:
                return body[token.start() :].strip()
        raise AssertionError("unbalanced CASE")
    if body.endswith(")"):
        depth = 0
        for i in range(len(body) - 1, -1, -1):
            depth += {")": 1, "(": -1}.get(body[i], 0)
            if depth == 0:
                return body[i + 1 :].strip()
        raise AssertionError("unbalanced predicate")
    raise AssertionError("model_certified must be assigned a predicate or CASE")


class TestProvenanceGuard(unittest.TestCase):
    def test_fixture_sha_can_never_restore_a_sport(self):
        """The original defect: caller-supplied 'c0ffee1' established success."""
        fake = ReadResult(True, "c0ffee1", "fixture:test", CAPTURED)
        self.assertFalse(is_live_source(fake.source))

        assessment = assess_runtime(
            "NBA",
            github_main=fake,
            render_web=fake,
            render_worker=fake,
            runtime_receipt=ReadResult(
                True,
                {
                    "can_execute": False,
                    "terminal_authority": TERMINAL_AUTHORITY,
                    "disposition": "MODEL_SCORED",
                    "scored_rows": 1,
                    "prediction_receipt_id": "rcpt-1",
                },
                "fixture:test",
                CAPTURED,
            ),
        )
        self.assertEqual(assessment["status"], "RUNTIME_UNVERIFIED")
        self.assertIn("GITHUB_MAIN_PROVENANCE_NOT_LIVE", assessment["blockers"])
        self.assertIn("RENDER_WEB_PROVENANCE_NOT_LIVE", assessment["blockers"])
        self.assertIn("RUNTIME_RECEIPT_PROVENANCE_NOT_LIVE", assessment["blockers"])

    def test_hand_built_live_read_result_is_rejected(self):
        """Constructing the dataclass directly must not mint live provenance."""
        with self.assertRaises(PermissionError):
            ReadResult(True, "c0ffee1", "live:forged", CAPTURED)

    def test_only_a_reader_holding_the_token_can_attest(self):
        with self.assertRaises(PermissionError):
            ReadResult._attested_live(
                "c0ffee1", source="live:x", captured_at=CAPTURED, attestation=object()
            )


class TestRuntimeAssessment(unittest.TestCase):
    def test_matching_live_shas_with_scoring_receipt_restore_runtime(self):
        assessment = assess_runtime("NBA", **live_runtime_evidence())
        self.assertEqual(assessment["status"], "RUNTIME_RESTORED")
        self.assertEqual(assessment["blockers"], [])
        self.assertEqual(assessment["scored_rows"], 1)
        self.assertEqual(assessment["prediction_receipt_id"], "rcpt-abc123")
        self.assertFalse(assessment["can_execute"])

    def test_worker_sha_drift_holds_runtime(self):
        assessment = assess_runtime("NBA", **live_runtime_evidence(worker_sha="deadbeef"))
        self.assertEqual(assessment["status"], "RUNTIME_HOLD")
        self.assertIn("RENDER_WORKER_SHA_MISMATCH", assessment["blockers"])

    def test_unreadable_render_service_is_unverified_not_restored(self):
        origin, path = "https://engine.example", "/internal/v17/score"
        transport = transport_for(
            **dict([github_route(), render_route("srv-web"), receipt_route(origin, path)])
        )
        assessment = assess_runtime(
            "NBA",
            github_main=read_github_main_sha(transport, repo=REPO, captured_at=CAPTURED),
            render_web=read_render_service_commit(transport, service_id="srv-web", captured_at=CAPTURED),
            render_worker=read_render_service_commit(
                transport, service_id="srv-worker-missing", captured_at=CAPTURED
            ),
            runtime_receipt=read_runtime_terminal_receipt(
                transport, origin=origin, path=path, captured_at=CAPTURED
            ),
        )
        self.assertEqual(assessment["status"], "RUNTIME_UNVERIFIED")
        self.assertTrue(any(b.startswith("RENDER_WORKER_UNREAD") for b in assessment["blockers"]))

    def test_missing_immutable_receipt_holds_runtime(self):
        assessment = assess_runtime(
            "NBA", **live_runtime_evidence(receipt_patch={"prediction_receipt_id": None})
        )
        self.assertEqual(assessment["status"], "RUNTIME_HOLD")
        self.assertIn("RUNTIME_IMMUTABLE_RECEIPT_MISSING", assessment["blockers"])

    def test_zero_scored_rows_holds_runtime(self):
        assessment = assess_runtime("NBA", **live_runtime_evidence(receipt_patch={"scored_rows": 0}))
        self.assertEqual(assessment["status"], "RUNTIME_HOLD")
        self.assertIn("RUNTIME_NO_SCORED_ROW", assessment["blockers"])

    def test_terminal_model_failure_is_not_a_scoring_result(self):
        assessment = assess_runtime(
            "NBA", **live_runtime_evidence(receipt_patch={"disposition": "MODEL_UNAVAILABLE"})
        )
        self.assertEqual(assessment["status"], "RUNTIME_HOLD")
        self.assertIn("RUNTIME_TERMINAL_MODEL_FAILURE:MODEL_UNAVAILABLE", assessment["blockers"])

    def test_receipt_not_terminating_through_the_reducer_holds_runtime(self):
        assessment = assess_runtime(
            "NBA", **live_runtime_evidence(receipt_patch={"terminal_authority": "SOMETHING_ELSE"})
        )
        self.assertEqual(assessment["status"], "RUNTIME_HOLD")
        self.assertIn("RUNTIME_TERMINAL_AUTHORITY_MISMATCH", assessment["blockers"])

    def test_runtime_claiming_execution_authority_is_rejected_loudly(self):
        origin, path = "https://engine.example", "/internal/v17/score"
        transport = transport_for(**dict([(origin + path, (200, {"can_execute": True}))]))
        with self.assertRaises(PermissionError):
            read_runtime_terminal_receipt(transport, origin=origin, path=path, captured_at=CAPTURED)


class TestReport(unittest.TestCase):
    def test_report_is_json_safe_and_carries_can_execute_false(self):
        report = assess_runtime("NFL", **live_runtime_evidence())
        self.assertFalse(report["can_execute"])
        self.assertEqual(json.loads(json.dumps(report)), report)

    def test_digest_is_stable_and_content_sensitive(self):
        restored = assess_runtime("NBA", **live_runtime_evidence())
        self.assertEqual(report_digest([restored]), report_digest([restored]))
        self.assertEqual(len(report_digest([restored])), 64)
        drifted = assess_runtime("NBA", **live_runtime_evidence(worker_sha="deadbeef"))
        self.assertNotEqual(report_digest([restored]), report_digest([drifted]))


class TestModuleInvariants(unittest.TestCase):
    def test_priority_sports_are_exactly_the_owner_four(self):
        self.assertEqual(PRIORITY_SPORTS, ("NFL", "NBA", "NHL", "NCAAF"))

    def test_module_never_grants_execution(self):
        self.assertFalse(CAN_EXECUTE)

    def test_sql_verdicts_are_read_only(self):
        body = sql_body(SQL_PATH).lower()
        for verb in ("insert", "update", "delete", "alter", "create", "drop", "truncate", "grant", "revoke"):
            self.assertNotRegex(body, rf"\b{verb}\b", f"{verb} found in verdict SQL")

    def test_sql_covers_each_governed_model_relation(self):
        body = sql_body(SQL_PATH)
        for relation in (
            "wow_nfl_forward_calibration_health",
            "wow_basketball_team_event_certification",
            "wow_ncaaf_fitted_model_artifacts",
        ):
            self.assertIn(relation, body)
        self.assertNotIn("wow_nhl", body.lower())

    def test_sql_forward_floor_matches_the_governing_module(self):
        """The 100 floor moved to SQL; it must not drift from the model module."""
        tree = ast.parse((MODULE_DIR / "nfl_forward_shadow.py").read_text(encoding="utf-8"))
        governed = None
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == "MIN_FORWARD_GRADED":
                if isinstance(node.value, ast.Constant):
                    governed = node.value.value
        self.assertIsNotNone(governed, "MIN_FORWARD_GRADED not found in nfl_forward_shadow")

        floors = re.findall(r"minimum_forward_required\s*>=\s*(\d+)", sql_body(SQL_PATH))
        self.assertTrue(floors, "no minimum_forward_required floor in verdict SQL")
        for floor in floors:
            self.assertEqual(int(floor), governed)


class TestModelVerdictSqlInvariants(unittest.TestCase):
    """Locks the invariants that keep an omitted check from passing as a verdict.

    Both defects found in this file were silent: a filter on a column pinned
    false (the query could never return a row) and a skipped calibrator gate
    (absence of evidence looked like a recorded failure).
    """

    @classmethod
    def setUpClass(cls):
        cls.nfl = query_for("NFL")

    def test_one_query_per_governed_sport(self):
        statements = sql_statements()
        self.assertEqual(len(statements), 3)
        for sport, marker in QUERY_MARKERS.items():
            with self.subTest(sport=sport):
                self.assertEqual(sum(marker in statement.lower() for statement in statements), 1)

    def test_all_three_queries_return_the_same_verdict_row_shape(self):
        """sport, verdict, reason_code, model_certified -- one row each, same order."""
        # The final SELECT, not a CTE: `as reason_code` appears inside `judged`
        # too, so searching the whole statement would prove nothing.
        order = ("as sport", "judged.verdict", "judged.reason_code", "as model_certified")
        for sport in QUERY_MARKERS:
            statement = query_for(sport)
            final = statement[statement.rindex("\nselect") :].lower()
            with self.subTest(sport=sport):
                self.assertIn("left join", statement.lower())  # cannot return zero rows
                positions = [final.find(token) for token in order]
                self.assertNotIn(-1, positions)
                self.assertEqual(positions, sorted(positions))

    def test_nfl_verdict_reads_the_capability_row(self):
        lowered = self.nfl.lower()
        self.assertIn("wow_runtime_capabilities", lowered)
        self.assertRegex(lowered, r"capability_key\s*=\s*'nfl_event_probability'")
        # Joined, not merely declared: cap.* with no FROM entry is an invalid
        # query that a text-only check would happily approve.
        self.assertRegex(lowered, r"\bjoin\s+capability\b")
        # The gate lives in the scored CTE; the verdict is derived from it.
        self.assertRegex(lowered, r"capability_status\s*=\s*'available'")
        self.assertIn("calibrator_ready", lowered)

    def test_nfl_verdict_never_filters_the_dead_publishable_column(self):
        """wow_nfl_event_fitted_registry_not_probability_publication pins it false."""
        self.assertNotIn("probability_publishable", self.nfl.lower())

    def test_nfl_verdict_avoids_unprovable_calibrator_columns(self):
        """No DDL in this repo defines them; reading them risks SQLSTATE 42703."""
        lowered = self.nfl.lower()
        self.assertNotIn("wow_calibrators", lowered)
        for column in ("market_family", "validation_status", "health_status"):
            self.assertNotIn(column, lowered)

    def test_nfl_reason_codes_are_governed_not_invented(self):
        """The NFL report must speak the contract's vocabulary, not a local one."""
        governed = {
            "NFL_EVENT_PROBABILITY_CAPABILITY_UNAVAILABLE",  # nfl_event_model_contract.p0_readiness
            "NFL_EVENT_CALIBRATOR_UNAVAILABLE",              # nfl_event_model_contract.p0_readiness
            "NFL_FITTED_MODEL_ARTIFACT_UNAVAILABLE",         # nfl_event_model_contract.p0_readiness
            "NFL_FORWARD_CERTIFICATION_UNPROVEN",            # v17/nfl_daily_stage_receipts.py
            "INSUFFICIENT_FORWARD_EVIDENCE",                 # ledger status (DB check constraint)
            "DO_NOT_CERTIFY_YET",                            # ledger certification_recommendation
            "CERTIFIED_ARTIFACT_PUBLISHABLE",                # report-local success label
        }
        emitted = set(
            re.findall(r"(?:then|else)\s+'([A-Z][A-Z0-9_]+)'", verdict_expression(self.nfl, "reason_code"))
        )
        self.assertTrue(emitted)
        self.assertEqual(emitted - governed, set())
        for retired in (
            "NO_CAPABILITY_RECORD",
            "NO_CALIBRATOR_EVIDENCE",
            "NO_CALIBRATION_HEALTH_RECORD",
            "CAPABILITY_NOT_AVAILABLE",
            "CALIBRATOR_NOT_READY",
            "CERTIFIED_ARTIFACT_UNAVAILABLE",
            "LEDGER_NOT_PASS",
            "LEDGER_BLOCKERS_PRESENT",
            "FORWARD_FLOOR_BELOW_GOVERNED_MINIMUM",
            "LEDGER_COHORT_BELOW_MINIMUM",
            "CHAMPION_COHORT_BELOW_MINIMUM",
        ):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, self.nfl)

    def test_forward_evidence_outcomes_stay_distinct(self):
        """The ledger's three-way outcome must not collapse back to one code.

        PASS / FAIL / INSUFFICIENT_FORWARD_EVIDENCE are separate governed
        outcomes; a single shared reason code would make "not enough evidence
        yet" and "evidence failed" read identically.
        """
        expr = verdict_expression(self.nfl, "reason_code")
        self.assertRegex(
            expr,
            r"status\s*=\s*'INSUFFICIENT_FORWARD_EVIDENCE'\s+then\s+'INSUFFICIENT_FORWARD_EVIDENCE'",
        )
        self.assertIn("'DO_NOT_CERTIFY_YET'", expr)
        self.assertLess(
            expr.index("status = 'INSUFFICIENT_FORWARD_EVIDENCE'"),
            expr.index("'DO_NOT_CERTIFY_YET'"),
        )

    def test_nba_and_ncaaf_reason_codes_are_governed(self):
        """Both must name the gap the governed source names, not a local synonym."""
        governed = {
            "NBA": {
                "MODEL_UNAVAILABLE",              # certification table default
                "CERTIFICATION_REPLAY_REQUIRED",  # persisted when CERTIFIED is not live
                "CERTIFIED_ARTIFACT_PUBLISHABLE",  # report-local success label
            },
            "NCAAF": {
                "NCAAF_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",  # wow_ncaaf_certified_model_artifact
                "NCAAF_BUNDLE_NOT_CERTIFIED",                # ncaaf_fitted_provider
                "NCAAF_ARTIFACT_NOT_PUBLISHABLE",            # ncaaf_fitted_provider
                "NCAAF_CERTIFIED_CALIBRATOR_NOT_FOUND",      # ncaaf_runtime_contract
                "CERTIFIED_ARTIFACT_PUBLISHABLE",            # report-local success label
            },
        }
        for sport, allowed in governed.items():
            expr = verdict_expression(query_for(sport), "reason_code")
            emitted = set(re.findall(r"(?:then|else)\s+'([A-Z][A-Z0-9_]+)'", expr))
            with self.subTest(sport=sport):
                self.assertTrue(emitted)
                self.assertEqual(emitted - allowed, set())

        # NBA delegates the non-CERTIFIED reason to the row the registry wrote.
        self.assertIn("registry_reason_code", verdict_expression(query_for("NBA"), "reason_code").lower())

        for retired in (
            "NO_CERTIFICATION_RECORD",
            "CAPABILITY_NOT_CERTIFIED",
            "NO_LIVE_ARTIFACT",
            "NO_ARTIFACT_RECORDED",
            "NO_ACTIVE_ARTIFACT",
            "ACTIVE_ARTIFACT_NOT_PUBLISHABLE",
        ):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, SQL_PATH.read_text())

    def test_reason_codes_cover_every_unverified_branch(self):
        """Each branch that can only be reached by absent evidence keeps its name."""
        expectations = {
            "NFL": (
                "NFL_EVENT_PROBABILITY_CAPABILITY_UNAVAILABLE",
                "NFL_EVENT_CALIBRATOR_UNAVAILABLE",
                "NFL_FORWARD_CERTIFICATION_UNPROVEN",
            ),
            "NBA": ("MODEL_UNAVAILABLE",),
            "NCAAF": ("NCAAF_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",),
        }
        for sport, codes in expectations.items():
            lowered = query_for(sport).lower()
            for code in codes:
                with self.subTest(sport=sport, code=code):
                    self.assertIn(f"'{code.lower()}'", lowered)

    def test_cte_joins_are_not_aliased_away(self):
        """Aliasing a CTE then referencing its bare name is a 42P01 at runtime.

        Scoped to the anchor join block: elsewhere the same CTE may legitimately
        be aliased for a different SELECT, so a statement-wide check would flag
        valid SQL such as `join champion ch on` inside `cohort`.
        """
        for sport in QUERY_MARKERS:
            statement = query_for(sport).lower()
            start = statement.find("from (select 1) anchor")
            if start == -1:
                continue
            # The whole CTE holding the anchor, select list included.
            block = statement[max(statement.rfind("),", 0, start), 0) : statement.find("\n),", start)]
            for cte, _alias in re.findall(r"\bjoin\s+([a-z_]\w*)\s+([a-z_]\w*)\s+on\b", block):
                with self.subTest(sport=sport, cte=cte):
                    self.assertNotIn(f"{cte}.", block)

    def test_nfl_verdict_requires_calibrator_evidence_presence(self):
        self.assertRegex(self.nfl.lower(), r"evidence\s*->>\s*'calibrator_ready'\s+is\s+not\s+null")

    def test_no_query_defaults_absence_to_false(self):
        """Absent evidence may produce a row only if that row says UNVERIFIED outright."""
        for sport in QUERY_MARKERS:
            statement = query_for(sport)
            predicate = verdict_expression(statement)
            with self.subTest(sport=sport):
                self.assertRegex(predicate, r"\w+\.\w+")  # a real column, not a literal
                self.assertNotIn("coalesce", predicate.lower())
                # A boolean literal is allowed only as an explicit outcome
                # (`then true` / `else false`); anywhere else it is a stub.
                lowered = predicate.lower()
                for literal in re.finditer(r"\b(?:true|false)\b", lowered):
                    before = lowered[: literal.start()].rstrip()
                    self.assertTrue(
                        before.endswith(("then", "else")),
                        f"bare boolean literal in the {sport} verdict: {lowered[max(0, literal.start() - 30):literal.end() + 10]!r}",
                    )
                if re.search(r"\bleft\s+(?:outer\s+)?join\b", statement.lower()):
                    self.assertIn("'unverified'", statement.lower())
                    self.assertRegex(predicate.lower(), r"then\s+null")

    def test_gates_are_not_weakened(self):
        """Dropping any governed term must fail here, not silently relax a gate."""
        nfl_gates = verdict_expression(self.nfl, "gates_pass").lower()
        for term in (
            "capability_status = 'available'",
            "capability.calibrator_ready",
            "champion.certified",
            "status = 'pass'",
            "certification_recommendation = 'forward_evidence_pass'",
            "blockers = '[]'::jsonb",
            "health.graded_n >= health.minimum_forward_required",
            "cohort.graded_n >= health.minimum_forward_required",
            "health.minimum_forward_required >= 100",
        ):
            self.assertIn(term, nfl_gates)

        nba = query_for("NBA").lower()
        self.assertRegex(nba, r"cert\.capability_status\s*=\s*'certified'\s+and\s+cert\.live_artifact")
        nba_live = verdict_expression(query_for("NBA"), "live_artifact").lower()
        for term in ("a.sport = c.sport", "a.model_artifact_version = c.model_artifact_version",
                     "a.promoted", "a.active"):
            self.assertIn(term, nba_live)

        ncaaf_gates = verdict_expression(query_for("NCAAF"), "certified").lower()
        for term in (
            "p.probability_publishable",
            "p.promoted",
            "p.active",
            "p.lifecycle_state in ('prospective_certified', 'champion')",
            "p.calibrator_version is not null",
            "p.calibration_training_n > 0",
        ):
            self.assertIn(term, ncaaf_gates)

    def test_ncaaf_picked_exposes_every_column_the_case_reads(self):
        """A reference to an unprojected CTE column is SQLSTATE 42703 at runtime.

        `certified` uses p.promoted internally; that does not make
        picked.promoted available to the outer SELECT.
        """
        stmt = query_for("NCAAF")
        picked = re.search(r"picked as \((.*?)\n\)", stmt, re.S).group(1)
        # Drop parenthesised expressions: the AND that builds `certified` reads
        # columns the outer query cannot see.
        plain = picked.split("from ranked p")[0]
        while True:  # strip innermost first: the certified AND nests a list
            stripped = re.sub(r"\([^()]*\)", "", plain)
            if stripped == plain:
                break
            plain = stripped
        for column in sorted(set(re.findall(r"picked\.(\w+)", stmt))):
            with self.subTest(column=column):
                self.assertRegex(plain, rf"\bp\.{column}\b|\bas {column}\b")

    def test_ncaaf_always_returns_a_row_and_names_the_missing_case(self):
        """Empty registry and no active artifact stay distinct under one governed code.

        The governed RPC returns a single code for "no certified artifact"; the
        two gaps remain separate tests here, and total_rows vs artifact_id still
        say which one it is.
        """
        ncaaf = query_for("NCAAF")
        lowered = ncaaf.lower()
        self.assertIn("left join", lowered)  # survives an empty table
        # Both absence gaps share one governed code and one branch; registry_empty
        # is what separates them.
        self.assertRegex(
            lowered,
            r"when\s+picked\.artifact_id\s+is\s+null\s+or\s+picked\.promoted\s+is\s+not\s+true"
            r"\s+then\s+'ncaaf_certified_model_artifact_not_found'",
        )
        for token in ("'unverified'", "'model_certified'", "'model_hold'", "total_rows", "artifact_id"):
            self.assertIn(token, lowered)
        # Both absence gaps share the governed code, so the row must say which
        # one it is without the reader inferring from a raw count.
        final = lowered[lowered.rindex("\nselect") :]
        self.assertIn("(judged.total_rows = 0) as registry_empty", final)
        # The boolean is keyed to the verdict, so a held row can never be null
        # even when picked.certified evaluates to NULL.
        certified = verdict_expression(ncaaf).lower()
        self.assertRegex(certified, r"when\s+judged\.verdict\s*=\s*'unverified'\s+then\s+null")
        self.assertRegex(
            certified,
            r"when\s+judged\.verdict\s*=\s*'model_certified'\s+then\s+true\s+else\s+false",
        )
        self.assertEqual(len(re.findall(r"then\s+null", certified)), 1)


if __name__ == "__main__":
    unittest.main()
