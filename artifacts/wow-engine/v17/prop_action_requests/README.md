# Governed prop Action request fixtures

Files in this directory are immutable or explicitly historical request inputs for the canonical V17 prop Action workflow. Adding or changing a fixture intentionally triggers the workflow's production infrastructure acceptance on `main`.

Historical fixtures must be dispatched with `allow_historical_request=true`; they never count as current-slate or live-host proof. Production scoring remains dry-run only with `can_execute=false`.
