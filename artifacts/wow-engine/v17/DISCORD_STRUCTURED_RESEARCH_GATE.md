# WOW V17 Discord structured research gate

Status: code-only candidate. No live Discord read, endpoint deployment, database writes, model scoring, certification, or wagering. Separate from unmerged Discord user-install bridge PR #2709.

## Purpose
Use only minimal authorized structured research facts from third-party Discord messages AFTER a trusted intake has established channel permission, owner rights for the particular processing, source provenance, a server-observed capture timestamp, and explicit event/market identity.
The validator does not retrieve, store or train on raw Discord message text or attachments. It does not interpret a private message. A separately approved extractor would be necessary for any text-to-fact transformation.

## Mandatory security rules
- User membership, subscription, OAuth identify/guilds, and a User Install command do not authorize automatic server scraping or channel collection.
- The source_use_authorized argument must originate from trusted server configuration after documentary authorization, never from a client or Discord payload. Its use in tests does not establish live rights.
- allowed_channels is a trusted fail-closed guild/channel allowlist. No public channel IDs, message bodies, credentials or proprietary selections should be committed.
- Discord Developer Policy prohibits using message content from its API to train AI/ML models without express Discord permission. This change does no model training.
- No probability, rank, calibration, betting execution or certified model can be changed by these outputs.

## Input contract
Pure Python: accept_structured_claim(claim, observed_at, allowed_channels, source_use_authorized). The caller must supply a server-observed timestamp, authorized channel set and a structured record with a guild/channel/message ID, claim index, source creation time, event start time, sport, event ID, kind, market, direction and applicable line/player/stat. A source-edited timestamp, book and American odds are optional.
Sports: NFL, NBA, NHL, NCAAF, NCAAB, MLB, WNBA, soccer, tennis, golf, MMA, boxing, cricket.
Markets: moneyline, spread, total, player prop. Source classes: pregame pick, sharp alert, postgame recap.
Unexpected raw content or caller-supplied certification flags cause rejection.

## Chronology and settlement
- A pregame pick/sharp alert received after event start is rejected as non-prospective; a source-created timestamp alone cannot restore prospectivity.
- A recap is a source-reported outcome only, never an independently verified settlement. Official sporting results are a separate future verification task.
- Revisions are fingerprint-sensitive. Fingerprints are for deduplication only, not evidence of historical price availability or consent.
- Exact event/player ID, source snapshot, book-specific price and late news must be corroborated independently by the existing sport specialists before research can inform decisions.

## Verification
Run: pytest -q artifacts/wow-engine/tests/test_v17_discord_structured_signal.py
Tests use only synthetic input. No connected Discord feed, external data source or live database is touched.

## Release ladder
1. Resolve PR #2709's protected QA/Release checks without bypass, and verify exact-head approval.
2. Configure existing app server-side secrets, approved HTTPS endpoint and private database migration through the authorized release path.
3. Verify signed Discord PING, a user-selected message context interaction (where server permissions allow), no-access and replay rejection.
4. Secure creator and server rights before structured claims are derived from third-party content. Continuous monitoring additionally requires server admin-approved bot access and relevant message content permissions.
5. Add a separately rights-approved extractor and deterministic typed routing. Do not train AI on Discord API message content without express permission.
6. Independently verify player, event, market quote and results, then add research-only post-score annotations to existing matchup/sport specialists.
7. Prove end-to-end receipt, idempotency, chronology, security, latency and production SHA without altering fitted probabilities, certification or can_execute=false.

Unmerged code in this candidate is not proof of a live connection or model improvement.