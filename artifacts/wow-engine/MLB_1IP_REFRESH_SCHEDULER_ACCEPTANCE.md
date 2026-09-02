# MLB 1IP refresh scheduler acceptance

Acceptance requires the full WOW engine regression suite to remain green, the production entrypoint to import successfully, the scheduler to stay disabled by default, and an enabled deployment to emit `WOW_MLB_1IP_FINAL_REFRESH` without changing `probability_publishable=false` or `can_execute=false`.
