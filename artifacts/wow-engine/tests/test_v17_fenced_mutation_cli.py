"""Negative validation for protected receipt-to-gateway integration (#1739)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from v17.engineering_fenced_writer import FencingHold
from v17.engineering_fenced_mutation_cli import deliver, receipt_identity


REPO = "gregoryharper84-ship-it/WOW-Dashboard"
ID = "68e69cd5-7f55-4baa-a2a2-8ab5b4c35313"
SHA = "a" * 40


def context():
    return {
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW": "wow-v17-engineering-auto-repair",
        "GITHUB_RUN_ID": "401111",
        "GITHUB_RUN_ATTEMPT": "2",
        "WOW_CAN_EXECUTE": "false",
        "WOW_DRY_RUN_ONLY": "true",
        "APP_TOKEN": "app-writer-only",
        "GH_TOKEN": "read-token-only",
        "WOW_ENGINEERING_LEASE_GROUP": "GLOBAL",
    }


def receipt(tmp_path, *, owner="github-actions:401111:2:wow-v17-engineering-auto-repair"):
    p = tmp_path / "lease.json"
    p.write_text(json.dumps([{"incident_id": 1739, "owner_id": owner, "epoch": 7}]))
    return p


def args(p, op="CREATE_BRANCH", payload_file=""):
    return SimpleNamespace(receipt=str(p), mutation_id=ID, operation=op,
        branch="wow-engineering/fenced-1739-7-68e69cd5",
        sha=SHA, payload_file=payload_file)


def test_valid_authenticated_existing_lease_is_bound_to_the_db(tmp_path):
    p = receipt(tmp_path)
    store = Mock()
    store.begin.return_value = "STARTED"
    store.confirm.return_value = True
    github = Mock()
    github.exact_readback.return_value = True
    out = deliver(args(p), context(), store=store, github=github)
    assert out == "EXACT_GITHUB_WRITE_CONFIRMED"
    intent = store.begin.call_args.args[0]
    assert (intent.incident_id, intent.epoch, intent.lease_group) == (1739,7,"GLOBAL")
    assert intent.owner_id.startswith("github-actions:401111:2:")
    github.create_branch.assert_called_once_with(intent)
    github.create_pr.assert_not_called()


@pytest.mark.parametrize("variable,value", [
    ("GITHUB_REPOSITORY", "other/repo"),
    ("GITHUB_REF", "refs/pull/1/merge"),
    ("GITHUB_WORKFLOW", "untrusted"),
    ("WOW_CAN_EXECUTE", "true"),
    ("WOW_DRY_RUN_ONLY", "false"),
])
def test_untrusted_run_context_cannot_begin_mutation(tmp_path,variable,value):
    env=context()
    env[variable]=value
    store=Mock()
    with pytest.raises(FencingHold,match="UNTRUSTED_ENGINEERING_WORKFLOW_CONTEXT"):
        deliver(args(receipt(tmp_path)),env,store=store,github=Mock())
    store.begin.assert_not_called()


@pytest.mark.parametrize("variable,value", [
    ("APP_TOKEN", ""), ("GH_TOKEN", ""), ("APP_TOKEN", "read-token-only"),
])
def test_missing_or_shared_write_identity_denied(tmp_path,variable,value):
    env=context()
    env[variable]=value
    store=Mock()
    with pytest.raises(FencingHold,match="INDEPENDENT_APP_WRITE_IDENTITY_REQUIRED"):
        deliver(args(receipt(tmp_path)),env,store=store,github=Mock())
    store.begin.assert_not_called()


@pytest.mark.parametrize("wrong_owner", [
    "github-actions:401110:2:wow-v17-engineering-auto-repair",
    "github-actions:401111:1:wow-v17-engineering-auto-repair",
    "manual:other-session"
])
def test_wrong_run_and_attempt_cannot_reuse_receipt(tmp_path,wrong_owner):
    store=Mock()
    with pytest.raises(FencingHold,match="WRITER_LEASE_RECEIPT_UNVERIFIED"):
        deliver(args(receipt(tmp_path,owner=wrong_owner)),context(),store=store,github=Mock())
    store.begin.assert_not_called()


def test_missing_lease_receipt_is_not_autoclaimed(tmp_path):
    with pytest.raises(FencingHold,match="WRITER_LEASE_RECEIPT_UNVERIFIED"):
        receipt_identity(tmp_path/"missing.json",context())


def test_create_pr_needs_real_body_and_uses_same_fence(tmp_path):
    p=receipt(tmp_path)
    file=tmp_path/"body.md"
    file.write_text("Source PR exact SHA; independent review required.")
    store=Mock()
    store.begin.return_value="STARTED"
    store.confirm.return_value=True
    github=Mock()
    github.exact_readback.return_value=True
    assert deliver(args(p,"CREATE_PR",str(file)),context(),store=store,github=github)=="EXACT_GITHUB_WRITE_CONFIRMED"
    github.create_pr.assert_called_once()
    assert store.begin.call_args.args[0].payload==file.read_text()


def test_non_pr_payload_is_rejected_before_any_db_request(tmp_path):
    store=Mock()
    with pytest.raises(FencingHold,match="NON_PR_BODY_FORBIDDEN"):
        deliver(args(receipt(tmp_path),"CREATE_BRANCH","bogus.md"),context(),store=store,github=Mock())
    store.begin.assert_not_called()


def test_ambiguous_github_write_does_not_create_second_post(tmp_path):
    p=receipt(tmp_path)
    store=Mock()
    store.begin.return_value="PENDING"
    store.confirm.return_value=True
    github=Mock()
    github.exact_readback.return_value=True
    assert deliver(args(p),context(),store=store,github=github)=="EXACT_GITHUB_WRITE_CONFIRMED"
    github.create_branch.assert_not_called()
    github.create_pr.assert_not_called()


def test_store_not_attested_cannot_be_skipped_with_real_writer(tmp_path):
    env=context()
    env.update(SUPABASE_URL="https://wrong-project.supabase.co",
               SUPABASE_SERVICE_ROLE_KEY="dummy-key",
               WOW_ENGINEERING_SUPABASE_PROJECT_ID="iczfhsmjrrafhvcpmqhr")
    with pytest.raises(FencingHold,match="LEASE_STORE_CONFIGURATION_INVALID"):
        deliver(args(receipt(tmp_path)),env,github=Mock())


def test_boolean_or_duplicate_lease_receipts_rejected(tmp_path):
    path=receipt(tmp_path)
    path.write_text(json.dumps([
        {"incident_id":1739,"owner_id":"github-actions:401111:2:a","epoch":7},
        {"incident_id":1739,"owner_id":"github-actions:401111:2:a","epoch":7}]))
    with pytest.raises(FencingHold,match="WRITER_LEASE_RECEIPT_UNVERIFIED"):
        receipt_identity(path,context())
