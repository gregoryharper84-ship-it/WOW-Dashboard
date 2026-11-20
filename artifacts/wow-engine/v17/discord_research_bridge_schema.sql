-- PRIVATE manual Discord research receipts. No raw chat message text is stored.
-- Apply through reviewed migration; never expose wow_discord to Supabase Data API.
CREATE SCHEMA IF NOT EXISTS wow_discord;
REVOKE ALL ON SCHEMA wow_discord FROM PUBLIC, anon, authenticated;
CREATE TABLE IF NOT EXISTS wow_discord.research_receipts (
    interaction_id text PRIMARY KEY CHECK (interaction_id ~ '^[0-9]+$'),
    message_id text NOT NULL CHECK (message_id ~ '^[0-9]+$'),
    channel_id text NOT NULL CHECK (channel_id ~ '^[0-9]+$'),
    guild_id text,
    actor_id text NOT NULL CHECK (actor_id ~ '^[0-9]+$'),
    message_author_id text,
    source_created_at timestamptz,
    ingested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    content_bytes integer NOT NULL CHECK (content_bytes BETWEEN 1 AND 12000),
    source_class text NOT NULL CHECK (source_class = 'DISCORD_USER_SHARED_METADATA'),
    status text NOT NULL CHECK (status = 'RESEARCH_ONLY_UNVERIFIED'),
    prediction_authority boolean NOT NULL DEFAULT false CHECK (prediction_authority = false),
    can_execute boolean NOT NULL DEFAULT false CHECK (can_execute = false)
);
REVOKE ALL ON ALL TABLES IN SCHEMA wow_discord FROM PUBLIC, anon, authenticated;
ALTER TABLE wow_discord.research_receipts ENABLE ROW LEVEL SECURITY;
-- App must use an explicitly privileged server-side Postgres connection, never a client key.
-- Append-only by application contract: INSERT ON CONFLICT DO NOTHING, no upserts.
