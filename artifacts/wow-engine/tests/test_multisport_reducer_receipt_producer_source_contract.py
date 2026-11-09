"""Always-on source fence for the reducer-owned NFL receipt producer.

These are source invariants, NOT substitutes for real PostgreSQL integration,
independent SIRT acceptance, or a deployment. The admission candidate must
keep failing closed (LLP_REDUCER_ORIGIN_RECEIPT_UNAVAILABLE) until the
independently approved producer/attestor from this candidate exists.
"""
from pathlib import Path
import re

ENGINE = Path(__file__).resolve().parents[1]
PRODUCER = (
    ENGINE / "v17/sql_candidates/20261106_nfl_reducer_owned_receipt_UNDEPLOYED.sql"
)
ADMISSION = (
    ENGINE / "v17/sql_candidates/20261105_multisport_nfl_receipt_admission_UNDEPLOYED.sql"
)
DEPLOYED = ENGINE / "20260913_nfl_governed_probability_publication.sql"


def producer_source() -> str:
    return PRODUCER.read_text(encoding="utf-8")


def reducer_function(source: str, name: str, end: str = "END $f$;") -> str:
    return source.split(f"FUNCTION {name}", 1)[1].split(end, 1)[0]


def test_every_deployed_terminal_blocker_survives_the_split():
    """Splitting the reducer must not drop a single pre-existing gate."""
    deployed = DEPLOYED.read_text(encoding="utf-8")
    deployed_body = deployed.split(
        "create or replace function public.wow_v17_reduce_nfl_event_terminal_label(", 1
    )[1].split("$function$;", 1)[0]
    blockers = set(re.findall(r"array_append\(v_blockers,'([A-Z_]+)'\)", deployed_body))
    assert len(blockers) >= 10, blockers
    pure = reducer_function(
        producer_source(),
        "wow_terminal_reducer.reduce_nfl_event_terminal_label(",
        end="$function$;",
    )
    for blocker in blockers:
        assert blocker in pure, blocker


def test_pure_reduction_never_mints_receipt_identity():
    pure = reducer_function(
        producer_source(),
        "wow_terminal_reducer.reduce_nfl_event_terminal_label(",
        end="$function$;",
    )
    assert "gen_random_uuid" not in pure
    assert "reducer_receipt" not in pure


def test_wrapper_emits_receipt_only_for_authenticated_pass():
    wrapper = reducer_function(
        producer_source(), "public.wow_v17_reduce_nfl_event_terminal_label("
    )
    assert "issue_nfl_reducer_receipt" in wrapper
    assert "IF v_receipt IS NULL THEN" in wrapper
    assert "REDUCER_RECEIPT_STALE_OR_UNAVAILABLE" in wrapper
    assert "'probability_publishable', false" in wrapper
    assert "'can_execute', false" in wrapper


def test_issuer_is_exactly_once_and_refuses_stale_rebinding():
    issuer = reducer_function(
        producer_source(), "wow_terminal_reducer.issue_nfl_reducer_receipt("
    )
    assert "pg_advisory_xact_lock" in issuer
    assert "reduction_payload IS DISTINCT FROM p_reduction" in issuer
    assert "RETURN NULL" in issuer
    # A caller may not hand the issuer a pre-minted receipt.
    assert "p_reduction ? 'reducer_receipt_id'" in issuer
    assert "p_reduction ? 'reducer_receipt_sha256'" in issuer
    assert "SECURITY DEFINER" in issuer


def test_attestor_recomputes_digest_instead_of_trusting_the_row():
    attestor = reducer_function(
        producer_source(), "wow_terminal_reducer.verify_nfl_reducer_receipt("
    )
    assert "nfl_receipt_digest" in attestor
    compact = re.sub(r"\s+", " ", attestor)
    for field in ("score_snapshot_id", "reducer_receipt_id", "reducer_receipt_sha256"):
        assert f"o.{field} = p_{field}" in compact, field
    assert "'can_execute' = 'false'" in compact


def test_origin_ledger_is_append_only_and_pass_only():
    source = producer_source()
    assert "BEFORE UPDATE OR DELETE ON wow_terminal_reducer.origin_receipts" in source
    assert "BEFORE TRUNCATE ON wow_terminal_reducer.origin_receipts" in source
    assert "WOW_TERMINAL_REDUCER_ORIGIN_RECEIPT_IMMUTABLE" in source
    ledger = source.split("CREATE TABLE IF NOT EXISTS wow_terminal_reducer.origin_receipts (", 1)[1].split(");", 1)[0]
    assert "reduction_payload->>'status' = 'PASS'" in ledger
    assert "NOT (reduction_payload ? 'reducer_receipt_id')" in ledger


def test_producer_never_writes_the_admission_ledger():
    source = producer_source()
    assert "wow_v17_multisport_event_prediction_receipts" not in source
    assert "GRANT INSERT" not in source
    assert "GRANT ALL" not in source


def test_private_origin_is_revoked_from_every_application_role():
    source = producer_source()
    for role in ("PUBLIC", "anon", "authenticated", "service_role"):
        assert f"REVOKE ALL ON SCHEMA wow_terminal_reducer FROM {role}" in source
        assert (
            f"REVOKE EXECUTE ON FUNCTION "
            f"wow_terminal_reducer.issue_nfl_reducer_receipt(uuid, jsonb) FROM {role}"
        ) in source
    assert (
        "GRANT EXECUTE ON FUNCTION "
        "public.wow_v17_reduce_nfl_event_terminal_label(uuid) TO service_role"
    ) in source


def test_admission_candidate_still_fails_closed_until_independent_deploy():
    admission = ADMISSION.read_text(encoding="utf-8")
    assert "LLP_REDUCER_ORIGIN_RECEIPT_UNAVAILABLE" in admission
    assert "to_regprocedure(" in admission
    # The consumer is not edited by this candidate.
    assert "CREATE TABLE wow_terminal_reducer.origin_receipts" not in admission
