"""gate_engine/wnba — WNBA-specific engine modules.

Modules:
  opportunity_engine    — PATCH-WNBA-001: opportunity and role gate
  points_model          — WNBA points distribution model
  assists_model         — WNBA assists distribution model
  threes_model          — WNBA three-point attempts distribution model
  acquisition_packet    — WOW-PATCH-2026-08-06: WNBAOpportunityPacket schema,
                          source normalization, raw ledger reconstruction
  missing_field_detector — WOW-PATCH-2026-08-06: required-field comparison,
                          coverage audit, fallback trigger
  fallback_router       — WOW-PATCH-2026-08-06: source-priority configuration
                          and in-pipeline fallback routing
  evidence_acquisition  — WOW-PATCH-2026-08-06: main orchestrator (called by
                          pipeline.py before the analytical pipeline)
"""
