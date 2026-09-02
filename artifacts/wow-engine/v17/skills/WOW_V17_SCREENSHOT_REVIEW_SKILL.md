# WOW V17 Screenshot Review Skill

skill_id=WOW_V17_SCREENSHOT_REVIEW
owners=WOW_BETTING_ENGINE,LLP_TEAM_BETTING_ENGINE
can_execute=false

## Trigger
Use whenever the user supplies screenshots, images, PDFs, or pasted betting boards and asks for extraction, review, best picks, full model, props, MLs, or winners.

## Non-negotiable extraction rule
**Extract every visible betting row first, before filtering or scoring.**

Do not pre-filter to the easiest market family. If a board contains strikeouts, pitching outs, pitches/strikes, plate appearances, first-inning pitches, points, rebounds, assists, shots, games, aces, MLs, or other visible markets, record every readable row.

## Extraction contract
For every visible row preserve, when visible:
- source screenshot/PDF/page
- sport/league
- event/opponents
- participant/team
- market/stat
- exact displayed line
- direction/side
- displayed odds/payout/market context if present
- scheduled time if present
- extraction confidence

Do not merge distinct lines or opposite directions. Deduplicate only true duplicate rows. If a field cannot be read confidently, mark it `EXTRACTION_UNCERTAIN` rather than guessing.

## Routing
After the complete extraction pass:
- player/scalar/prop rows -> WOW_BETTING_ENGINE prop lane;
- team/event ML, winner, favorite, underdog, upset, match-winner, fight-winner rows -> LLP_TEAM_BETTING_ENGINE;
- exactly one controlling specialist per row.

## Governed review
1. Validate exact identity against current event/player/team context.
2. Refresh current injury/lineup/role/status and other material evidence.
3. Call the governed backend route for each supported candidate.
4. Never treat screenshot odds or payout as model probability.
5. Exact screenshot line controls the requested market identity. Adjacent external lines are context only.
6. Preserve typed fail-closed responses; do not substitute generic research for a missing fitted model.
7. Rank only official model-supported rows by calibrated lower bound where required.
8. Keep screenshot-origin results visibly separate from autonomous-discovery results unless the user asks for one combined pool.

## Output
First report extraction coverage, e.g. `Extracted 47/47 readable rows across 5 market families` and name the market families so omitted categories are obvious.

Then present:
- `Best Screenshot Props`
- `Best Screenshot ML Winners`
- `Best Screenshot Underdogs/Upsets`
- `Unsupported / Held / Extraction-Uncertain` diagnostics when useful

For official rows show the same governed probability fields as the V17 daily skill plus source screenshot/page and extraction confidence.

## Safety
- No probability fabrication.
- No silent market-family omission.
- No retroactive line substitution.
- No wager placement.
- `can_execute=false` always.
