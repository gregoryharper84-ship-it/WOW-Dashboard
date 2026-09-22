# WOW Odds Proxy Internal Acquisition Authentication

`WOW_ODDS_PROXY_INTERNAL_KEY` is an optional server-to-server bearer used only by the production WOW engine to call the read-only odds credential proxy.

It is separate from the Custom GPT Action bearer and GitHub Actions OIDC. The proxy continues to accept those existing authorized modes. The internal bearer grants only the same read-only acquisition endpoints already exposed by the proxy; it creates no wager/order/execution authority and `can_execute=false` remains invariant.

The same secret value must be configured only in the engine service and odds-proxy service environments. It must never be committed, logged, returned in API responses, or exposed to Custom GPT clients.
