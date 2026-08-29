from __future__ import annotations
from dataclasses import dataclass
from .contracts import JobStatus, TERMINAL_JOB_STATES

CEILING_ORDER=["FINAL_APPROVED","FINAL_REFRESH_HOLD","STRUCTURE_VERIFIED_HOLD","MARKET_VERIFIED_HOLD","MODEL_QUALIFIED_HOLD","EVIDENCE_VERIFIED","IDENTITY_VERIFIED","RESEARCH_INTEREST"]
RANK={name:i for i,name in enumerate(CEILING_ORDER)}

@dataclass(frozen=True)
class Decision:
    terminal_label:str; final_terminal_ceiling:str; blockers:tuple[str,...]; probability_publishable:bool; can_execute:bool=False

def strictest(ceilings:list[str])->str:
    try:return max(ceilings,key=lambda x:RANK[x])
    except KeyError as exc: raise ValueError("GOVERNANCE_LABEL_UNKNOWN") from exc

def reduce_candidate(*,controlling_worker_id:str|None,required_jobs:list[dict])->Decision:
    if any(JobStatus(j["status"]) not in TERMINAL_JOB_STATES for j in required_jobs):
        raise RuntimeError("RUN_NOT_TERMINAL")
    if not controlling_worker_id:
        return Decision("NO_SPECIALIST_COVERAGE","RESEARCH_INTEREST",("NO_SPECIALIST_COVERAGE",),False)
    controlling=[j for j in required_jobs if j.get("worker_id")==controlling_worker_id]
    if len(controlling)!=1 or controlling[0]["status"]!="SUCCEEDED":
        blockers=tuple(sorted(set(sum((list(j.get("blockers") or []) for j in required_jobs),[])+["MODEL_UNAVAILABLE"])))
        return Decision("MODEL_UNAVAILABLE","RESEARCH_INTEREST",blockers,False)
    blockers=tuple(sorted(set(sum((list(j.get("blockers") or []) for j in required_jobs),[]))))
    ceiling=strictest([j.get("ceiling","RESEARCH_INTEREST") for j in required_jobs])
    publishable=not blockers and RANK[ceiling] <= RANK["MODEL_QUALIFIED_HOLD"]
    return Decision(ceiling,ceiling,blockers,publishable)

def reconcile(rows_in:int,rows_completed:int,rows_held:int,rows_rejected:int)->dict:
    balanced=rows_in==rows_completed+rows_held+rows_rejected
    if not balanced: raise ValueError("ROW_RECONCILIATION_MISMATCH")
    return {"rows_in":rows_in,"rows_completed":rows_completed,"rows_held":rows_held,"rows_rejected":rows_rejected,"balanced":True,"can_execute":False}
