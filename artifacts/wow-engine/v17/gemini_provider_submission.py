"""Opt-in Gemini Batch/cache creation through durable spend reservations.

Never a wager/order execution capability. External provider mutations are
hard-disabled until multiple explicit operator gates are set. Each manifest
is reserved once; an OUTCOME_UNKNOWN fence is recorded before outbound POST.
Any ambiguous result remains UNKNOWN and must be reconciled, never resubmitted.
"""
from __future__ import annotations
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from v17.gemini_async_contracts import (
    BATCH_NAME_RE, CACHE_NAME_RE, GeminiContractError, _digest,
)
from v17.gemini_budget_gateway import GeminiBudgetGateway, BudgetGatewayError

CAN_EXECUTE = False
ROOT = "https://generativelanguage.googleapis.com"
MAX_REPLY = 64_000


class GeminiSubmissionError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def submit_prepared(*, plan: dict, ledger: GeminiBudgetGateway, project_key: str,
                    reserved_microusd: int, approval_ref: str, tariff_sha256: str,
                    env=None, opener=None) -> dict:
    """Send exactly once, after central spend reservation and UNKNOWN fence.

    Requires trusted SIRT DLP/authorization upstream; heuristic prompt checks
    alone do not establish data-sharing authorization.
    """
    settings = os.environ if env is None else env
    if (settings.get("WOW_GEMINI_ENABLED") != "true" or
        settings.get("WOW_GEMINI_EXTERNAL_MUTATIONS_ENABLED") != "true"):
        raise GeminiSubmissionError("GEMINI_EXTERNAL_MUTATIONS_DISABLED")
    if settings.get("WOW_GEMINI_PAID_API_PROJECT_VERIFIED") != "true":
        raise GeminiSubmissionError("GEMINI_BILLING_UNVERIFIED")
    if (not isinstance(plan,dict) or plan.get("can_execute") is not False
        or plan.get("status") not in ("PREPARED_NOT_SUBMITTED","PREPARED_NOT_CREATED")):
        raise GeminiSubmissionError("GEMINI_SUBMISSION_INVALID")
    if not isinstance(ledger,GeminiBudgetGateway):
        raise GeminiSubmissionError("GEMINI_DURABLE_LEDGER_REQUIRED")
    kind = plan.get("kind")
    if kind == "BATCH_INLINE_PREPARED":
        route = plan.get("api_route")
        model = plan.get("manifest",{}).get("model")
        if (not isinstance(model,str) or not route or
            route != "/v1beta/models/"+model+":batchGenerateContent"):
            raise GeminiSubmissionError("GEMINI_SUBMISSION_INVALID")
        if plan.get("manifest",{}).get("urgency") != "BACKGROUND":
            raise GeminiSubmissionError("GEMINI_BATCH_LATENCY_INCOMPATIBLE")
        if _digest(plan.get("api_payload")) != plan.get("manifest",{}).get("payload_sha256"):
            raise GeminiSubmissionError("GEMINI_SUBMISSION_INVALID")
        if _digest(plan["manifest"]) != plan.get("manifest_sha256"):
            raise GeminiSubmissionError("GEMINI_SUBMISSION_INVALID")
        pattern = BATCH_NAME_RE
        budget_kind = "BATCH_INLINE"
        token_guard = "batch"
    elif kind == "CACHE_CREATE_PREPARED":
        route = plan.get("api_route")
        if route != "/v1beta/cachedContents" or not isinstance(plan.get("api_payload"),dict):
            raise GeminiSubmissionError("GEMINI_SUBMISSION_INVALID")
        source_refs = plan.get("source_refs")
        if not isinstance(source_refs,list) or not isinstance(plan.get("manifest_sha256"),str):
            raise GeminiSubmissionError("GEMINI_SUBMISSION_INVALID")
        # Cache's reviewed model/context/TTL are still subject to an independent
        # upstream allowlist and provider capability gate.
        pattern = CACHE_NAME_RE
        budget_kind = "CACHE_CREATE"
        token_guard = "cache"
    else:
        raise GeminiSubmissionError("GEMINI_SUBMISSION_INVALID")
    key = settings.get("GEMINI_API_KEY")
    if not key:
        raise GeminiSubmissionError("GEMINI_CONFIG_MISSING")
    if type(reserved_microusd) is not int or reserved_microusd <= 0:
        raise GeminiSubmissionError("GEMINI_SUBMISSION_INVALID")

    # Fully guard route names; no host input or paths from user text.
    if token_guard == "batch" and (
        "/" in model or ".." in model or any(c in model for c in "?&#")):
        raise GeminiSubmissionError("GEMINI_SUBMISSION_INVALID")

    try:
        reserved = ledger.reserve(
            project_key=project_key, manifest_sha256=plan["manifest_sha256"],
            kind=budget_kind, budget_microusd=reserved_microusd,
            approval_ref=approval_ref, tariff_sha256=tariff_sha256,
        )
        receipt = reserved["reservation_id"]
        # Fence first: crash/timeout can never cause a second attempt.
        ledger.record(reservation_id=receipt, stage="OUTCOME_UNKNOWN")
    except (BudgetGatewayError,KeyError):
        raise GeminiSubmissionError("GEMINI_SUBMISSION_RESERVATION_FAILED") from None

    request = Request(ROOT+route,method="POST",
        data=json.dumps(plan["api_payload"],sort_keys=True).encode("utf-8"),
        headers={"x-goog-api-key":key,"Content-Type":"application/json",
                 "Accept":"application/json"})
    transport = urlopen if opener is None else opener
    try:
        with transport(request,timeout=20) as response:
            raw = response.read(MAX_REPLY+1)
        if len(raw)>MAX_REPLY:
            raise ValueError("response too large")
        reply=json.loads(raw)
        name=reply.get("name")
        if not isinstance(name,str) or not pattern.fullmatch(name):
            raise ValueError("invalid resource")
    except (HTTPError,URLError,TimeoutError,OSError,ValueError,TypeError,AttributeError):
        # Do not release budget or retry. An HTTP error may still have an
        # uncertain provider-side outcome.
        raise GeminiSubmissionError("GEMINI_PROVIDER_OUTCOME_UNKNOWN") from None
    try:
        ledger.record(reservation_id=receipt, stage="PROVIDER_CONFIRMED",
                      provider_name=name)
    except BudgetGatewayError:
        # Provider may have accepted; only durable lookup can resolve.
        raise GeminiSubmissionError("GEMINI_PROVIDER_CONFIRMED_RECEIPT_MISSING") from None
    return {"status":"SUBMITTED_UNVERIFIED","provider_name":name,
            "reservation_id":receipt, "manifest_sha256":plan["manifest_sha256"],
            "can_execute":False,"terminal_authority":"V17_TERMINAL_REDUCER"}
