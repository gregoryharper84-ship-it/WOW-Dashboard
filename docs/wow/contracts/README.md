# WOW Authoritative Contract Documents

This directory holds the authoritative WOW skill and contract documents. These are the binding authority for all patch decisions. The `wow-replit-patch-governor` skill references these documents in the `binding_authority` field of every build-packet contract.

---

## What belongs here

- WOW domain skill files (sport-specific scoring rules, probability formulas, gate contracts)
- Stage-level pipeline contracts (Stage 1 intake, Stage 2 settlement, etc.)
- Endpoint API contracts (request/response shapes, error codes)
- Governance invariant specifications

## What does NOT belong here

- Engineering process documents (those live in `docs/wow/runbooks/`)
- Architecture decisions (those live in `docs/wow/architecture/`)
- Test fixtures or sample data

---

## Document index

| Document | Description | Status |
|----------|-------------|--------|
| *(add as documents are promoted from external sources)* | | |

---

## How to add a contract document

1. Place the document in this directory with a descriptive filename (e.g. `mlb-failure-path-contract.md`).
2. Add it to the index table above.
3. Reference it in the `binding_authority` field of any patch contract that it governs.
4. Do not modify the document's acceptance tests or invariants — those are binding.

---

## Usage in patch contracts

```yaml
binding_authority: docs/wow/contracts/mlb-failure-path-contract.md § Section 4.2
```

The `wow-replit-patch-governor` skill will load the referenced document and treat its requirements as non-negotiable constraints on the patch implementation.
