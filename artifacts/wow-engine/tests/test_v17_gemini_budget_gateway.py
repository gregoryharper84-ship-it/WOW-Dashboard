"""No-network Gemini budget gateway readback and duplicate handling."""
from dataclasses import dataclass
from types import SimpleNamespace
import pytest
from v17.gemini_budget_gateway import GeminiBudgetGateway, BudgetGatewayError

RID="b3505cb5-93a1-4da7-ad8e-121eb318d068"
SHA="a"*64

@dataclass
class Query:
    client: object
    kind: str
    def select(self,*a):return self
    def eq(self,*a):return self
    def execute(self):
        self.client.events.append(self.kind)
        out=self.client.outputs.pop(0)
        if isinstance(out,Exception):raise out
        return SimpleNamespace(data=out)

class Client:
    def __init__(self,outputs):
        self.events=[]
        self.outputs=list(outputs)
    def rpc(self,name,payload):
        self.events.append(name)
        return Query(self,"rpc")
    def table(self,name):
        self.events.append(name)
        return Query(self,"select")


def arguments(**kw):
    values=dict(project_key="test_project",manifest_sha256=SHA,
        kind="BATCH_INLINE",budget_microusd=4200,
        approval_ref="approval:123",tariff_sha256="b"*64)
    values.update(kw)
    return values


def test_reservation_requires_verified_readback():
    c=Client([{"status":"RESERVED","reservation_id":RID,"can_execute":False},
      [{"reservation_id":RID,"project_key":"test_project","manifest_sha256":SHA,
        "kind":"BATCH_INLINE","reserved_microusd":4200,"can_execute":False}]])
    out=GeminiBudgetGateway(c).reserve(**arguments())
    assert out["status"]=="RESERVED"
    assert c.events==["wow_gemini_reserve_spend","rpc",
                      "wow_gemini_spend_reservations","select"]


def test_readback_missing_fails_closed():
    c=Client([{"status":"RESERVED","reservation_id":RID,"can_execute":False},[]])
    with pytest.raises(BudgetGatewayError,match="GEMINI_BUDGET_READBACK_MISMATCH"):
        GeminiBudgetGateway(c).reserve(**arguments())


def test_db_duplicate_is_typed_failure():
    c=Client([RuntimeError("unique violation and secret")])
    with pytest.raises(BudgetGatewayError,match="GEMINI_BUDGET_RPC_UNAVAILABLE") as exc:
        GeminiBudgetGateway(c).reserve(**arguments())
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize("bad",[
  {"budget_microusd":0},{"budget_microusd":True},
  {"kind":"MLB_PROBABILITY"},{"manifest_sha256":"bad"},
  {"project_key":"foo/bar"},{"tariff_sha256":"bad"}])
def test_invalid_reservation_never_calls_db(bad):
    c=Client([])
    with pytest.raises(BudgetGatewayError,match="GEMINI_BUDGET_INPUT_INVALID"):
        GeminiBudgetGateway(c).reserve(**arguments(**bad))
    assert c.events==[]


def test_stage_receipt_must_read_back_same_provider_name():
    c=Client([{"status":"PROVIDER_CONFIRMED","can_execute":False},
      [{"reservation_id":RID,"stage":"PROVIDER_CONFIRMED",
        "provider_name":"batches/abc","can_execute":False}]])
    x=GeminiBudgetGateway(c).record(reservation_id=RID,
      stage="PROVIDER_CONFIRMED",provider_name="batches/abc")
    assert x["stage"]=="PROVIDER_CONFIRMED"


def test_mismatched_stage_is_hold():
    c=Client([{"status":"RECONCILED","can_execute":False},[]])
    with pytest.raises(BudgetGatewayError,match="GEMINI_BUDGET_READBACK_MISMATCH"):
        GeminiBudgetGateway(c).record(reservation_id=RID,stage="RECONCILED")
