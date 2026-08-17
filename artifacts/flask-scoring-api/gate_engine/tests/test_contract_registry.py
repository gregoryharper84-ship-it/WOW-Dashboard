"""
test_contract_registry.py
WOW-PATCH-2026-08-16-CONTRACT-REGISTRY-POPULATION-V1

Validation tests for docs/wow/contracts/registry.yaml.

These tests enforce:
  1. No duplicate canonical_contract_ids
  2. Every entry has a sha256 (or null with explicit unresolved documentation)
  3. Only valid declared_status values
  4. Unsafe promotion prevention (TEST_ONLY / RESEARCH_ONLY / PROPOSED / PROBABILITY_ONLY
     may never have project_authority_status = ACTIVE or EMERGENCY_ACTIVE)
  5. can_execute=false on every entry
  6. Broken canonical document links (canonical_path must point to a real file if not null)
  7. Every unresolved entry appears in registry_metadata.unresolved_authority_items
  8. Registry schema completeness (all required fields present)
  9. Patch ID collision entries are documented in unresolved_authority_items
  10. No entry has project_authority_status that exceeds declared_status authority tier

can_execute=false
"""

import os
import hashlib
import unittest
import yaml

# ── helpers ──────────────────────────────────────────────────────────────────

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
REGISTRY_PATH = os.path.join(REPO_ROOT, "docs", "wow", "contracts", "registry.yaml")
CANONICAL_DIR = os.path.join(REPO_ROOT, "docs", "wow", "contracts", "canonical")

VALID_STATUSES = {
    "ACTIVE",
    "EMERGENCY_ACTIVE",
    "TEST_ONLY",
    "RESEARCH_ONLY",
    "PROBABILITY_ONLY",
    "GOVERNANCE_DOCUMENT",
    "PROPOSED",
    "UNRESOLVED_AUTHORITY",
}

REQUIRED_FIELDS = {
    "canonical_contract_id",
    "source_filename",
    "canonical_path",
    "sha256",
    "version",
    "declared_status",
    "project_authority_status",
    "lane",
    "precedence",
    "supersedes",
    "effective_date",
    "can_execute",
    "notes",
}

# Promotion constraints: declared_status → allowed project_authority_status values
# Ordered from most to least restrictive.
PROMOTION_RULES = {
    "TEST_ONLY":        {"TEST_ONLY"},
    "RESEARCH_ONLY":    {"RESEARCH_ONLY"},
    "PROPOSED":         {"PROPOSED", "UNRESOLVED_AUTHORITY"},
    "PROBABILITY_ONLY": {"PROBABILITY_ONLY", "UNRESOLVED_AUTHORITY"},
    "GOVERNANCE_DOCUMENT": {"GOVERNANCE_DOCUMENT"},
    "ACTIVE":           {"ACTIVE", "UNRESOLVED_AUTHORITY"},
    "EMERGENCY_ACTIVE": {"EMERGENCY_ACTIVE", "UNRESOLVED_AUTHORITY"},
    "UNRESOLVED_AUTHORITY": {"UNRESOLVED_AUTHORITY"},
}


def load_registry():
    """Load and return the parsed registry YAML."""
    with open(REGISTRY_PATH, "r") as f:
        return yaml.safe_load(f)


