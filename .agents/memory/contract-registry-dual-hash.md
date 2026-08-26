---
name: Contract registry dual-hash reconciliation
description: When an external verifier disputes a canonical-document SHA-256, use separate canonical_sha256 + raw_source_sha256 + transformation_record fields in the registry entry.
---

## Rule

When an external verifier independently reconstructs a canonical document and obtains a different hash than the on-disk file, the registry entry must carry three fields instead of one:

```yaml
sha256: "<on-disk hash>"           # backward-compat alias for canonical_sha256
canonical_sha256: "<on-disk hash>" # authoritative — must match the committed file
raw_source_sha256: "<ext hash>"    # external verifier's independently computed hash
transformation_record: >           # required when the two hashes differ
  Explanation of the discrepancy (e.g. whitespace normalization, line endings,
  or reconstruction from a communication channel vs. on-disk bytes).
  State which hash is binding: canonical_sha256.
```

## Why

External verifiers reconstruct documents from return-packet descriptions; line-ending and whitespace normalization in the channel can produce different bytes than the on-disk file. Recording both hashes plus a transformation record makes the discrepancy auditable and prevents the authoritative hash from being silently overwritten.

## How to apply

- `sha256` and `canonical_sha256` MUST agree (sha256 is the backward-compat field used by existing tests).
- `raw_source_sha256` is allowed to differ from the on-disk file.
- `transformation_record` MUST reference the external verifier's hash (so future auditors can trace it).
- Add `test_<contract>_dual_sha_fields_present` test that verifies `canonical_sha256` matches the file on disk AND that `sha256 == canonical_sha256`.

## BINDING_ACTIVE project_authority_status

When an `EMERGENCY_ACTIVE` contract is formally registered with a canonical document and dual-hash reconciliation, set `project_authority_status: BINDING_ACTIVE` (not `EMERGENCY_ACTIVE`). The two fields have different semantics:
- `declared_status` = lifecycle status of the contract
- `project_authority_status` = project's classification of how authoritatively the contract is registered

`BINDING_ACTIVE` is in `VALID_STATUSES` and `PROMOTION_RULES["EMERGENCY_ACTIVE"]` in `gate_engine/tests/test_contract_registry.py`.
