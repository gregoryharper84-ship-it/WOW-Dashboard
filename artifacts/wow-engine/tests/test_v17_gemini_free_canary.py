"""No-network tests for the isolated Free API public fixture canary."""
import io
import json
import pytest
from v17.gemini_free_canary import (
    probe,settings_status,FreeCanaryError,MODEL,FIXTURE
)

class Resp(io.BytesIO):
    def __enter__(self):return self
    def __exit__(self,*args):return False

def config(**extra):
    settings={"WOW_GEMINI_FREE_CANARY_ENABLED":"true",
              "WOW_GEMINI_FREE_PROJECT_VERIFIED":"true",
              "GEMINI_FREE_CANARY_API_KEY":"fake-test-key"}
    settings.update(extra)
    return settings

def mock_reply():
    return Resp(json.dumps({"candidates":[{"finishReason":"STOP","content":{
      "parts":[{"text":json.dumps({"status":"ok","echo":"WOW_FREE_CANARY"})}]}}]}).encode())

def test_default_is_disabled_and_no_network():
    calls=[]
    with pytest.raises(FreeCanaryError,match="GEMINI_FREE_CANARY_DISABLED"):
        probe(env={},opener=lambda *a,**k:calls.append(True))
    assert calls==[]
    assert settings_status({})["enabled"] is False

@pytest.mark.parametrize("bad,expected",[
    ({"WOW_GEMINI_FREE_PROJECT_VERIFIED":"false"},"GEMINI_FREE_PROJECT_NOT_VERIFIED"),
    ({"GEMINI_FREE_CANARY_API_KEY":""},"GEMINI_FREE_CANARY_KEY_MISSING"),
])
def test_missing_free_project_attestation_or_key_no_network(bad,expected):
    calls=[]
    with pytest.raises(FreeCanaryError,match=expected):
        probe(env=config(**bad),opener=lambda *a,**k:calls.append(True))
    assert calls==[]

def test_only_fixed_public_fixture_transmitted():
    requests=[]
    def opener(req,timeout):
        requests.append(req)
        return mock_reply()
    out=probe(env=config(),opener=opener)
    assert out["status"]=="PUBLIC_SYNTHETIC_PROBE_PASSED"
    assert out["sent_proprietary_data"] is False
    assert out["model"]==MODEL
    assert len(requests)==1
    request=requests[0]
    assert request.get_method()=="POST"
    assert request.full_url.endswith("/models/gemini-2.5-flash-lite:generateContent")
    payload=json.loads(request.data)
    assert payload["contents"][0]["parts"][0]["text"]==FIXTURE
    assert payload["generationConfig"]["maxOutputTokens"]==128

@pytest.mark.parametrize("payload",[
    b'not json',
    b'{"candidates":[]}',
    b'{"candidates":[{"finishReason":"MAX_TOKENS"}]}',
    b'{"candidates":[{"finishReason":"STOP","content":{"parts":[{"text":"{}"}]}}]}',
])
def test_invalid_completion_fails_closed(payload):
    with pytest.raises(FreeCanaryError,match="GEMINI_FREE_CANARY_RESPONSE_INVALID"):
        probe(env=config(),opener=lambda *a,**k:Resp(payload))

def test_status_does_not_expose_api_key():
    status=settings_status(config(GEMINI_FREE_CANARY_API_KEY="top-secret"))
    assert "top-secret" not in json.dumps(status)