def sha256_file(path):
    """Return lowercase hex SHA-256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


# ── test class ───────────────────────────────────────────────────────────────

class TestContractRegistry(unittest.TestCase):
    """Validation suite for docs/wow/contracts/registry.yaml."""

    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry()
        cls.contracts = cls.registry.get("contracts", [])
        cls.metadata = cls.registry.get("registry_metadata", {})
        cls.unresolved_ids = {
            item["id"]
            for item in cls.metadata.get("unresolved_authority_items", [])
        }

    # ── T1: registry file exists and parses ─────────────────────────────────

    def test_registry_file_exists(self):
        """registry.yaml must exist and parse as valid YAML."""
        self.assertTrue(
            os.path.isfile(REGISTRY_PATH),
            f"registry.yaml not found at {REGISTRY_PATH}",
        )
        self.assertIsInstance(self.contracts, list)
        self.assertGreater(len(self.contracts), 0, "registry.yaml has no contracts")

    # ── T2: metadata fields ──────────────────────────────────────────────────

    def test_registry_metadata_can_execute_false(self):
        """registry_metadata.can_execute must be false."""
        self.assertFalse(
            self.metadata.get("can_execute", True),
            "registry_metadata.can_execute must be false",
        )

    def test_registry_metadata_publish_authorized_false(self):
        """registry_metadata.publish_authorized must be false."""
        self.assertFalse(
            self.metadata.get("publish_authorized", True),
            "registry_metadata.publish_authorized must be false",
        )

    def test_unresolved_authority_items_present(self):
        """registry_metadata must declare at least one unresolved_authority_items entry."""
        items = self.metadata.get("unresolved_authority_items", [])
        self.assertGreater(len(items), 0, "No unresolved_authority_items declared")
        for item in items:
            self.assertIn("id", item, f"unresolved_authority_item missing 'id': {item}")
            self.assertIn(
                "description", item,
                f"unresolved_authority_item missing 'description': {item}",
            )

    # ── T3: no duplicate IDs ────────────────────────────────────────────────

    def test_no_duplicate_canonical_contract_ids(self):
        """Every canonical_contract_id must be unique."""
        ids = [c["canonical_contract_id"] for c in self.contracts]
        seen = set()
        duplicates = []
        for cid in ids:
            if cid in seen:
                duplicates.append(cid)
            seen.add(cid)
        self.assertEqual(
            [], duplicates,
            f"Duplicate canonical_contract_ids found: {duplicates}",
        )

    # ── T4: required fields present on every entry ──────────────────────────

    def test_all_required_fields_present(self):
        """Every contract entry must have all required fields."""
        for c in self.contracts:
            cid = c.get("canonical_contract_id", "<unknown>")
            missing = REQUIRED_FIELDS - set(c.keys())
            self.assertEqual(
                set(), missing,
                f"Contract {cid} is missing required fields: {missing}",
            )

    # ── T5: valid declared_status ────────────────────────────────────────────

    def test_valid_declared_status(self):
        """Every declared_status must be one of the allowed values."""
        for c in self.contracts:
            cid = c["canonical_contract_id"]
            ds = c.get("declared_status")
            self.assertIn(
                ds, VALID_STATUSES,
                f"{cid}: invalid declared_status '{ds}'",
            )

    def test_valid_project_authority_status(self):
        """Every project_authority_status must be one of the allowed values."""
        for c in self.contracts:
            cid = c["canonical_contract_id"]
            pas = c.get("project_authority_status")
            self.assertIn(
                pas, VALID_STATUSES,
                f"{cid}: invalid project_authority_status '{pas}'",
            )

    # ── T6: unsafe promotion prevention ─────────────────────────────────────

    def test_no_unsafe_promotion(self):
        """
        TEST_ONLY, RESEARCH_ONLY, PROPOSED, PROBABILITY_ONLY, and
        GOVERNANCE_DOCUMENT statuses may not be promoted beyond their allowed
        project_authority_status tier.

        E.g. TEST_ONLY → project_authority_status ACTIVE is forbidden.
        """
        for c in self.contracts:
            cid = c["canonical_contract_id"]
            ds = c.get("declared_status")
            pas = c.get("project_authority_status")
            allowed = PROMOTION_RULES.get(ds, VALID_STATUSES)
            self.assertIn(
                pas, allowed,
                f"{cid}: unsafe promotion — declared_status={ds} but "
                f"project_authority_status={pas}. Allowed: {allowed}",
            )

    def test_test_only_never_active(self):
        """TEST_ONLY documents must never have project_authority_status=ACTIVE."""
        for c in self.contracts:
            if c.get("declared_status") == "TEST_ONLY":
                cid = c["canonical_contract_id"]
                self.assertNotEqual(
                    c.get("project_authority_status"), "ACTIVE",
                    f"{cid}: TEST_ONLY document promoted to ACTIVE — forbidden",
                )
                self.assertNotEqual(
                    c.get("project_authority_status"), "EMERGENCY_ACTIVE",
                    f"{cid}: TEST_ONLY document promoted to EMERGENCY_ACTIVE — forbidden",
                )

    def test_research_only_never_active(self):
        """RESEARCH_ONLY documents must never have project_authority_status=ACTIVE."""
        for c in self.contracts:
            if c.get("declared_status") == "RESEARCH_ONLY":
                cid = c["canonical_contract_id"]
                self.assertNotEqual(
                    c.get("project_authority_status"), "ACTIVE",
                    f"{cid}: RESEARCH_ONLY document promoted to ACTIVE — forbidden",
                )

    def test_probability_only_never_active(self):
        """PROBABILITY_ONLY documents must never have project_authority_status=ACTIVE."""
        for c in self.contracts:
            if c.get("declared_status") == "PROBABILITY_ONLY":
                cid = c["canonical_contract_id"]
                self.assertNotEqual(
                    c.get("project_authority_status"), "ACTIVE",
                    f"{cid}: PROBABILITY_ONLY document promoted to ACTIVE — forbidden",
                )
                self.assertNotEqual(
                    c.get("project_authority_status"), "EMERGENCY_ACTIVE",
                    f"{cid}: PROBABILITY_ONLY document promoted to EMERGENCY_ACTIVE — forbidden",
                )

    def test_proposed_never_active(self):
        """PROPOSED documents must never have project_authority_status=ACTIVE."""
        for c in self.contracts:
            if c.get("declared_status") == "PROPOSED":
                cid = c["canonical_contract_id"]
                self.assertNotEqual(
                    c.get("project_authority_status"), "ACTIVE",
                    f"{cid}: PROPOSED document promoted to ACTIVE — forbidden",
                )

    # ── T7: can_execute=false on every entry ─────────────────────────────────

    def test_can_execute_false_on_all_entries(self):
        """Every contract entry must have can_execute=false."""
        for c in self.contracts:
            cid = c["canonical_contract_id"]
            self.assertFalse(
                c.get("can_execute", True),
                f"{cid}: can_execute is not false",
            )

    # ── T8: canonical file links ─────────────────────────────────────────────

    def test_canonical_paths_exist(self):
        """
        For entries with a non-null canonical_path, the file must exist
        on disk in docs/wow/contracts/canonical/.
        """
        for c in self.contracts:
            cid = c["canonical_contract_id"]
            cp = c.get("canonical_path")
            if cp is None:
                continue  # null canonical_path is allowed (unresolved entries)
            full_path = os.path.join(REPO_ROOT, cp)
            self.assertTrue(
                os.path.isfile(full_path),
                f"{cid}: canonical_path '{cp}' does not exist on disk",
            )

    # ── T9: SHA-256 integrity ────────────────────────────────────────────────

    def test_canonical_sha256_matches_file(self):
        """
        For entries with both a canonical_path and a sha256, verify that
        the on-disk canonical copy matches the recorded hash.

        NOTE: The registry records sha256 of the source file (source_filename),
        not necessarily the canonical copy (which is identical since we cp
        without modification). Both must match.
        """
        for c in self.contracts:
            cid = c["canonical_contract_id"]
            cp = c.get("canonical_path")
            recorded_hash = c.get("sha256")

            if cp is None or recorded_hash is None:
                continue  # unresolved entries; skip hash check

            full_path = os.path.join(REPO_ROOT, cp)
            if not os.path.isfile(full_path):
                continue  # caught by test_canonical_paths_exist

            actual_hash = sha256_file(full_path)
            self.assertEqual(
                actual_hash, recorded_hash,
                f"{cid}: canonical file sha256 mismatch.\n"
                f"  Expected: {recorded_hash}\n"
                f"  Actual:   {actual_hash}\n"
                f"  File:     {full_path}",
            )

    # ── T10: unresolved entries documented ───────────────────────────────────

    def test_unresolved_authority_entries_have_null_sha256(self):
        """
        Entries with project_authority_status=UNRESOLVED_AUTHORITY that have
        NO source document at all (source_filename=null) must have sha256=null.

        Entries that DO have a source_filename (e.g. a ZIP with known hash) may
        still have project_authority_status=UNRESOLVED_AUTHORITY for their
        individual contents, but the ZIP sha256 itself is valid and not null.
        """
        for c in self.contracts:
            cid = c["canonical_contract_id"]
            pas = c.get("project_authority_status")
            src = c.get("source_filename")
            sha = c.get("sha256")
            # Only entries with NO source document at all must have sha256=null
            if pas == "UNRESOLVED_AUTHORITY" and src is None:
                self.assertIsNone(
                    sha,
                    f"{cid}: UNRESOLVED_AUTHORITY with no source_filename must have "
                    f"sha256=null, not '{sha}'",
                )

    def test_unresolved_authority_items_referenced_in_notes(self):
        """
        UNRESOLVED_AUTHORITY entries with null sha256 must reference an
        UNRESOLVED-NNN id in their notes field.
        """
        for c in self.contracts:
            cid = c["canonical_contract_id"]
            pas = c.get("project_authority_status")
            sha = c.get("sha256")
            notes = c.get("notes", "") or ""

            if pas == "UNRESOLVED_AUTHORITY" and sha is None:
                has_ref = any(uid in notes for uid in self.unresolved_ids)
                self.assertTrue(
                    has_ref,
                    f"{cid}: UNRESOLVED_AUTHORITY entry with null sha256 must "
                    f"reference an unresolved_authority_items id in notes. "
                    f"Available ids: {self.unresolved_ids}",
                )

    # ── T11: patch ID collision documented ───────────────────────────────────

    def test_patch_id_collision_documented(self):
        """
        UNRESOLVED-003 (PATCH-014/015 collision) must appear in
        unresolved_authority_items and be referenced in the notes of at
        least one contract.
        """
        self.assertIn(
            "UNRESOLVED-003", self.unresolved_ids,
            "UNRESOLVED-003 (PATCH-014/015 collision) not found in "
            "unresolved_authority_items",
        )
        refs = [
            c["canonical_contract_id"]
            for c in self.contracts
            if "UNRESOLVED-003" in (c.get("notes") or "")
        ]
        self.assertGreater(
            len(refs), 0,
            "UNRESOLVED-003 must be referenced in at least one contract's notes",
        )

    # ── T12: minimum inventory check ─────────────────────────────────────────

    def test_minimum_required_contracts_present(self):
        """
        The registry must contain at minimum these canonical_contract_ids.
        This guards against accidental deletion.
        """
        required_ids = {
            "WOW-MLB-FIRST-INNING-PITCH-COUNT-EXPERT-SKILL-V3",  # TEST_ONLY — must stay
            "WOW-MLB-PITCHER-FAILURE-PATH-EXPERT-SKILL-V2",       # RESEARCH_ONLY — must stay
            "WOW-LLP-MONEYLINE-PROBABILITY-EXPERT-SKILL",          # PROBABILITY_ONLY — must stay
            "WOW-PATCH-2026-08-01-LLP-SLATE-INTEGRITY",            # ACTIVE LLP patch
            "WOW-PATCH-2026-07-30-WNBA-MLB-GOVERNANCE",            # ACTIVE MLB/WNBA patch
            "WOW-PATCH-2026-07-23-SLIP-CONSISTENCY",               # ACTIVE slip patch
            "WOW-PATCH-CROSS-SPORT-HIGH-PROBABILITY-SELECTOR",     # PROPOSED — must not be ACTIVE
            "WOW-MLB-PITCHER-FAILURE-PATH-PATCHES-010-013",        # UNRESOLVED_AUTHORITY
            "WOW-KALSHI-RECOVERY-COMBO-GOVERNANCE",                # EMERGENCY_ACTIVE (resolved #251)
        }
        present_ids = {c["canonical_contract_id"] for c in self.contracts}
        missing = required_ids - present_ids
        self.assertEqual(
            set(), missing,
            f"Required contract IDs missing from registry: {missing}",
        )

    # ── T13: TEST_ONLY and RESEARCH_ONLY ceiling note ─────────────────────────

    def test_test_only_notes_contain_ceiling_warning(self):
        """TEST_ONLY entries must document their MODEL_QUALIFIED_HOLD ceiling in notes."""
        for c in self.contracts:
            if c.get("declared_status") == "TEST_ONLY":
                cid = c["canonical_contract_id"]
                notes = (c.get("notes") or "").lower()
                self.assertIn(
                    "test_only", notes,
                    f"{cid}: TEST_ONLY entry notes must reference TEST_ONLY status",
                )

    def test_research_only_notes_contain_ceiling_warning(self):
        """RESEARCH_ONLY entries must document their ceiling in notes."""
        for c in self.contracts:
            if c.get("declared_status") == "RESEARCH_ONLY":
                cid = c["canonical_contract_id"]
                notes = (c.get("notes") or "").lower()
                self.assertIn(
                    "research_only", notes,
                    f"{cid}: RESEARCH_ONLY entry notes must reference RESEARCH_ONLY status",
                )

    # ── T14: canonical dir exists ─────────────────────────────────────────────

    def test_canonical_directory_exists(self):
        """docs/wow/contracts/canonical/ must exist."""
        self.assertTrue(
            os.path.isdir(CANONICAL_DIR),
            f"canonical directory not found at {CANONICAL_DIR}",
        )

    def test_canonical_directory_not_empty(self):
        """docs/wow/contracts/canonical/ must contain at least one file."""
        files = os.listdir(CANONICAL_DIR)
        self.assertGreater(
            len(files), 0,
            "canonical directory is empty",
        )

    # ── T15: no fabricated hashes ────────────────────────────────────────────

    def test_no_placeholder_sha256_values(self):
        """
        sha256 values must not be placeholder strings (e.g. 'TODO', 'null',
        'placeholder', 'abc123'). They must be lowercase 64-char hex strings
        or Python None.
        """
        import re
        hex64 = re.compile(r"^[0-9a-f]{64}$")
        for c in self.contracts:
            cid = c["canonical_contract_id"]
            sha = c.get("sha256")
            if sha is None:
                continue  # null is allowed for unresolved entries
            self.assertIsInstance(sha, str, f"{cid}: sha256 must be a string or null")
            self.assertTrue(
                hex64.match(sha),
                f"{cid}: sha256 '{sha}' is not a valid 64-character lowercase hex string",
            )


    # ── T16: Kalshi recovery authority invariants (WOW-#251) ─────────────────

    def test_kalshi_recovery_contract_is_emergency_active(self):
        """
        WOW-KALSHI-RECOVERY-COMBO-GOVERNANCE must be formally registered as
        EMERGENCY_ACTIVE with a canonical source document and SHA-256.
        Resolves UNRESOLVED-002; registration must not newly activate behavior.
        """
        kalshi = next(
            (c for c in self.contracts
             if c["canonical_contract_id"] == "WOW-KALSHI-RECOVERY-COMBO-GOVERNANCE"),
            None,
        )
        self.assertIsNotNone(kalshi, "WOW-KALSHI-RECOVERY-COMBO-GOVERNANCE missing from registry")

        self.assertEqual(
            kalshi.get("declared_status"), "EMERGENCY_ACTIVE",
            "Kalshi recovery contract must have declared_status=EMERGENCY_ACTIVE",
        )
        self.assertEqual(
            kalshi.get("project_authority_status"), "EMERGENCY_ACTIVE",
            "Kalshi recovery contract must have project_authority_status=EMERGENCY_ACTIVE "
            "(was UNRESOLVED_AUTHORITY before WOW-#251)",
        )
        self.assertEqual(
            kalshi.get("lane"), "KALSHI_PORTFOLIO_GOVERNANCE",
            "Kalshi recovery contract must be in lane=KALSHI_PORTFOLIO_GOVERNANCE",
        )
        self.assertFalse(
            kalshi.get("can_execute", True),
            "Kalshi recovery contract must have can_execute=false",
        )
        self.assertEqual(
            kalshi.get("effective_date"), "2026-07-21",
            "Effective date must be 2026-07-21 (original activation, not registration date)",
        )

    def test_kalshi_recovery_contract_has_canonical_document(self):
        """
        The Kalshi recovery canonical document must exist on disk and its
        SHA-256 must match the registry entry.
        """
        kalshi = next(
            (c for c in self.contracts
             if c["canonical_contract_id"] == "WOW-KALSHI-RECOVERY-COMBO-GOVERNANCE"),
            None,
        )
        self.assertIsNotNone(kalshi, "WOW-KALSHI-RECOVERY-COMBO-GOVERNANCE missing from registry")

        canonical_path = kalshi.get("canonical_path")
        self.assertIsNotNone(canonical_path, "canonical_path must not be null after #251 resolution")

        abs_path = os.path.join(REPO_ROOT, canonical_path)
        self.assertTrue(
            os.path.isfile(abs_path),
            f"Canonical document not found at: {abs_path}",
        )

        sha = kalshi.get("sha256")
        self.assertIsNotNone(sha, "sha256 must not be null after #251 resolution")
        actual_sha = sha256_file(abs_path)
        self.assertEqual(
            sha, actual_sha,
            f"SHA-256 mismatch for {canonical_path}: "
            f"registry={sha}, file={actual_sha}",
        )

    def test_kalshi_recovery_contract_enforces_no_execute_and_no_capital(self):
        """
        The Kalshi recovery canonical document must assert can_execute=false
        and capital_allocation=false in its content.
        These are unconditional invariants that must survive any registry edit.
        """
        kalshi = next(
            (c for c in self.contracts
             if c["canonical_contract_id"] == "WOW-KALSHI-RECOVERY-COMBO-GOVERNANCE"),
            None,
        )
        self.assertIsNotNone(kalshi)
        canonical_path = kalshi.get("canonical_path")
        if canonical_path is None:
            self.skipTest("canonical_path is null — document not yet created")

        abs_path = os.path.join(REPO_ROOT, canonical_path)
        if not os.path.isfile(abs_path):
            self.skipTest(f"canonical document not found at {abs_path}")

        with open(abs_path) as f:
            content = f.read()

        self.assertIn(
            "can_execute", content.lower(),
            "Canonical document must assert can_execute invariant",
        )
        self.assertIn(
            "capital_allocation", content.lower(),
            "Canonical document must assert capital_allocation=false invariant",
        )
        self.assertIn(
            "false", content.lower(),
            "Canonical document must contain 'false' (for can_execute and capital_allocation)",
        )
        self.assertIn(
            "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
            content.upper().replace("-", "_"),
            "Canonical document must contain the DRY_RUN_ONLY execution rule",
        )

    def test_kalshi_recovery_unresolved_002_removed(self):
        """
        After WOW-#251, UNRESOLVED-002 must no longer appear in
        unresolved_authority_items. Its resolution is recorded in the
        Kalshi contract entry's notes and the canonical document.
        """
        unresolved_ids = [i["id"] for i in self.metadata.get("unresolved_authority_items", [])]
        self.assertNotIn(
            "UNRESOLVED-002", unresolved_ids,
            "UNRESOLVED-002 must be removed from unresolved_authority_items "
            "after WOW-#251 resolution",
        )
        # The resolution note should appear in the contract's notes
        kalshi = next(
            (c for c in self.contracts
             if c["canonical_contract_id"] == "WOW-KALSHI-RECOVERY-COMBO-GOVERNANCE"),
            None,
        )
        notes = (kalshi.get("notes") or "") if kalshi else ""
        self.assertIn(
            "UNRESOLVED-002", notes,
            "Kalshi contract notes must reference UNRESOLVED-002 (as resolved)",
        )


if __name__ == "__main__":
    unittest.main()
