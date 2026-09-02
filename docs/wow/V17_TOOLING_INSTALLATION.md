# WOW V17 tooling installation

Status: REPOSITORY_FOUNDATION_INSTALLED

The tooling in this document supports security, data quality, observability,
research, and reproducibility. None is a controlling probability specialist or
terminal authority.

## Production installation

Render installs artifacts/wow-engine/requirements.txt, which now includes:

- sentry-sdk[fastapi] — optional error/performance telemetry
- pandera and pandas — typed external-evidence validation

Sentry remains inert unless SENTRY_DSN is configured. Default PII collection
and request-body capture are disabled. Telemetry cannot change scoring or
terminal results.

## Research installation

Install the isolated research stack with:

    python -m pip install -r artifacts/wow-engine/requirements-research.txt

It contains:

- DuckDB for local Parquet and historical analysis
- MLflow for experiment/artifact lineage
- Evidently for drift and calibration-health analysis
- Optuna for governed chronological hyperparameter research

Render production deliberately does not install this file. Research results
remain non-authoritative until the normal V17 artifact review, registration,
calibration, and promotion gates pass.

## External connections

These require owner-side accounts or DNS/service authorization and cannot be
truthfully marked connected by a repository commit:

- Codex Security
- Wolfram
- Sentry account/project and DSN
- Cloudflare zone/proxy configuration

Codex Security is for repository/security review only. Wolfram is an optional
calculation auditor only. Neither may generate substitute probabilities,
override blockers, alter terminal labels, or execute wagers.

## Permanent invariants

    CURRENT_GENERATION = V17
    GLOBAL_TERMINAL_AUTHORITY = V17_TERMINAL_REDUCER
    can_execute = false
    DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = true
