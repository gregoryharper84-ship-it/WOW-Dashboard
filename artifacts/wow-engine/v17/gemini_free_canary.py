"""Isolated synthetic-only Gemini Free API canary (never WOW engineering input).

Unpaid Gemini API content may be used to improve Google products.
For that reason the only request body is a fixed, reviewed, non-confidential
fixture. This does NOT bypass or activate WOW's paid-only engineering lanes.
No async, cache, batch, files, code context, user prompt, retries, or execution.
"""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CAN_EXECUTE = False
AUTHORITY = "PUBLIC_SYNTHETIC_CONNECTIVITY_ONLY"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent"
MODEL = "gemini-2.5-flash-lite"
FIXTURE = "This is a public synthetic connectivity test. Reply only with a JSON object containing status='ok' and echo='WOW_FREE_CANARY'."
MAX_RESPONSE = 4096

class FreeCanaryError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def settings_status(env=None) -> dict[str, object]:
    e = os.environ if env is None else env
    return {
        "provider": "gemini",
        "mode": "FREE_PUBLIC_CANARY_ONLY",
        "enabled": e.get("WOW_GEMINI_FREE_CANARY_ENABLED") == "true",
        "operator_attests_free_project": e.get("WOW_GEMINI_FREE_PROJECT_VERIFIED") == "true",
        "key_configured": bool(e.get("GEMINI_FREE_CANARY_API_KEY")),
        "model": MODEL,
        "can_execute": False,
        "authority": AUTHORITY,
    }


def probe(*, env=None, opener=None) -> dict[str, object]:
    """One manual request to an allowlisted endpoint; no arbitrary caller data."""
    e = os.environ if env is None else env
    if e.get("WOW_GEMINI_FREE_CANARY_ENABLED") != "true":
        raise FreeCanaryError("GEMINI_FREE_CANARY_DISABLED")
    if e.get("WOW_GEMINI_FREE_PROJECT_VERIFIED") != "true":
        raise FreeCanaryError("GEMINI_FREE_PROJECT_NOT_VERIFIED")
    key=e.get("GEMINI_FREE_CANARY_API_KEY")
    if not key:
        raise FreeCanaryError("GEMINI_FREE_CANARY_KEY_MISSING")
    body=json.dumps({
        "contents":[{"role":"user","parts":[{"text":FIXTURE}]}],
        "generationConfig":{"responseMimeType":"application/json","maxOutputTokens":128,
                            "temperature":0},
    },separators=(",",":")).encode("utf-8")
    req=Request(ENDPOINT, method="POST",data=body,
                headers={"x-goog-api-key":key, "Content-Type":"application/json",
                         "Accept":"application/json"})
    caller=urlopen if opener is None else opener
    try:
        with caller(req,timeout=10) as response:
            raw=response.read(MAX_RESPONSE+1)
    except HTTPError as exc:
        if exc.code==429:
            raise FreeCanaryError("GEMINI_FREE_CANARY_QUOTA") from None
        raise FreeCanaryError("GEMINI_FREE_CANARY_TRANSPORT") from None
    except (URLError,TimeoutError,OSError):
        raise FreeCanaryError("GEMINI_FREE_CANARY_TRANSPORT") from None
    if len(raw)>MAX_RESPONSE:
        raise FreeCanaryError("GEMINI_FREE_CANARY_RESPONSE_INVALID")
    try:
        data=json.loads(raw)
        first=data["candidates"][0]
        if first.get("finishReason")!="STOP":
            raise ValueError("unfinished")
        output=json.loads("".join(x["text"] for x in first["content"]["parts"]
                                  if isinstance(x.get("text"),str)))
        if output.get("status")!="ok" or output.get("echo")!="WOW_FREE_CANARY":
            raise ValueError("unexpected content")
    except (ValueError,TypeError,KeyError,IndexError,AttributeError):
        raise FreeCanaryError("GEMINI_FREE_CANARY_RESPONSE_INVALID") from None
    return {"provider":"gemini","mode":"FREE_PUBLIC_CANARY_ONLY",
            "status":"PUBLIC_SYNTHETIC_PROBE_PASSED","model":MODEL,
            "data_classification":"PUBLIC_SYNTHETIC","sent_proprietary_data":False,
            "can_execute":False,"authority":AUTHORITY}


def main(argv=None) -> int:
    import argparse
    p=argparse.ArgumentParser(description="Isolated synthetic-only Gemini free canary")
    p.add_argument("--status",action="store_true")
    p.add_argument("--probe",action="store_true")
    args=p.parse_args(argv)
    if args.status or not args.probe:
        print(json.dumps(settings_status(),sort_keys=True))
        return 0
    try:
        result=probe()
    except FreeCanaryError as exc:
        print(json.dumps({"status":exc.code,"can_execute":False}))
        return 1
    print(json.dumps(result,sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
