# NHL V17 five-on-five xG feature research — design

**Intent.** Build a new, independently testable five-on-five (5v5) expected-goals evidence layer, without modifying the current fitted NHL model or any production probability. The immediate goal is to expose whether a legal, timestamped xG corpus can yield useful match-specific research features; a later Class C challenger must prove actual predictive lift.

**Current architecture.** NHL V1 uses Elo, prior win/goal form and rest. A separate goalie/shot challenger adds shots and PP summaries; PR #2655 covers general fatigue and PR #2639 covers cross-market joint projections. This change does not replace or duplicate them.

**Approach options.** (A) Add xG directly to incumbent scorer (rejected: no verified licensed PIT source and no calibrated challenger); (B) ingest raw NHL event data and construct a homegrown xG fit (rejected: new source rights, modeling, and cohort scope); **(C) add an inert, source-bound xG feature-reconstruction contract** (selected: safely measurable, no production change).

**Inputs.** Offline caller-provided regular-season completed-game records contain exact official event ID, UTC start, UTC settlement, evidence capture time, home/away three-letter team IDs, complete 5v5 xG for each team, source name and 64-character payload digest. No provider network requests or data persistence. Caller-provided source IDs/hashes are *not* independent verification or proof of rights.

**Chronology and integrity.** Require history game's settlement time <= capture time <= target cutoff < target game kickoff; prior-game kickoff < target kickoff. Reject event identity collisions, target event reuse, same-team identity, non-finite/invalid xG, mismatched strength or game type, malformed timestamps and hashes. Sort records deterministically before computing each team's last 5–10 prior matches; never substitute zeros when data are missing.

**Outputs.** Deterministic per-side xGF/xGA, xG share and xG differential (and deltas), sample counts, feature as-of, source digests, and a typed hold if either team lacks sufficient observations. Always mark research-only / unverified external rights / nonpublishable / rank-ineligible / not train-eligible / `can_execute=false` and `V17_TERMINAL_REDUCER` sole terminal authority. No probabilities, model confidence, lines, sportsbook prices or orders.

**Acceptance.** Test red→green on calculations, order invariance, sample windows, source and chronology guards, holds and unchanged authority. Local tests are not protected CI. Separate source-rights authorization, frozen PIT material, fitted challenger, chronological holdout, independent SIRT acceptance, and real Action/terminal receipts remain required before any score-path integration.
