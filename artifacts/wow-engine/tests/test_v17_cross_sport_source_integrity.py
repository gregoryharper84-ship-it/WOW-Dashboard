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


@pytest.mark.parametrize("obj,code", [
    ({"events": [{**EVENT, "official_event_id": " nfl-123"}]}, "SOURCE_CANONICAL_EVENT_ID_INVALID"),
    ({"events": [{**EVENT, "official_event_id": "nfl-123\n"}]}, "SOURCE_CANONICAL_EVENT_ID_INVALID"),
    ({"events": [{**EVENT, "official_event_id": "a" * 181}]}, "SOURCE_CANONICAL_EVENT_ID_INVALID"),
    ({"events": [{**EVENT, "start_utc": 1759900000}]}, "SOURCE_EVENT_TIMESTAMP_INVALID"),
    ({"events": [{**EVENT, "official_event_id": 7}]}, "SOURCE_CANONICAL_EVENT_ID_INVALID"),
])
def test_reject_untrusted_event_identity_variants(obj, code):
    with pytest.raises(InventoryIntegrityError, match=code):
        call(obj)


def test_empty_inventory_and_final_event_are_valid_observations_not_certification():
    assert call({"events": []}).fixtures == ()
    assert call({"events": [{**EVENT, "status": "FINAL"}]}).fixtures[0].status == "FINAL"


def test_timestamp_with_timezone_offset_is_normalized_to_utc():
    result = call({"events": [{**EVENT, "start_utc": "2026-10-14T19:00:00-05:00"}]})
    assert result.fixtures[0].start_utc.isoformat() == "2026-10-15T00:00:00+00:00"


@pytest.mark.parametrize("kwargs,code", [
    ({"expected_sha256": "INVALID"}, "SOURCE_EXPECTED_DIGEST_INVALID"),
    ({"source_id": "   "}, "SOURCE_IDENTITY_MISSING"),
    ({"fetched_at": datetime(2026, 10, 12)}, "SOURCE_FETCH_TIMEZONE_MISSING"),
    ({"sport": "nfl"}, "SOURCE_SPORT_INVALID"),
    ({"sport": None}, "SOURCE_SPORT_INVALID"),
])
def test_fail_closed_invalid_verified_source_context(kwargs, code):
    raw = json.dumps({"events": [EVENT]}).encode()
    valid = dict(expected_sha256=hashlib.sha256(raw).hexdigest(), source_id="upstream",
                 fetched_at=NOW, sport="NFL")
    valid.update(kwargs)
    with pytest.raises(InventoryIntegrityError, match=code):
        verify_inventory_bytes(raw, **valid)


@pytest.mark.parametrize("raw,code", [
    (b"", "SOURCE_PAYLOAD_SIZE_INVALID"),
    (b"{", "SOURCE_PAYLOAD_JSON_INVALID"),
    (b"\xff", "SOURCE_PAYLOAD_JSON_INVALID"),
    (b"[]", "SOURCE_EVENT_LIST_INVALID"),
])
def test_fail_closed_invalid_payload(raw, code):
    with pytest.raises(InventoryIntegrityError, match=code):
        verify_inventory_bytes(raw, expected_sha256=hashlib.sha256(raw).hexdigest(),
                               source_id="upstream", fetched_at=NOW, sport="NFL")


def test_fail_closed_event_count_limit():
    with pytest.raises(InventoryIntegrityError, match="SOURCE_EVENT_COUNT_LIMIT"):
        call({"events": [EVENT] * 2001})


@pytest.mark.parametrize("raw,code", [
    (b'{"events":[],"events":[]}', "SOURCE_PAYLOAD_JSON_DUPLICATE_KEY"),
    (b'{"events":[{"sport":"NFL","sport":"NBA"}]}', "SOURCE_PAYLOAD_JSON_DUPLICATE_KEY"),
    (b'{"events":[],"metadata":{"x":1,"x":2}}', "SOURCE_PAYLOAD_JSON_DUPLICATE_KEY"),
    (b'{"events":[],"discarded":NaN}', "SOURCE_PAYLOAD_JSON_NONFINITE_NUMBER"),
    (b'{"events":[],"discarded":Infinity}', "SOURCE_PAYLOAD_JSON_NONFINITE_NUMBER"),
    (b'{"events":[],"discarded":-Infinity}', "SOURCE_PAYLOAD_JSON_NONFINITE_NUMBER"),
    ('{"events":[]}'.encode("utf-16"), "SOURCE_PAYLOAD_JSON_INVALID"),
    (b'\xef\xbb\xbf{"events":[]}', "SOURCE_PAYLOAD_JSON_INVALID"),
    (b'{"events":[]} trailing', "SOURCE_PAYLOAD_JSON_INVALID"),
])
def test_strict_json_rejects_ambiguous_bytes(raw, code):
    with pytest.raises(InventoryIntegrityError) as exc:
        verify_inventory_bytes(
            raw, expected_sha256=hashlib.sha256(raw).hexdigest(),
            source_id="authenticated-upstream", fetched_at=NOW, sport="NFL",
        )
    assert exc.value.code == code
