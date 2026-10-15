"""P0 primary API Release fence: contract, crash and provider fault injection.

A source-only or mock pass does not authorize production migration, release,
or a real Render API call.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from v17.primary_api_release_fence import (
    PRIMARY_SERVICE,
    PrimaryReleaseHold,
    post_render_exactly_once,
    record_authenticated_readback,
    reserve_single_post,
)


class FakeDb:
    autocommit = True

    def __init__(self):
        self.used = False
        self.committed = False
        self.pending = False
        self.result = ()

    @contextmanager
    def transaction(self):
        self.pending = True
        try:
            yield
        except BaseException:
            self.pending = False
            raise
        else:
            self.committed = True
            self.pending = False

    def execute(self, sql, args):
        assert self.pending
        if "reserve_primary_api_post" in sql:
            if self.used:
                raise RuntimeError("PRIMARY_RELEASE_POST_ALREADY_RESERVED_RECONCILE_ONLY")
            self.used = True
            self.result = ("PRIMARY_RELEASE_POST_AUTHORIZED_ONCE",)
        elif "observe_primary_api_deploy" in sql:
            self.result = ("PRIMARY_RELEASE_PROVIDER_READBACK_ONLY",)
        else:
            raise AssertionError("Wrong SQL RPC")
        return self

    def fetchone(self):
        return self.result


def test_exact_sha_post_only_after_durable_commit():
    db = FakeDb()
    events = []

    def render(body):
        assert db.committed and not db.pending
        events.append(body)
        return {"id": "dep-abc123", "commit": {"id": "a" * 40}}

    receipt = post_render_exactly_once(
        db, render, service_id=PRIMARY_SERVICE, sha="a" * 40, run_id="ci-release"
    )
    assert events == [{"commitId": "a" * 40, "clearCache": "do_not_clear"}]
    assert receipt["status"] == "POST_ACK_ONLY_REQUIRES_LIVE_READBACK"
    assert receipt["can_execute"] is False
    with pytest.raises(RuntimeError, match="RECONCILE_ONLY"):
        post_render_exactly_once(
            db, render, service_id=PRIMARY_SERVICE, sha="a" * 40, run_id="ci-release"
        )
    assert len(events) == 1, "same SHA cannot issue second POST"


def test_lost_ack_burns_token_and_never_retries():
    db = FakeDb()
    events = []

    def lost_ack(body):
        assert db.committed
        events.append(body)
        raise TimeoutError("Render already received POST but response is lost")

    with pytest.raises(PrimaryReleaseHold, match="ACK_UNKNOWN_RECONCILE_ONLY"):
        post_render_exactly_once(
            db, lost_ack, service_id=PRIMARY_SERVICE, sha="a" * 40, run_id="ci-ack"
        )
    with pytest.raises(RuntimeError, match="RECONCILE_ONLY"):
        post_render_exactly_once(
            db, lost_ack, service_id=PRIMARY_SERVICE, sha="a" * 40, run_id="ci-ack"
        )
    assert len(events) == 1


@pytest.mark.parametrize("response", [
    None, [], {}, {"id": "dep-abc123"},
    {"id": "dep-abc123", "commit": {"id": "b" * 40}},
    {"id": "wrong", "commit": {"id": "a" * 40}},
])
def test_bad_or_missing_provider_ack_does_not_retry(response):
    db = FakeDb()
    called = []
    with pytest.raises(PrimaryReleaseHold, match="PROVIDER_"):
        post_render_exactly_once(
            db, lambda body: called.append(body) or response,
            service_id=PRIMARY_SERVICE, sha="a" * 40, run_id="ci-ack"
        )
    assert len(called) == 1 and db.used


@pytest.mark.parametrize("service,sha,owner", [
    ("srv-different", "a" * 40, "ci"),
    (PRIMARY_SERVICE, "a" * 39, "ci"),
    (PRIMARY_SERVICE, "A" * 40, "ci"),
    (PRIMARY_SERVICE, "a" * 40, "owner with spaces"),
    (PRIMARY_SERVICE, "a" * 40, ""),
])
def test_invalid_release_identity_never_mutates(service, sha, owner):
    db = FakeDb()
    with pytest.raises(PrimaryReleaseHold):
        reserve_single_post(db, service_id=service, sha=sha, run_id=owner)
    assert not db.used


def test_no_autocommit_prevents_hidden_outer_transaction_rollback():
    db = FakeDb()
    db.autocommit = False
    with pytest.raises(PrimaryReleaseHold, match="AUTOCOMMIT_REQUIRED"):
        reserve_single_post(db, service_id=PRIMARY_SERVICE, sha="a" * 40, run_id="ci")
    assert not db.used


def test_live_receipt_is_not_product_or_QA_pass():
    db = FakeDb()
    receipt = record_authenticated_readback(
        db, service_id=PRIMARY_SERVICE, sha="a" * 40, run_id="ci",
        deploy_id="dep-test123", observed_sha="a" * 40, live=True,
    )
    assert receipt["status"] == "LIVE_OBSERVED_NOT_QA_VERIFIED"
    assert receipt["can_execute"] is False


def test_private_primary_api_migration_has_single_burn_and_no_seeded_approval():
    sql = (
        Path(__file__).resolve().parent /
        "v17/sql/primary_api_single_post_release_fence.sql"
    ).read_text()
    assert sql.count("create or replace function wow_release_control.reserve_primary_api_post(") == 1
    assert sql.count("create or replace function wow_release_control.assert_primary_api_authority()") == 1
    assert sql.count("'POST_UNCERTAIN'") >= 2
    assert "PRIMARY_RELEASE_POST_ALREADY_RESERVED_RECONCILE_ONLY" in sql
    assert "for share of ident,anchor" in sql
    assert "PRIMARY_RELEASE_CONTROLLER_PRIVILEGE_LEAK" in sql
    assert "commitId" not in sql  # provider interaction belongs outside PostgreSQL
    assert "insert into wow_release_control.primary_api_release_identity" not in sql
