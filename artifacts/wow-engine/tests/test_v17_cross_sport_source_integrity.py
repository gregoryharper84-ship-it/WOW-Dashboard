"""Adversarial and happy-path tests for canonical source-integrity boundary."""
from datetime import datetime, timezone
import hashlib
import json
import pytest
from v17.cross_sport_source_integrity import InventoryIntegrityError, verify_inventory_bytes

NOW = datetime(2026, 10, 12, 17, tzinfo=timezone.utc)
EVENT = {"sport": "NFL", "official_event_id": "nfl-123",
         "start_utc": "2026-10-15T00:00:00Z", "status": "SCHEDULED"}

def call(obj=None):
    raw = json.dumps({"events": [EVENT]} if obj is None else obj).encode()
    return verify_inventory_bytes(raw, expected_sha256=hashlib.sha256(raw).hexdigest(),
                                  source_id="authenticated-upstream", fetched_at=NOW, sport="NFL")

def test_valid_inventory_is_read_only_evidence():
    result = call()
    assert result.fixtures[0].official_event_id == "nfl-123"
    assert result.fixtures[0].start_utc.utcoffset().total_seconds() == 0
    assert not hasattr(result, "probability_publishable")
    assert not hasattr(result, "can_execute")

@pytest.mark.parametrize("obj,code", [
    ({"events": [{}]}, "SOURCE_CANONICAL_EVENT_ID_INVALID"),
    ({"events": [EVENT, EVENT]}, "SOURCE_CANONICAL_EVENT_ID_DUPLICATE"),
    ({"events": [{**EVENT, "sport": "NBA"}]}, "SOURCE_CROSS_SPORT_EVENT"),
    ({"events": [{**EVENT, "status": "UNKNOWN"}]}, "SOURCE_EVENT_STATUS_UNKNOWN"),
    ({"events": [{**EVENT, "start_utc": "2026-10-15T00:00:00"}]}, "SOURCE_EVENT_TIMEZONE_MISSING"),
    ({"events": [{**EVENT, "start_utc": "broken"}]}, "SOURCE_EVENT_TIMESTAMP_INVALID"),
    ({"games": []}, "SOURCE_EVENT_LIST_INVALID"),
    ({"events": ["bad"]}, "SOURCE_EVENT_ROW_INVALID"),
])
def test_fails_closed_on_invalid_canonical_row(obj, code):
    with pytest.raises(InventoryIntegrityError) as exc:
        call(obj)
    assert exc.value.code == code

def test_digest_mismatch_rejected():
    raw = json.dumps({"events": [EVENT]}).encode()
    with pytest.raises(InventoryIntegrityError, match="SOURCE_PAYLOAD_DIGEST_MISMATCH"):
        verify_inventory_bytes(raw, expected_sha256="0"*64, source_id="provider",
                               fetched_at=NOW, sport="NFL")

def test_payload_size_bounded():
    with pytest.raises(InventoryIntegrityError, match="SOURCE_PAYLOAD_SIZE_INVALID"):
        verify_inventory_bytes(b" "*2_000_001, expected_sha256="0"*64, source_id="provider",
                               fetched_at=NOW, sport="NFL")
