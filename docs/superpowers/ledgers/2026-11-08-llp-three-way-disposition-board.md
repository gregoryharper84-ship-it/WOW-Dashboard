# SDD ledger — plan: docs/superpowers/plans/2026-11-08-llp-three-way-disposition-board.md

Base: db5e6b7c8e97079dc52ede0e36cc2b0e6e5bbc8c
Branch: chatgpt/llp-three-way-disposition-board-20261108
Parent issue: #2076
Related active readback PR: #2487

## Setup

- Repository main was inspected at db5e6b7c8e97079dc52ede0e36cc2b0e6e5bbc8c.
- No local repository checkout was present. A shallow git clone failed because outbound network access is unavailable in the execution container.
- No native worktree tool is exposed. An isolated GitHub feature branch was created from the exact main SHA; all edits remain on that branch.
- Baseline tests could not be run locally because the repository could not be cloned. Hosted PR CI is the verification surface.

## Rulings

- Ruling: Keep PR #2487 untouched and add the board as a separate layer that imports the existing LLP projection interface — PR #2487 is active and owns the current ledger-verification/readback files; avoiding a parallel edit prevents review conflicts. Cost if wrong: the board may need a small follow-up adaptation if that interface changes.
- Ruling: QUALIFIED requires source_diagnostics.event_prediction_receipt_verified=true, not a Daily row's own claim — the main-branch projector does not independently verify the ledger; this keeps the board fail-closed until #2487's verifier is merged. Cost if wrong: qualified count remains zero until that integration lands.
- Ruling: PROVISIONAL requires a present model artifact ID and SHA-256 plus two distinct non-market source domains — the user explicitly classified missing artifacts as HELD, and market prices alone are not independent research. Cost if wrong: fewer provisional selections may be displayed.
- Ruling: External research is passed in the stateless read-only board request and is not persisted — this avoids a new table/migration and keeps research outside scoring/terminal authority. The API validates metadata but does not fetch source content. Cost if wrong: research context is not durable across later readbacks.
- Ruling: board_complete means complete against the persisted Daily run manifest and discovery/acquisition reconciliation, not official schedule completeness — the existing run cannot independently prove that an upstream feed omitted no official event. Cost if wrong: users must do a separate official-slate comparison before calling a board a full official slate.
