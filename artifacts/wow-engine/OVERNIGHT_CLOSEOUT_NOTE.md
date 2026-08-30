# WOW Overnight Closeout Note

This branch converges the durable Agent Runtime onto the existing `public.wow_*` data plane and preserves `can_execute=false` and dry-run-only governance. It does not authorize probability publication, live trading, market orders, or capital allocation.

Production blockers must remain evidence-driven. In particular, Calibration Health may remain BLOCKED while forward-shadow outcomes are incomplete; no code or deployment change may force that gate to PASS.
