"""Deprecated compatibility import for the removed Wolfram arithmetic runtime.

V17 no longer performs external Wolfram/WolframAlpha computation. Existing
callers that have not yet been renamed import the deterministic local Python
auditor through this compatibility module. No network transport, Wolfram
credential, or Wolfram readiness state is used.
"""
from python_arithmetic_auditor import *  # noqa: F401,F403
