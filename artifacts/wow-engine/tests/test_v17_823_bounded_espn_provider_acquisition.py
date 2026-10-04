from pathlib import Path

SOURCE = Path(__file__).parents[1] / "v17" / "scout_secondary_source.py"
RUNTIME = Path(__file__).parents[1] / "v17" / "full_board_runtime.py"

def test_compact_espn_discovery_bounds_provider_fetch_before_compaction():
    source = SOURCE.read_text(encoding="utf-8")
    runtime = RUNTIME.read_text(encoding="utf-8")
    assert "provider_limit" in source, "provider transport must expose a bounded fetch limit"
    assert "provider_limit=250" in runtime, "compact discovery must bound ESPN acquisition before response compaction"
    assert '"limit": bounded_limit' in source

def test_bounded_provider_fetch_remains_research_only():
    runtime = RUNTIME.read_text(encoding="utf-8")
    assert '"prediction_authority": False' in runtime
    assert '"exact_line_authority": False' in runtime
    assert '"can_execute": False' in runtime
