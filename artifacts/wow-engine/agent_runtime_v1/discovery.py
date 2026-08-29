from __future__ import annotations
from urllib.parse import urlparse

def source_family(source:dict)->str:
    family=str(source.get("source_family") or "").strip().lower()
    if family:return family
    url=str(source.get("url") or "").strip()
    if url:
        host=urlparse(url).hostname or ""
        return host.lower().removeprefix("www.")
    return str(source.get("provider") or source.get("name") or "unknown").strip().lower()

def canonical_candidate_key(row:dict)->str:
    parts=[str(row.get("sport","")).upper(),str(row.get("official_event_id","")).strip(),str(row.get("participant","")).strip().lower(),str(row.get("market_family","")).upper(),str(row.get("stat_family") or "").upper(),str(row.get("period","FULL_GAME")).upper(),str(row.get("exact_line") if row.get("exact_line") is not None else ""),str(row.get("side") or "").upper()]
    return "|".join(parts)

def merge_discovery(rows:list[dict])->list[dict]:
    merged={}
    for row in rows:
        key=canonical_candidate_key(row)
        if not row.get("sport") or not row.get("participant") or not row.get("market_family"):
            continue
        cur=merged.setdefault(key,{**row,"canonical_key":key,"sources":[],"source_families":[]})
        seen=set(cur["source_families"])
        for src in row.get("sources") or []:
            fam=source_family(src)
            if fam in seen:continue
            seen.add(fam); cur["sources"].append(src); cur["source_families"].append(fam)
    return list(merged.values())
