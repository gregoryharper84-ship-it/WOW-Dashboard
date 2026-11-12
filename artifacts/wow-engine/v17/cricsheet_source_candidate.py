"""Bounded Cricsheet T20 archive acquisition and integrity research ONLY.

Acquires an openly published candidate archive, but does not attest original
publisher ownership, authorize commercial training, create a model, or certify.
Run only with an explicit command and retain raw archive in quarantine.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import urllib.parse
import urllib.request
import zipfile
from typing import Any

SOURCE_ID = "CRICSHEET_ODC_BY"
SOURCE_URL = "https://cricsheet.org/downloads/t20s_json.zip"
SOURCE_TERMS = "https://cricsheet.org/licensing/"
VERSION = "WOW_V17_CRICSHEET_T20_CANDIDATE_V1"
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_FILES = 20000
MAX_ENTRY_BYTES = 3 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 450 * 1024 * 1024
MAX_RATIO = 1000
CAN_EXECUTE = False
_SHA = re.compile(r"^[0-9a-f]{64}$")


class SourceCandidateHold(ValueError):
    """Typed, nonpublishable acquisition failure."""


def _safe_entry(member: zipfile.ZipInfo) -> None:
    name = member.filename
    parts = PurePosixPath(name).parts
    if (not name or name.startswith("/") or "\\" in name or
            ".." in parts or ":" in name or not parts or name.endswith("//")):
        raise SourceCandidateHold("SOURCE_ARCHIVE_PATH_INVALID")
    file_type = (member.external_attr >> 16) & 0o170000
    if file_type == stat.S_IFLNK:
        raise SourceCandidateHold("SOURCE_ARCHIVE_SYMLINK_FORBIDDEN")
    if member.file_size > MAX_ENTRY_BYTES:
        raise SourceCandidateHold("SOURCE_ARCHIVE_ENTRY_TOO_LARGE")
    if member.file_size > MAX_RATIO * max(1, member.compress_size):
        raise SourceCandidateHold("SOURCE_ARCHIVE_COMPRESSION_RATIO_INVALID")


def audit_t20_archive(data: bytes, *, expected_sha256: str | None = None) -> dict[str, Any]:
    """Validate source bytes and metadata; never derive sporting probability.

    Match date is a settlement-era field, NOT proof that a feature was observed
    before first ball. This script cannot register a historical training row.
    """
    if not isinstance(data, bytes) or len(data) > MAX_ARCHIVE_BYTES or not data:
        raise SourceCandidateHold("SOURCE_ARCHIVE_SIZE_INVALID")
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None:
        if not _SHA.fullmatch(expected_sha256) or expected_sha256 != digest:
            raise SourceCandidateHold("SOURCE_ARCHIVE_PIN_MISMATCH")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except (zipfile.BadZipFile, ValueError) as exc:
        raise SourceCandidateHold("SOURCE_ARCHIVE_FORMAT_INVALID") from exc
    with archive:
        members = archive.infolist()
        if not members or len(members) > MAX_FILES:
            raise SourceCandidateHold("SOURCE_ARCHIVE_MEMBER_COUNT_INVALID")
        if sum(m.file_size for m in members) > MAX_UNCOMPRESSED_BYTES:
            raise SourceCandidateHold("SOURCE_ARCHIVE_UNCOMPRESSED_LIMIT")
        names: set[str] = set()
        licenses: list[zipfile.ZipInfo] = []
        games: list[zipfile.ZipInfo] = []
        for member in members:
            _safe_entry(member)
            if member.filename in names:
                raise SourceCandidateHold("SOURCE_ARCHIVE_DUPLICATE_MEMBER")
            names.add(member.filename)
            if member.is_dir():
                continue
            if PurePosixPath(member.filename).name == "LICENSE.txt":
                licenses.append(member)
            elif member.filename.endswith(".json"):
                games.append(member)
            elif member.filename.lower().endswith((".exe", ".sh", ".py", ".html")):
                raise SourceCandidateHold("SOURCE_ARCHIVE_EXECUTABLE_MEMBER_FORBIDDEN")
        if len(licenses) != 1:
            raise SourceCandidateHold("SOURCE_ARCHIVE_LICENSE_MISSING_OR_AMBIGUOUS")
        if not games:
            raise SourceCandidateHold("SOURCE_ARCHIVE_NO_MATCHES")
        try:
            license_bytes = archive.read(licenses[0])
        except (RuntimeError, NotImplementedError, zipfile.BadZipFile, EOFError, OSError) as exc:
            raise SourceCandidateHold("SOURCE_ARCHIVE_LICENSE_READ_FAILED") from exc
        license_text = license_bytes.decode("utf-8", errors="replace").lower()
        if not ("attribution" in license_text and
                ("open data commons" in license_text or "odc-by" in license_text)):
            raise SourceCandidateHold("SOURCE_ARCHIVE_LICENSE_NOT_RECOGNIZED")
        selected_match_ids: set[str] = set()
        settled = tie_or_no_result = 0
        date_only = 0
        for member in games:
            stem = PurePosixPath(member.filename).stem
            if not stem.isascii() or not stem.isdigit():
                raise SourceCandidateHold("SOURCE_ARCHIVE_MATCH_ID_INVALID")
            if stem in selected_match_ids:
                raise SourceCandidateHold("SOURCE_ARCHIVE_DUPLICATE_MATCH_ID")
            selected_match_ids.add(stem)
            try:
                obj = json.loads(archive.read(member))
            except (ValueError, UnicodeDecodeError, RuntimeError, NotImplementedError,
                    zipfile.BadZipFile, EOFError, OSError) as exc:
                raise SourceCandidateHold("SOURCE_ARCHIVE_MATCH_JSON_INVALID") from exc
            info = obj.get("info") if isinstance(obj, dict) else None
            if not isinstance(info, dict):
                raise SourceCandidateHold("SOURCE_ARCHIVE_MATCH_TYPE_UNVERIFIED")
            match_type = info.get("match_type")
            if not isinstance(match_type, str) or match_type not in {"T20", "IT20"}:
                raise SourceCandidateHold("SOURCE_ARCHIVE_MATCH_TYPE_UNVERIFIED")
            teams = info.get("teams")
            dates = info.get("dates")
            outcome = info.get("outcome")
            if (not isinstance(teams, list) or len(teams) != 2 or
                    any(not isinstance(t, str) or not t.strip() for t in teams) or
                    teams[0] == teams[1] or not isinstance(dates, list) or not dates or
                    any(not isinstance(d, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for d in dates)):
                raise SourceCandidateHold("SOURCE_ARCHIVE_MATCH_IDENTITY_OR_DATE_INVALID")
            try:
                for match_day in dates:
                    date.fromisoformat(match_day)
            except ValueError as exc:
                raise SourceCandidateHold("SOURCE_ARCHIVE_MATCH_DATE_INVALID") from exc
            if not isinstance(outcome, dict):
                raise SourceCandidateHold("SOURCE_ARCHIVE_OUTCOME_MISSING")
            winner = outcome.get("winner")
            result = outcome.get("result")
            if "winner" in outcome and "result" in outcome:
                raise SourceCandidateHold("SOURCE_ARCHIVE_OUTCOME_CONTRADICTORY")
            if winner is not None:
                if not isinstance(winner, str) or winner not in teams:
                    raise SourceCandidateHold("SOURCE_ARCHIVE_SETTLEMENT_TEAM_MISMATCH")
                settled += 1
            elif isinstance(result, str) and result in {"tie", "no result", "draw"}:
                tie_or_no_result += 1
            else:
                raise SourceCandidateHold("SOURCE_ARCHIVE_OUTCOME_UNRESOLVED")
            date_only += 1
        return {
            "schema": VERSION, "source_id": SOURCE_ID, "source_url": SOURCE_URL,
            "terms_url": SOURCE_TERMS, "archive_sha256": digest,
            "archive_bytes": len(data), "license_sha256": hashlib.sha256(license_bytes).hexdigest(),
            "unique_t20_match_ids": len(selected_match_ids),
            "binary_settled_matches": settled, "nonbinary_or_void_matches": tie_or_no_result,
            "date_only_match_rows": date_only,
            "archive_pin_status": "MATCHED" if expected_sha256 else "UNPINNED",
            "raw_archive_quarantine_required": True,
            "source_review_status": "REQUIRED", "pregame_feature_chronology_status": "UNVERIFIED",
            "official_event_identity_status": "UNVERIFIED",
            "model_training_authorized": False, "model_artifact_generated": False,
            "probability_publishable": False, "model_probability": None,
            "calibrated_lower_bound": None,
            "terminal_authority": "V17_TERMINAL_REDUCER", "can_execute": False,
            "status": "CANDIDATE_ARCHIVE_VALIDATED_NOT_CERTIFIED",
        }


class _PublisherOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Do not follow archive redirects to a different origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname != "cricsheet.org":
            raise SourceCandidateHold("SOURCE_ARCHIVE_REDIRECT_UNTRUSTED")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def acquire_published_t20_archive(*, opener=None) -> bytes:
    """Network-only explicit archive fetch; no credentials and no training."""
    opener = opener or urllib.request.build_opener(_PublisherOnlyRedirect())
    request = urllib.request.Request(
        SOURCE_URL, headers={"User-Agent": "WOW-V17-Research-Archive/1.0"}, method="GET")
    with opener.open(request, timeout=25) as response:
        final = urllib.parse.urlsplit(response.geturl())
        if final.scheme != "https" or final.hostname != "cricsheet.org":
            raise SourceCandidateHold("SOURCE_ARCHIVE_ORIGIN_UNVERIFIED")
        raw = response.read(MAX_ARCHIVE_BYTES + 1)
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise SourceCandidateHold("SOURCE_ARCHIVE_SIZE_INVALID")
    return raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--archive", type=Path, help="already downloaded, quarantined ZIP")
    source.add_argument("--download", action="store_true", help="fetch only pinned publisher T20 ZIP")
    parser.add_argument("--expected-sha256", help="optional pinned raw archive sha256")
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--quarantine-archive", type=Path, help="required if --download")
    args = parser.parse_args(argv)
    if args.download and not args.quarantine_archive:
        parser.error("--quarantine-archive is mandatory with --download")
    if args.archive is not None:
        if args.archive.stat().st_size > MAX_ARCHIVE_BYTES:
            raise SourceCandidateHold("SOURCE_ARCHIVE_SIZE_INVALID")
        raw = args.archive.read_bytes()
    else:
        raw = acquire_published_t20_archive()
    receipt = audit_t20_archive(raw, expected_sha256=args.expected_sha256)
    # Reserve receipt first. If a subsequent exclusive archive create/write or
    # receipt write fails, remove only paths created by this invocation.
    # Existing files are never replaced, and no unreceipted archive is stranded.
    created: list[Path] = []
    try:
        with args.receipt.open("x", encoding="utf-8") as handle:
            created.append(args.receipt)
            if args.quarantine_archive is not None:
                with args.quarantine_archive.open("xb") as archive_handle:
                    created.append(args.quarantine_archive)
                    archive_handle.write(raw)
            json.dump(receipt, handle, sort_keys=True, indent=2)
            handle.write("\n")
    except BaseException:
        for created_path in reversed(created):
            try:
                created_path.unlink()
            except FileNotFoundError:
                pass
        raise
    print(json.dumps({"status": receipt["status"], "receipt": str(args.receipt),
                      "can_execute": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SourceCandidateHold, OSError) as exc:
        print(f"SOURCE_RESEARCH_HELD:{exc}", file=sys.stderr)
        raise SystemExit(2)
