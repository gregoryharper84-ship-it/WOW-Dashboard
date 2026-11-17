# WOW V17 Discord integration: user install first

**Phase:** code prototype, not connected, deployed, certified or approved to ingest server history. **Owner:** WOW V17 research. `can_execute=false`.

## Why

The operator is a member (not an administrator) of third-party sports servers. Discord OAuth `identify guilds` does **not** grant message-read access. Do not build a self-bot, intercept sessions or scrape the browser. Instead, a **User Install** application with the **Save to WOW Research** message context command lets the operator explicitly submit one selected message in contexts that permit user-installed apps. The app cannot continuously monitor arbitrary channels; a server-admin-approved bot with permitted scopes/intents is a distinct later option. Some server settings disallow external apps; the operator must respect those settings.

## What this code does

- `GET /discord/oauth/start` → OAuth2 `identify guilds` flow, short-lived signed SameSite cookie. Callback checks the account against `WOW_DISCORD_ALLOWED_USER_IDS`, returns basic guild listing, does not store tokens or messages.
- `POST /discord/interactions` → Discord Ed25519 signature verification, 5-minute timestamp window, PING support and message context command. Only allowlisted Discord user IDs can save. Each chosen message produces an append-only private research metadata receipt with SHA-256 fingerprint, authored timestamp (source-provided), server-observed ingestion timestamp, source ID and no text. `ON CONFLICT DO NOTHING` deduplicates interaction delivery.
- A signed PING does not imply authorization to read channels. Storage failure is **not** reported as capture success. Discord credentials and database password must be configured in a server secret manager, not in GitHub or client bundles.
- No model scorers, calibration, terminal reducer, order execution or sportsbook outcomes are reachable from the bridge. Discord tips are **unverified, non-certified research only**.

## Manual setup, once code is independently reviewed

1. In the [Discord Developer Portal](https://discord.com/developers/applications), create an application. Enable **User Install**; configure the Interactions Endpoint URL to `https://<dedicated-host>/discord/interactions`, and the OAuth redirect URL to `https://<dedicated-host>/discord/oauth/callback`. Do not install a bot into a third-party server without administrator authorization.
2. Add server-only secrets `DISCORD_APPLICATION_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_PUBLIC_KEY`, `WOW_DISCORD_OAUTH_REDIRECT_URI`, a random >=32-character `WOW_DISCORD_OAUTH_STATE_SECRET`, and `WOW_DISCORD_ALLOWED_USER_IDS` containing the operator's numeric Discord user ID. Configure `WOW_DISCORD_DATABASE_URL` only with an approved SSL Postgres credential that can insert into the **private** `wow_discord` schema. No keys in PRs, chat, screenshots or logs.
3. Apply `discord_research_bridge_schema.sql` as a reviewed migration in the correct existing WOW database. It does not use/modify the governed model tables. Keep the schema unexposed to Data API and client apps.
4. Register the global message context command as **User Install** using the `command_spec()` payload and `POST https://discord.com/api/v10/applications/<application_id>/commands` authenticated from server-side administration. The application must be enabled for User Install in the portal.
5. Deploy **separately** using the existing infrastructure's approved release workflow (working directory `artifacts/wow-engine`, command `uvicorn v17.discord_research_bridge:app --host 0.0.0.0 --port $PORT`). Do not alter existing Render service startup or create a new paid instance without explicit cost approval.
6. Log into Discord with OAuth via `/discord/oauth/start`; then right-click one permitted sports message → Apps → **Save to WOW Research**. Verify one row in private `wow_discord.research_receipts` with original message ID, fingerprint, `ingested_at`, and false authority flags. Verify replay produces no extra row; forged/old signatures fail; permissions-denied server yields no capture.

## Future phases (not delivered)

- **Content-derived signals:** this phase deliberately stores **no original message content**. A hash alone cannot power game scripts or model rescoring. Before that capability, establish rights/consent and content-minimization policies, private secure storage with deletion/retention, moderation and source corroboration, event identity reconciliation, independent chronology and a separately reviewed research-only adapter.
- **Continuous monitoring:** only for servers whose admins permit the bot and applicable Discord Message Content intent. Never treat a member's OAuth guild list as channel-read authorization.
- **Two-way Discord notifications:** add only after verified research acceptance and controlled bot scope; no automated wagering or submission.

**Local tests:** `cd artifacts/wow-engine && pytest -q tests/test_v17_discord_research_bridge.py`.

**Release gates:** independent QA/security, OAuth and Discord developer setup, schema permission review, exact production deploy SHA, signed Discord PING, real User Install command, one captured and deduplicated permitted message, refusal in disallowed servers. Not satisfied by unit tests alone.
