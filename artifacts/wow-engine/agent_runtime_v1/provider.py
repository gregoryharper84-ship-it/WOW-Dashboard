from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256

class ModelUnavailable(RuntimeError):
    code="MODEL_UNAVAILABLE"

@dataclass(frozen=True)
class Capability:
    provider_id:str; model_family:str; artifact_id:str|None; calibrator_id:str|None; status:str

def route_capability(records:list[dict],*,sport:str,market_family:str,stat_family:str|None,period:str)->Capability:
    matches=[r for r in records if str(r.get("sport","")).upper()==sport.upper() and str(r.get("market_family","")).upper()==market_family.upper() and (r.get("stat_family") or None)==(stat_family or None) and str(r.get("period","")).upper()==period.upper() and r.get("status")=="AVAILABLE"]
    if not matches: raise ModelUnavailable("MODEL_UNAVAILABLE")
    if len(matches)!=1: raise RuntimeError("SPECIALIST_ROUTING_CONFLICT")
    r=matches[0]
    if not r.get("artifact_id") or not r.get("calibrator_id"): raise ModelUnavailable("MODEL_UNAVAILABLE")
    return Capability(r["provider_id"],r["model_family"],str(r["artifact_id"]),str(r["calibrator_id"]),r["status"])

def verify_artifact_bytes(data:bytes,expected_sha256:str)->None:
    actual=sha256(data).hexdigest()
    if actual.lower()!=expected_sha256.lower(): raise RuntimeError("ARTIFACT_HASH_MISMATCH")

def validate_pmf(support:dict[int,float],tol:float=1e-9)->dict[int,float]:
    if not support: raise ValueError("PROP_PMF_EMPTY")
    cleaned={}
    for k,v in support.items():
        if isinstance(k,bool) or int(k)<0: raise ValueError("PROP_PMF_SUPPORT_INVALID")
        p=float(v)
        if p<0 or p>1: raise ValueError("PROP_PMF_PROBABILITY_INVALID")
        cleaned[int(k)]=p
    total=sum(cleaned.values())
    if abs(total-1.0)>tol: raise ValueError("PROP_PMF_NOT_NORMALIZED")
    return cleaned
