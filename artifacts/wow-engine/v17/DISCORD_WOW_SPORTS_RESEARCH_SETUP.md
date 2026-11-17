# WOW Sports Research — Discord operator onboarding (2026-11-17)

## Known, public identity
- Application: **WOW Sports Research**
- Application ID / Client ID: `1572359272317063259`
- Portal: https://discord.com/developers/applications/1572359272317063259/information
- Target: **User Install**, NOT unapproved server bot or self-bot.
- Current state: application exists according to operator; no live OAuth, command, Discord PING, storage, server monitoring or deployment is proven.

## Operator setup in Discord Developer Portal

1. Open **Installation**. Enable **User Install** under Installation Contexts. Select **Discord Provided Link**. Under User Install default install settings select only **`applications.commands`**. Save. **Guild Install** is not required. Do not request bot permissions, Message Content, Guild Members or Presence intents.
2. Under **General Information**, find **Public Key**. This 64-character hex key is public and can be provided for configuration. A **Client Secret** or **bot token is confidential** and must NOT be pasted into GitHub, ChatGPT or screenshots.
3. The app's **Interactions Endpoint URL** must be a *real, deployed and tested HTTPS endpoint* ending `/discord/interactions`. Do not point it at the currently deployed governed Render API merely because the hostname exists: this PR has not been mounted or deployed there. Discord will send signed PINGs when saving the URL; a nonexistent endpoint cannot pass.
4. For optional personal identity discovery, add the deployed HTTPS `/discord/oauth/callback` under **OAuth2 → Redirects**. The app requests only `identify guilds`; guild listing is not permission to read messages. The callback validates Discord's `iss` and OAuth state.
5. In the Discord client enable **User Settings → Advanced → Developer Mode**, then copy **your own User ID** from your profile. Configure it as the `WOW_DISCORD_ALLOWED_USER_IDS` allowlist in server secrets (never other people's IDs without their consent).
6. After endpoint deployment and review, use your app's **Installation** link to select **Add to My Apps**. In third-party servers where user apps are permitted, right-click an eligible sports message → **Apps → Save to WOW Research**.

## Register the context command (only once secrets and review are ready)

The registration uses Discord **client credentials**, scope `applications.commands.update`, and creates **one global message command** with `type=3`, `integration_types=[1]` and `contexts=[0,1,2]`. It does not add a bot to a server.

From `artifacts/wow-engine`:

```sh
python -m v17.discord_register_command
# dry-run; prints public payload, never touches Discord

# After supplying DISCORD_CLIENT_SECRET through an approved protected environment:
python -m v17.discord_register_command --register
```

The publicly known application ID is the default. `DISCORD_APPLICATION_ID` can override it if a new application is deliberately approved. Only the `--register` option writes to Discord; it fails closed without credentials. The command can be registered from a protected one-shot job; no always-on bot is required.

## Required server configuration after governed release

```text
DISCORD_APPLICATION_ID=1572359272317063259              # public
DISCORD_PUBLIC_KEY=<portal public signing key>           # public
DISCORD_CLIENT_SECRET=<server secret manager>            # confidential
WOW_DISCORD_ALLOWED_USER_IDS=<your numeric Discord ID>
WOW_DISCORD_OAUTH_STATE_SECRET=<random 32+ character secret>
WOW_DISCORD_OAUTH_REDIRECT_URI=https://<approved-host>/discord/oauth/callback
WOW_DISCORD_DATABASE_URL=<approved private Postgres connection> 
```

Never commit secret values. Prefer reusing an already paid-for approved Render service only if integration with its live API is independently reviewed; **no production route has been mounted** and no paid service is authorized by this implementation. Private `wow_discord` schema must remain unexposed to public Data API. Avoid making a new paid service without a cost review.

## Acceptance proof checklist

- Exact PR head passes required tests, independent QA App, security review and Release Authority App; no self-approval.
- Deployed SHA matches reviewed code and correct server secrets are present (values never disclosed).
- Discord verifies a signed PING at actual endpoint; invalid or stale signatures fail.
- OAuth2 returns only the allowlisted account's basic guild list, no channel history.
- User installs app successfully and invokes message context command in a permitted channel.
- Private DB receives **exactly one** metadata receipt for the chosen message; duplicate is idempotent. No text stored; no private message content in logs. DB records both event metadata and server-side receipt time.
- Unsupported server contexts/unauthorized actors do not generate receipts.
- No gameplay model probability, no certification, no order or wager, `can_execute=false`.

### What this does **not** accomplish yet

It does not process sports claims from message bodies. A content hash cannot extract injuries or game scripts. Content-derived intelligence requires separate permission/rights review, safe short-lived content handling, independent source confirmation, proper event identity and chronology checks, and governed research promotion. Continuous monitoring of someone else's channel requires their administrator's explicit bot access and current Discord data-access compliance.

Reference: https://discord.com/developers/docs/tutorials/developing-a-user-installable-app
