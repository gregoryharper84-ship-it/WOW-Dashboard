"""
WOW Prediction Validation Harness v1
=====================================
Isolated offline tooling for backtesting and calibration of MLB 1IP
pitch-count prop predictions.

Production commit frozen at: af96567
DO NOT import live app.py or any endpoint handler from here.
All production code accessed via read-only module imports only.

Entry point: validation/cli.py
Tests:       validation/tests/test_validation_harness.py
Config:      validation/config/eval_rules.yaml
"""

HARNESS_VERSION = "1.0.0"
SPORT_SCOPE     = "MLB"
PROP_SCOPE      = "1IP_PITCHES_THROWN"
FROZEN_COMMIT   = "af96567"
