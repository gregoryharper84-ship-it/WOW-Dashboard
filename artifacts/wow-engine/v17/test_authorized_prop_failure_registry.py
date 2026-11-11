"""All new source/handoff failures are registered, not rewritten as model absence."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs" / "failure_codes.md"
SOURCE_FILES = [
    ROOT / "v17" / "authorized_prop_board_discovery.py",
    ROOT / "v17" / "authorized_prop_board_route.py",
    ROOT / "v17" / "authorized_prop_board_handoff.py",
]
NON_ERROR_NAMES = {
    "PROP_FEED_ACCOUNT_USE_AUTHORIZED",
    "PROP_FEED_API_KEY",
    "PROP_HANDOFF_V1",
}


def test_all_authorized_acquisition_failure_codes_are_in_canonical_registry():
    source = "\n".join(path.read_text(encoding="utf-8") for path in SOURCE_FILES)
    codes = set(re.findall(r"PROP_(?:FEED|HANDOFF|IDENTITY|OFFER)_[A-Z0-9_]+", source))
    codes.difference_update(NON_ERROR_NAMES)
    registry = REGISTRY.read_text(encoding="utf-8")
    missing = [code for code in sorted(codes) if "`" + code + "`" not in registry]
    assert not missing, f"Unregistered native failure codes: {missing}"


def test_registry_separates_data_acquisition_from_model_availability():
    body = REGISTRY.read_text(encoding="utf-8")
    section = body.split("## Authorized third-party player-prop acquisition and handoff", 1)[1]
    assert "cannot be relabeled" in section
    assert "MODEL_UNAVAILABLE" in section
    assert "can_execute=false" in section
    assert "SOURCE_ADAPTER" in section
    assert "OFFICIAL_IDENTITY" in section
