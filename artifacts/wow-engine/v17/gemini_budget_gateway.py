"""Gemini spending gateway over an existing server-side Supabase client.

Supabase access is injected by the governed worker. This module does not create
credentials or clients and never performs direct table writes. The SQL RPC is
the atomic budget authority; readback must exactly match the reservation.
"""
from __future__ import annotations
import re
import uuid

CAN_EXECUTE = False
SHA = re.compile(r"^[0-9a-f]{64}$")
PROJECT = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
KINDS = frozenset({"BATCH_INLINE", "CACHE_CREATE", "STANDARD"})


class BudgetGatewayError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _data(call):
    try:
        return call.execute().data
    except Exception:
        raise BudgetGatewayError("GEMINI_BUDGET_RPC_UNAVAILABLE") from None


class GeminiBudgetGateway:
    """Requires a privileged, server-side client with only approved access."""

    def __init__(self, client):
        self._client = client

    def reserve(self, *, project_key, manifest_sha256, kind, budget_microusd,
                approval_ref, tariff_sha256):
        if (not isinstance(project_key, str) or not PROJECT.fullmatch(project_key)
            or not isinstance(manifest_sha256, str) or not SHA.fullmatch(manifest_sha256)
            or kind not in KINDS or type(budget_microusd) is not int
            or budget_microusd <= 0 or not isinstance(approval_ref, str)
            or not 3 <= len(approval_ref) <= 160
            or not isinstance(tariff_sha256, str) or not SHA.fullmatch(tariff_sha256)):
            raise BudgetGatewayError("GEMINI_BUDGET_INPUT_INVALID")
        row = _data(self._client.rpc("wow_gemini_reserve_spend", {
            "p_project_key": project_key, "p_manifest_sha256": manifest_sha256,
            "p_kind": kind, "p_reserved_microusd": budget_microusd,
            "p_approval_ref": approval_ref, "p_tariff_sha256": tariff_sha256,
        }))
        if not isinstance(row, dict) or row.get("status") != "RESERVED" or row.get("can_execute") is not False:
            raise BudgetGatewayError("GEMINI_BUDGET_RPC_INVALID")
        try:
            receipt = str(uuid.UUID(str(row["reservation_id"])))
        except (ValueError, TypeError, KeyError):
            raise BudgetGatewayError("GEMINI_BUDGET_RPC_INVALID") from None
        records = _data(self._client.table("wow_gemini_spend_reservations")
            .select("reservation_id,project_key,manifest_sha256,kind,reserved_microusd,can_execute")
            .eq("reservation_id", receipt))
        if (not isinstance(records, list) or len(records) != 1
            or not isinstance(records[0], dict)
            or records[0].get("reservation_id") != receipt
            or records[0].get("project_key") != project_key
            or records[0].get("manifest_sha256") != manifest_sha256
            or records[0].get("kind") != kind
            or records[0].get("reserved_microusd") != budget_microusd
            or records[0].get("can_execute") is not False):
            raise BudgetGatewayError("GEMINI_BUDGET_READBACK_MISMATCH")
        return {"reservation_id": receipt, "status": "RESERVED",
                "manifest_sha256": manifest_sha256, "can_execute": False}

    def record(self, *, reservation_id, stage, provider_name=None):
        try:
            receipt = str(uuid.UUID(str(reservation_id)))
        except (ValueError, TypeError, AttributeError):
            raise BudgetGatewayError("GEMINI_BUDGET_INPUT_INVALID") from None
        if stage not in {"PROVIDER_CONFIRMED", "OUTCOME_UNKNOWN", "RECONCILED", "TERMINAL_HOLD"}:
            raise BudgetGatewayError("GEMINI_BUDGET_INPUT_INVALID")
        if stage == "PROVIDER_CONFIRMED" and not provider_name:
            raise BudgetGatewayError("GEMINI_BUDGET_INPUT_INVALID")
        row = _data(self._client.rpc("wow_gemini_record_spend_stage", {
            "p_reservation_id": receipt, "p_stage": stage,
            "p_provider_name": provider_name,
        }))
        if not isinstance(row, dict) or row.get("status") != stage or row.get("can_execute") is not False:
            raise BudgetGatewayError("GEMINI_BUDGET_RPC_INVALID")
        entries = _data(self._client.table("wow_gemini_spend_events")
            .select("reservation_id,stage,provider_name,can_execute")
            .eq("reservation_id", receipt).eq("stage", stage))
        if (not isinstance(entries, list) or len(entries) != 1
            or entries[0].get("reservation_id") != receipt
            or entries[0].get("stage") != stage
            or entries[0].get("can_execute") is not False
            or (provider_name is not None and entries[0].get("provider_name") != provider_name)):
            raise BudgetGatewayError("GEMINI_BUDGET_READBACK_MISMATCH")
        return {"reservation_id": receipt, "stage": stage, "can_execute": False}
