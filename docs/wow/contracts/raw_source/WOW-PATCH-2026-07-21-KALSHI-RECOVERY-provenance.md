# WOW-PATCH-2026-07-21-KALSHI-RECOVERY — Raw Source Provenance Record

**Contract:** WOW-KALSHI-RECOVERY-COMBO-GOVERNANCE
**Canonical path:** `docs/wow/contracts/canonical/WOW-PATCH-2026-07-21-KALSHI-RECOVERY-AND-COMBO-GOVERNANCE.md`
**Canonical SHA-256:** `2c5daad002222a070fe61c47c22e1a755361e3969139e7d80b94cad17f588e5f`
**External-verifier SHA-256:** `b79118e2d2167c047a5f28bf210345aa98e6f75e25f8cd8aec1ae8b48f604e48`
**Status:** `CHANNEL_TRANSCRIPTION_BYTES_UNAVAILABLE`
**Raw-source file:** not committed — bytes unavailable (see §Determination below)

`can_execute: false`
`runtime_governance_changed: false`
`documented_authority_changed: true`

---

## Provenance Statement

The canonical document was authored directly to disk and committed to the
repository at git commit `1a59025` (WOW-#251 registration, 2026-08-17). It was
never generated from a separate upstream source file; there is no prior "raw
source" that was transformed into the canonical form.

The external verifier obtained the document hash `b79118e2...` by independently
hashing "the supplied authoritative source" as described in the WOW-#251 R1
return packet (a communication-channel presentation of the document content).
The on-disk file hashes to `2c5daad0...`.

---

## Transformation Analysis

The following byte-level transformations of the on-disk canonical were
exhaustively tested and **none** produced `b79118e2...`:

| Transformation applied | Resulting SHA-256 |
|---|---|
| On-disk as committed (LF, 4765 bytes) | `2c5daad0...` ← canonical |
| CRLF line endings | `29747db7...` |
| Strip single trailing `\n` | `a83932b2...` |
| Append extra `\n` | `4e586115...` |
| rstrip trailing whitespace on each line (LF) | `2c5daad0...` (unchanged) |
| rstrip each line + CRLF | `29747db7...` |
| rstrip each line + strip trailing `\n` | `a83932b2...` |

**Conclusion:** `b79118e2...` is not the result of any deterministic byte-level
transformation of the on-disk canonical. The external verifier's hash was
produced from a copy of the document as it existed in the communication channel
(the WOW-#251 return packet), and those exact bytes are not available for
independent reconstruction.

---

## Exact Diff Status

A mechanically-generated diff between the on-disk canonical and the
channel-transcription copy **cannot be produced** because the channel-
transcription bytes are unavailable. No diff file is committed here.

---

## Determination: Raw Source Not Committable

Because the exact bytes producing `b79118e2...` cannot be reconstructed, no
immutable raw-source file can be committed with a verifiable hash. Committing
any file that merely claims to be the raw source without a hash match would be
misleading and is not done here.

**Authoritative value:** `canonical_sha256 = 2c5daad0...` (on-disk, verifiable).
`raw_source_sha256 = b79118e2...` is preserved as a chain-of-custody record of
the external verifier's independently computed hash. The two hashes represent
the same document content under different transcription conditions; the
discrepancy is attributable to channel normalization of unknown character (line
endings, whitespace, encoding).

---

## Registry Fields Added by This Record

```yaml
raw_source_status: "CHANNEL_TRANSCRIPTION_BYTES_UNAVAILABLE"
raw_source_provenance_path: "docs/wow/contracts/raw_source/WOW-PATCH-2026-07-21-KALSHI-RECOVERY-provenance.md"
documented_authority_changed: true
```

---

## Chain of Custody

| Event | Date | Hash | Notes |
|---|---|---|---|
| Document authored and committed | 2026-08-17 | `2c5daad0...` | On-disk canonical |
| External verifier independently hashed | 2026-08-17 | `b79118e2...` | From R1 return packet |
| Dual-hash reconciliation added (R1) | 2026-08-17 | — | `canonical_sha256` + `raw_source_sha256` |
| Provenance record created (R2) | 2026-08-17 | — | This document |

`can_execute: false`
`publish_authorized: false`
`DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS`
