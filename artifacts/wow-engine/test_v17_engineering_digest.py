from v17 import engineering_digest as digest


def base_kwargs(**overrides):
    kwargs = dict(
        generated_at="2026-10-12T00:00:00Z",
        merged_last_24h=[],
        hold_prs=[],
        waiting_on_owner=[],
        stale_or_conflicted=[],
        last_main_sha="deadbeef",
        last_render_deployed_sha="cafef00d",
    )
    kwargs.update(overrides)
    return kwargs


def test_title_and_can_execute_marker_present():
    body = digest.render_digest(**base_kwargs())
    assert body.startswith(f"# {digest.DIGEST_TITLE}")
    assert "can_execute=false" in body


def test_empty_sections_render_none():
    body = digest.render_digest(**base_kwargs())
    assert "## Merged in the last 24h\n- none" in body
    assert "## On HOLD\n- none" in body
    assert "## Independent handoff or CODEOWNER decision (release scoped; other lanes continue)\n- none" in body
    assert "## Stale or conflicted\n- none" in body


def test_merged_prs_are_listed_without_reason():
    body = digest.render_digest(
        **base_kwargs(merged_last_24h=[{"number": 42, "title": "Fix thing"}])
    )
    assert "- #42 Fix thing" in body


def test_hold_prs_include_reason():
    body = digest.render_digest(
        **base_kwargs(hold_prs=[{"number": 7, "title": "Repair", "reason": "QA_REGRESSION_FAILED"}])
    )
    assert "- #7 Repair — `QA_REGRESSION_FAILED`" in body


def test_waiting_on_owner_includes_reason():
    body = digest.render_digest(
        **base_kwargs(
            waiting_on_owner=[
                {"number": 3, "title": "Trust root", "reason": "QA_TRUST_ROOT_OWNER_APPROVAL_MISSING"}
            ]
        )
    )
    assert "## Independent handoff or CODEOWNER decision (release scoped; other lanes continue)" in body
    assert "QA_TRUST_ROOT_OWNER_APPROVAL_MISSING" in body


def test_stale_or_conflicted_includes_reason():
    body = digest.render_digest(
        **base_kwargs(stale_or_conflicted=[{"number": 5, "title": "Old", "reason": "PR_UPDATE_CONFLICT"}])
    )
    assert "PR_UPDATE_CONFLICT" in body


def test_state_section_has_shas():
    body = digest.render_digest(**base_kwargs(last_main_sha="abc123", last_render_deployed_sha="def456"))
    assert "last main SHA: `abc123`" in body
    assert "last Render deployed SHA: `def456`" in body


def test_multiple_prs_each_rendered_once():
    body = digest.render_digest(
        **base_kwargs(
            merged_last_24h=[
                {"number": 1, "title": "A"},
                {"number": 2, "title": "B"},
            ]
        )
    )
    assert "- #1 A" in body
    assert "- #2 B" in body
    assert body.count("- #") == 2


def test_main_cli_reads_json_evidence_file(tmp_path, capsys):
    import json

    evidence = base_kwargs()
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence))
    rc = digest.main(["--evidence-json", str(path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert f"# {digest.DIGEST_TITLE}" in out
