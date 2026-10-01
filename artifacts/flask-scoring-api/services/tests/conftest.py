"""Service-suite test isolation.

Several service modules can start background player-data work during collection or
execution.  pytest.approx imports NumPy lazily on first use; allowing that first
NumPy import to race a background import can expose a partially initialized
module (for example, missing ``numpy.isscalar``).  Import NumPy once here, during
pytest's single-threaded conftest bootstrap, before service tests can start those
workers.

This is test-process isolation only and is never imported by the production
service runtime.
"""
from __future__ import annotations

import numpy as _numpy


# Fail immediately during collection if the dependency itself is malformed,
# rather than surfacing a misleading assertion failure later in the suite.
assert hasattr(_numpy, "isscalar")
