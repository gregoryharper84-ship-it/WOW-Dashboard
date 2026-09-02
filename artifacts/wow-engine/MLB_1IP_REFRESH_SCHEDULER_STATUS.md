# MLB 1IP final-refresh scheduler status

The production entrypoint now supports an opt-in in-process final-refresh scheduler via `WOW_MLB_1IP_FINAL_REFRESH_ENABLED=1`.

The scheduler reuses the web service's existing governed Supabase client, runs the same `mlb_1ip_final_refresh_job.run_once` path, defaults to a 300-second cadence, clamps the loop to at least 60 seconds, and preserves `probability_publishable=false` and `can_execute=false`.

This adapter avoids copying the Supabase service-role credential into a second Render resource. Runtime activation remains explicit through the non-secret environment flag. Deployment parity and stable `/health` remain required after activation.
