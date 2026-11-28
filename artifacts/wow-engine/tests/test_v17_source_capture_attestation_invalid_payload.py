from datetime import datetime, timedelta, timezone
from hashlib import sha256
import pytest
from v17.source_capture_attestation import attest_bound_source_features, feature_payload_sha256, SourceCaptureAttestationError

NOW = datetime(2026, 11, 24, 18, tzinfo=timezone.utc)
RAW = 'a' * 64

class NoWrite:
    calls = 0
    def table(self, name):
        self.calls += 1
        raise AssertionError('invalid package must never contact database')

def package():
    return {'sport': 'NBA', 'official_event_id': 'test-only',
            'event_start_time_utc': (NOW + timedelta(hours=1)).isoformat(),
            'source_snapshot_id': 'sha256:' + RAW,
            'features': {'elo_diff': 1.5},
            'feature_audit': {'source_replay_status': 'RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS',
                              'source_raw_sha256': RAW, 'timestamp_attestation': 'VERIFIED',
                              'timestamp_attestation_id': 'stale', 'timestamp_attestation_policy': 'stale'},
            'probability_publishable': True, 'rank_eligible': True, 'can_execute': True}

@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf'), object()])
def test_invalid_feature_is_typed_and_never_written(value):
    db = NoWrite()
    p = package()
    p['features']['elo_diff'] = value
    out = attest_bound_source_features(db, p, now=NOW)
    assert out['feature_audit']['timestamp_attestation'] == 'NOT_ESTABLISHED'
    assert out['feature_audit']['timestamp_attestation_failure'] == 'SOURCE_CAPTURE_PAYLOAD_INVALID'
    assert 'timestamp_attestation_id' not in out['feature_audit']
    assert 'timestamp_attestation_policy' not in out['feature_audit']
    assert all(out[k] is False for k in ('probability_publishable','rank_eligible','can_execute'))
    assert db.calls == 0

@pytest.mark.parametrize('audit', ['invalid', 42, ['bad']])
def test_invalid_audit_is_typed_and_never_written(audit):
    db = NoWrite()
    p = package()
    p['feature_audit'] = audit
    out = attest_bound_source_features(db, p, now=NOW)
    assert out['feature_audit']['timestamp_attestation'] == 'NOT_ESTABLISHED'
    assert out['feature_audit']['timestamp_attestation_failure'] == 'SOURCE_CAPTURE_PAYLOAD_INVALID'
    assert all(out[k] is False for k in ('probability_publishable','rank_eligible','can_execute'))
    assert db.calls == 0

def test_ordinary_hash_unchanged():
    assert feature_payload_sha256({'z': 2, 'a': 1.5}) == sha256(b'{"a":1.5,"z":2}').hexdigest()

def test_existing_failure_also_clears_authority_and_stale_receipt():
    db = NoWrite()
    p = package()
    p['feature_audit']['source_replay_status'] = 'NOT_VERIFIED'
    out = attest_bound_source_features(db, p, now=NOW)
    assert out['feature_audit']['timestamp_attestation_failure'] == 'SOURCE_CAPTURE_REPLAY_NOT_VERIFIED'
    assert 'timestamp_attestation_id' not in out['feature_audit']
    assert all(out[k] is False for k in ('probability_publishable','rank_eligible','can_execute'))
    assert db.calls == 0
