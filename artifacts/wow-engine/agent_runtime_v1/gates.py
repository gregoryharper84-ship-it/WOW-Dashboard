from __future__ import annotations
from datetime import datetime
from math import isfinite
from typing import Mapping

def mix_failure_regimes(components:list[tuple[float,Mapping[int,float]]])->dict[int,float]:
    if not components: raise ValueError("FAILURE_PATH_MIXTURE_EMPTY")
    if abs(sum(float(w) for w,_ in components)-1.0)>1e-9: raise ValueError("FAILURE_PATH_WEIGHTS_NOT_NORMALIZED")
    out={}
    for weight,pmf in components:
        if weight<0: raise ValueError("FAILURE_PATH_WEIGHT_INVALID")
        if abs(sum(float(p) for p in pmf.values())-1.0)>1e-9: raise ValueError("FAILURE_PATH_COMPONENT_NOT_NORMALIZED")
        for outcome,p in pmf.items(): out[int(outcome)]=out.get(int(outcome),0.0)+float(weight)*float(p)
    return dict(sorted(out.items()))

def derive_line_probabilities(pmf:Mapping[int,float],line:float)->dict[str,float]:
    more=sum(p for x,p in pmf.items() if x>line); less=sum(p for x,p in pmf.items() if x<line); push=sum(p for x,p in pmf.items() if x==line)
    if abs(more+less+push-1.0)>1e-9: raise ValueError("PROP_LINE_DERIVATION_NOT_NORMALIZED")
    return {"MORE":more,"LESS":less,"PUSH":push}

def validate_calibrated(point:float|None,lower:float|None,upper:float|None,calibrator_id:str|None)->None:
    if not calibrator_id: raise ValueError("CALIBRATOR_UNAVAILABLE")
    vals=(point,lower,upper)
    if any(v is None or not isfinite(float(v)) for v in vals): raise ValueError("CALIBRATED_BOUNDS_INVALID")
    if not (0<float(lower)<=float(point)<=float(upper)<1): raise ValueError("CALIBRATED_BOUNDS_INVALID")

def final_refresh(*,now:datetime,event_start:datetime,event_status:str,market_fresh:bool,critical_status_fresh:bool)->tuple[str,list[str]]:
    blockers=[]
    if event_start<=now or event_status.upper() in {"STARTED","FINAL","CANCELED","POSTPONED"}: blockers.append("EVENT_NOT_PREGAME")
    if not market_fresh:blockers.append("MARKET_STALE_AT_FINAL_REFRESH")
    if not critical_status_fresh:blockers.append("CRITICAL_STATUS_STALE_AT_FINAL_REFRESH")
    return ("PASS",[]) if not blockers else ("REJECT",blockers)
