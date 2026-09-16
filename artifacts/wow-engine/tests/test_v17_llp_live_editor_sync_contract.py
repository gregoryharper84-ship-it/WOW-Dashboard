from pathlib import Path


SOURCE = Path(__file__).parents[1] / "LLP_V17_CUSTOM_GPT_INSTRUCTIONS.txt"


def test_llp_live_editor_sync_is_separate_from_backend_and_model_health():
    source = SOURCE.read_text()
    assert "Repository merge, CI, backend deploy, or production Action replay does not update the live LLP Custom GPT editor." in source
    assert "LIVE_GPT_EDITOR_SYNC=VERIFIED" in source
    assert "otherwise `PENDING`" in source
    assert "PENDING` is product configuration, not model capability" in source
    assert "Never convert it into `MODEL_UNAVAILABLE`" in source


def test_llp_full_stack_fixed_verified_requires_editor_sync():
    source = SOURCE.read_text()
    assert "Do not call the full LLP stack `FIXED_VERIFIED` while editor sync is `PENDING`" in source
    assert "BACKEND_RUNTIME" in source
    assert "MODEL_CAPABILITY" in source
    assert "REPOSITORY_GOVERNANCE" in source
    assert "LIVE_GPT_EDITOR_SYNC" in source
    assert "player/scalar prop fixes remain WOW-owned" in source
    assert "can_execute=false" in source


def test_llp_editor_source_remains_pasteable_under_eight_thousand_characters():
    source = SOURCE.read_text()
    assert len(source) < 8000
