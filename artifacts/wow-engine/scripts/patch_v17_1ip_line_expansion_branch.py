"""One-shot branch-only patcher for MLB 1IP line-expansion wiring.

The companion branch workflow runs this once, commits the three surgical
existing-file edits, and deletes both one-shot patch files in that same commit.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"PATCH_ANCHOR_COUNT_INVALID:{path}:{count}:{old[:80]}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    pick = ROOT / "artifacts" / "wow-engine" / "pick_request_runtime.py"
    oidc = ROOT / "artifacts" / "wow-engine" / "github_actions_oidc.py"
    test_oidc = ROOT / "artifacts" / "wow-engine" / "test_v17_github_oidc.py"

    replace_once(
        pick,
        "from v17.wnba_prop_candidate_registry import install_wnba_prop_candidate_registration_route\n",
        "from v17.mlb_1ip_line_expansion_maintenance import install_mlb_1ip_line_expansion_maintenance_route\n"
        "from v17.wnba_prop_candidate_registry import install_wnba_prop_candidate_registration_route\n",
    )
    replace_once(
        pick,
        "        install_wnba_prop_candidate_registration_route(\n"
        "            app,\n"
        "            auth_dependency=auth_dependency,\n"
        "            db_client_fn=get_client_fn,\n"
        "        )\n",
        "        install_wnba_prop_candidate_registration_route(\n"
        "            app,\n"
        "            auth_dependency=auth_dependency,\n"
        "            db_client_fn=get_client_fn,\n"
        "        )\n"
        "        install_mlb_1ip_line_expansion_maintenance_route(\n"
        "            app,\n"
        "            auth_dependency=auth_dependency,\n"
        "            db_client_fn=get_client_fn,\n"
        "        )\n",
    )

    anchor = (
        "FIRST_SIX_MODEL_MAINTENANCE_WORKFLOW_REF = (\n"
        "    f\"{REPOSITORY}/.github/workflows/wow-v17-first-six-model-maintenance.yml@{REF}\"\n"
        ")\n"
    )
    replace_once(
        oidc,
        anchor,
        anchor
        + "MLB_1IP_LINE_EXPANSION_MAINTENANCE_WORKFLOW_REF = (\n"
        + "    f\"{REPOSITORY}/.github/workflows/wow-v17-mlb-1ip-line-expansion-maintenance.yml@{REF}\"\n"
        + ")\n",
    )
    replace_once(
        oidc,
        "    FIRST_SIX_MODEL_MAINTENANCE_WORKFLOW_REF,\n})",
        "    FIRST_SIX_MODEL_MAINTENANCE_WORKFLOW_REF,\n"
        "    MLB_1IP_LINE_EXPANSION_MAINTENANCE_WORKFLOW_REF,\n})",
    )
    replace_once(
        oidc,
        '    "FIRST_SIX_MODEL_MAINTENANCE_WORKFLOW_REF",\n',
        '    "FIRST_SIX_MODEL_MAINTENANCE_WORKFLOW_REF",\n'
        '    "MLB_1IP_LINE_EXPANSION_MAINTENANCE_WORKFLOW_REF",\n',
    )

    pin = (
        '        (oidc.FIRST_SIX_MODEL_MAINTENANCE_WORKFLOW_REF, '
        '"/.github/workflows/wow-v17-first-six-model-maintenance.yml@refs/heads/main"),\n'
    )
    replace_once(
        test_oidc,
        pin,
        pin
        + '        (oidc.MLB_1IP_LINE_EXPANSION_MAINTENANCE_WORKFLOW_REF, '
        + '"/.github/workflows/wow-v17-mlb-1ip-line-expansion-maintenance.yml@refs/heads/main"),\n',
    )
    # The allowlist and PR-denial parameter sets each contain this exact line.
    # Replace the first and second occurrences explicitly after confirming two.
    text = test_oidc.read_text(encoding="utf-8")
    token = "        oidc.FIRST_SIX_MODEL_MAINTENANCE_WORKFLOW_REF,\n"
    if text.count(token) != 2:
        raise RuntimeError("OIDC_TEST_FIRST_SIX_ANCHOR_COUNT_INVALID")
    text = text.replace(
        token,
        token + "        oidc.MLB_1IP_LINE_EXPANSION_MAINTENANCE_WORKFLOW_REF,\n",
    )
    test_oidc.write_text(text, encoding="utf-8")

    # Prove all edited modules compile before the workflow commits anything.
    import py_compile
    for path in (pick, oidc, test_oidc):
        py_compile.compile(str(path), doraise=True)

    # Self-delete: these one-shot engineering helpers never land in the PR diff.
    (ROOT / ".github" / "workflows" / "v17-1ip-line-expansion-patch-branch.yml").unlink()
    Path(__file__).unlink()


if __name__ == "__main__":
    main()
