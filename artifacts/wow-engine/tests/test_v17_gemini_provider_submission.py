"""Guarded Gemini provider POST; fake transport and fake persistent gateway."""
import io
import json
import pytest
from v17.gemini_async_contracts import prepare_inline_batch,prepare_explicit_cache
from v17.gemini_budget_gateway import GeminiBudgetGateway
from v17.gemini_provider_submission import submit_prepared,GeminiSubmissionError


class Gateway(GeminiBudgetGateway):
    def __init__(self):self.events=[]
    def reserve(self,**args):
        self.events.append("RESERVED")
        return {"reservation_id":"b3505cb5-93a1-4da7-ad8e-121eb318d068"}
    def record(self,**args):
        self.events.append(args["stage"])
        return {"status":args["stage"]}

class Resp(io.BytesIO):
    def __enter__(self):return self
    def __exit__(self,*args):return False


def env(**kw):
    data=dict(WOW_GEMINI_ENABLED="true",WOW_GEMINI_EXTERNAL_MUTATIONS_ENABLED="true",
        WOW_GEMINI_PAID_API_PROJECT_VERIFIED="true",GEMINI_API_KEY="dummy-test-key")
    data.update(kw)
    return data


def plan():
    return prepare_inline_batch(model="gemini-test-model",urgency="BACKGROUND",
        data_classification="PUBLIC",items=[
            {"key":"code1","task":"code_review","prompt":"public test source only",
             "evidence_refs":["source:public"]}])


def call(plan_value=None,gw=None,**kw):
    return submit_prepared(plan=plan() if plan_value is None else plan_value,
        ledger=Gateway() if gw is None else gw,project_key="test_project",
        reserved_microusd=1200,approval_ref="review:123",
        tariff_sha256="a"*64,**kw)


def test_off_by_default_never_calls_network():
    events=[]
    with pytest.raises(GeminiSubmissionError,match="GEMINI_EXTERNAL_MUTATIONS_DISABLED"):
        call(env={},opener=lambda *a,**k: events.append(True))
    assert not events


def test_once_after_fence_then_receipt():
    gw=Gateway()
    seen=[]
    def opener(request,timeout):
        seen.append((request.get_method(),request.full_url,list(gw.events)))
        return Resp(b'{"name":"batches/123456"}')
    result=call(gw=gw,env=env(),opener=opener)
    assert result["status"]=="SUBMITTED_UNVERIFIED"
    assert gw.events==["RESERVED","OUTCOME_UNKNOWN","PROVIDER_CONFIRMED"]
    assert seen[0][2]==["RESERVED","OUTCOME_UNKNOWN"]
    assert seen[0][0]=="POST"
    assert seen[0][1].endswith("/v1beta/models/gemini-test-model:batchGenerateContent")


def test_timeout_keeps_unknown_and_does_not_retry():
    gw=Gateway()
    attempted=[]
    def opener(request,timeout):
        attempted.append(True)
        raise TimeoutError()
    with pytest.raises(GeminiSubmissionError,match="GEMINI_PROVIDER_OUTCOME_UNKNOWN"):
        call(gw=gw,env=env(),opener=opener)
    assert attempted==[True]
    assert gw.events==["RESERVED","OUTCOME_UNKNOWN"]


def test_tampered_manifest_never_reserves_or_posts():
    p=plan()
    p["api_payload"]["batch"]["display_name"]="modified"
    gw=Gateway()
    posted=[]
    with pytest.raises(GeminiSubmissionError,match="GEMINI_SUBMISSION_INVALID"):
        call(plan_value=p,gw=gw,env=env(),opener=lambda *a,**k:posted.append(True))
    assert gw.events==[] and posted==[]


def test_invalid_provider_resource_remains_unknown():
    gw=Gateway()
    with pytest.raises(GeminiSubmissionError,match="GEMINI_PROVIDER_OUTCOME_UNKNOWN"):
        call(gw=gw,env=env(),opener=lambda *a,**k:Resp(b'{"name":"https://evil.invalid/"}'))
    assert gw.events==["RESERVED","OUTCOME_UNKNOWN"]


def test_cache_creation_has_separate_exact_route():
    p=prepare_explicit_cache(model="gemini-test-model",content="Approved public excerpt",
        ttl_seconds=300,data_classification="PUBLIC",source_refs=["public:sha"])
    gw=Gateway()
    result=call(plan_value=p,gw=gw,env=env(),
                opener=lambda *a,**k:Resp(b'{"name":"cachedContents/abc123"}'))
    assert result["provider_name"]=="cachedContents/abc123"
    assert gw.events[-1]=="PROVIDER_CONFIRMED"


def test_disabled_paid_project_blocks_even_with_key():
    gw=Gateway()
    with pytest.raises(GeminiSubmissionError,match="GEMINI_BILLING_UNVERIFIED"):
        call(gw=gw,env=env(WOW_GEMINI_PAID_API_PROJECT_VERIFIED="false"))
    assert gw.events==[]


def test_tampered_cache_payload_cannot_spend():
    p=prepare_explicit_cache(model="gemini-test-model",content="Approved public excerpt",
        ttl_seconds=300,data_classification="PUBLIC",source_refs=["public:sha"])
    p["api_payload"]["contents"][0]["parts"][0]["text"]="changed"
    gw=Gateway()
    with pytest.raises(GeminiSubmissionError,match="GEMINI_SUBMISSION_INVALID"):
        call(plan_value=p,gw=gw,env=env())
    assert gw.events==[]


def test_tampered_batch_sensitive_text_does_not_egress():
    p=plan()
    p["api_payload"]["batch"]["input_config"]["requests"]["requests"][0]["request"]["contents"][0]["parts"][0]["text"]="DB_PASSWORD=secret-now"
    # Fake attacker re-hashes manifest; egress preflight still rejects.
    from v17.gemini_async_contracts import _digest
    p["manifest"]["payload_sha256"]=_digest(p["api_payload"])
    p["manifest_sha256"]=_digest(p["manifest"])
    gw=Gateway()
    with pytest.raises(GeminiSubmissionError,match="GEMINI_SENSITIVE_MATERIAL_REJECTED"):
        call(plan_value=p,gw=gw,env=env())
    assert gw.events==[]
