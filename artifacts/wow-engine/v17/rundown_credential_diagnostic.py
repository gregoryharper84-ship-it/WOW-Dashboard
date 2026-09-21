"""Non-secret runtime diagnostic for TheRundown credential configuration.

This module intentionally reports only whether a supported environment alias is
configured and which alias name won precedence. It never returns, hashes, logs,
or otherwise exposes the credential value itself.

The diagnostic must report the *active V2 runtime contract*, not the stale
pre-repair provider declaration. Applying the idempotent V2 auth repair before
inspection prevents import/test order from changing credential precedence.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from v17 import market_evidence_sources as sources

CAN_EXECUTE = False
LOGGER_NAME = "wow.v17.rundown.credential"


def _enabled(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() == "true"


def rundown_credential_status() -> dict[str, Any]:
    # The Sep-15 repair is the active Product V2 credential/header contract.
    # It is idempotent and mutates configuration only; it does not call the
    # provider or read/log any secret value.
    from v17.sep15_runtime_contract_repairs import install_rundown_v2_auth_repair

    install_rundown_v2_auth_repair()
    provider = sources.PROVIDERS["RUNDOWN"]
    selected_alias = None
    for alias in provider.key_envs:
        value = os.environ.get(alias)
        if value and value.strip():
            selected_alias = alias
            break

    return {
        "provider": "RUNDOWN",
        "configured": selected_alias is not None,
        "status": "CONFIGURED" if selected_alias else "UNCONFIGURED",
        "selected_alias": selected_alias,
        "aliases_checked": list(provider.key_envs),
        "auth_style": provider.auth_style,
        "auth_name": provider.auth_name,
        "market_evidence_enabled": _enabled("WOW_MARKET_EVIDENCE_ENABLED", "false"),
        "llp_rundown_bridge_enabled": _enabled("WOW_LLP_RUNDOWN_MARKET_ENABLED", "true"),
        "secret_value_exposed": False,
        "prediction_authority": False,
        "can_execute": CAN_EXECUTE,
    }


def log_rundown_credential_status(logger: logging.Logger | None = None) -> dict[str, Any]:
    status = rundown_credential_status()
    (logger or logging.getLogger(LOGGER_NAME)).info(
        "WOW_RUNDOWN_CREDENTIAL status=%s configured=%s selected_alias=%s aliases_checked=%s "
        "auth_style=%s auth_name=%s market_evidence_enabled=%s llp_rundown_bridge_enabled=%s "
        "secret_value_exposed=false can_execute=false",
        status["status"],
        str(status["configured"]).lower(),
        status["selected_alias"] or "NONE",
        ",".join(status["aliases_checked"]),
        status["auth_style"],
        status["auth_name"],
        str(status["market_evidence_enabled"]).lower(),
        str(status["llp_rundown_bridge_enabled"]).lower(),
    )
    return status


__all__ = ["CAN_EXECUTE", "log_rundown_credential_status", "rundown_credential_status"]
