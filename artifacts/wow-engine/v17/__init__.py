"""WOW V17 active-generation modules.

Runtime activation remains controlled by ``WOW_V17_ACTIVE=1`` at the accepted
production entrypoint. When the V17 package is composed after the governed prop
modules are loaded, install the V17 probability/qualification response contract
idempotently. The adapter cannot authorize execution.
"""

from v17.prop_response_semantics import install_prop_response_semantics

install_prop_response_semantics()
