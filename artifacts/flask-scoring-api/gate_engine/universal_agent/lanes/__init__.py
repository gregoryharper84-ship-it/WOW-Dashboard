"""
gate_engine/universal_agent/lanes/__init__.py
WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1-B3A

Lane adapter package for the Universal Agent Core.
Each sub-package maps one WOW scoring lane's evidence into the
universal EvidencePacket + B1 advisory role contracts.

Invariants across all lane adapters
------------------------------------
- can_execute = False: adapters are pure data transformers only.
- No live LLM/API calls.
- No app.py import or Flask route wiring.
- No terminal-label or final-decision authority.
- No trading, capital allocation, or deployment authority.
- Missing required evidence fields → explicit UNKNOWN/MISSING, never fabricated.
"""
