"""Safety regression for offline nflverse source acquisition: synthetic bytes only."""
from __future__ import annotations

import hashlib
import io
import sys
import urllib.error
import urllib.request
import urllib.response
from email.message import Message
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from acquire_nfl_count_prop_sources import frozen_assets, acquire_one


class FakeResponse(io.BytesIO):
    def __init__(self, blob, final="https://release-assets.githubusercontent.com/test"):
        super().__init__(blob)
        self.final = final

    def geturl(self):
        return self.final


def test_all_official_pins_complete_and_immutable():
    assets = frozen_assets()
    assert len(assets) == 11
    assert len({a[0] for a in assets}) == 11
    assert sum(a[2] for a in assets) == 61620003
    assert all(len(a[1]) == 64 for a in assets)


def test_good_download_then_verified_existing(tmp_path):
    blob = b"gsis_id,pfr_id\nQ1,P1\n"
    digest = hashlib.sha256(blob).hexdigest()
    one = acquire_one("players/players.csv", digest, len(blob), tmp_path,
                      open_url=lambda *a, **k: FakeResponse(blob))
    assert one["action"] == "downloaded_verified"
    two = acquire_one("players/players.csv", digest, len(blob), tmp_path, verify_only=True)
    assert two["action"] == "verified_existing"


def test_bad_sha_never_published(tmp_path):
    with pytest.raises(ValueError, match="NFL_PROP_ACQUIRE_DIGEST_MISMATCH"):
        acquire_one("players/players.csv", "a"*64, 9, tmp_path,
                    open_url=lambda *a, **k: FakeResponse(b"mismatch!"))
    assert not (tmp_path / "players/players.csv").exists()


def test_bad_existing_bytes_never_overwritten(tmp_path):
    dest = tmp_path / "players"
    dest.mkdir()
    target = dest / "players.csv"
    target.write_bytes(b"bad")
    with pytest.raises(ValueError, match="NFL_PROP_ACQUIRE_DIGEST_MISMATCH"):
        acquire_one("players/players.csv", "a"*64, 9, tmp_path)
    assert target.read_bytes() == b"bad"


def test_untrusted_redirect_does_not_install(tmp_path):
    with pytest.raises(ValueError, match="NFL_PROP_ACQUIRE_REDIRECT_UNTRUSTED"):
        acquire_one("players/players.csv", "a"*64, 4, tmp_path,
                    open_url=lambda *a, **k: FakeResponse(b"data", "https://example.com/file"))
    assert not (tmp_path / "players/players.csv").exists()


def test_typed_dns_failure_and_no_partial_output(tmp_path):
    def down(*a, **k):
        raise urllib.error.URLError("DNS unavailable")
    with pytest.raises(ValueError, match="NFL_PROP_ACQUIRE_NETWORK_UNAVAILABLE"):
        acquire_one("players/players.csv", "a"*64, 9, tmp_path, open_url=down)
    assert not (tmp_path / "players/players.csv").exists()
    assert not list(tmp_path.rglob("*.part"))


def test_interrupted_stream_is_typed_network_hold_and_cleans_part(tmp_path):
    """A read failure after bytes arrived must leave no published output."""
    class DropAfterFirstChunk(FakeResponse):
        def __init__(self):
            super().__init__(b"partially downloaded")
            self.reads = 0

        def read(self, length=-1):
            self.reads += 1
            if self.reads == 2:
                raise OSError("connection reset midstream")
            return super().read(length)

    with pytest.raises(ValueError, match="NFL_PROP_ACQUIRE_NETWORK_UNAVAILABLE"):
        acquire_one("players/players.csv", "a" * 64, 30, tmp_path,
                    open_url=lambda *a, **k: DropAfterFirstChunk())
    assert not (tmp_path / "players/players.csv").exists()
    assert not list(tmp_path.rglob("*.part"))


def test_untrusted_302_not_requested_even_once(tmp_path, monkeypatch):
    """Record requested hosts: the foreign Location must never be fetched."""
    requested = []

    class RedirectingHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            requested.append(req.full_url)
            headers = Message()
            headers["Location"] = "https://example.com/evil.csv"
            response = urllib.response.addinfourl(
                io.BytesIO(b""), headers, req.full_url, 302,
            )
            response.msg = "Found"
            return response

    original_build = urllib.request.build_opener
    monkeypatch.setattr(
        urllib.request, "build_opener",
        lambda *handlers: original_build(*handlers, RedirectingHTTPSHandler()),
    )
    with pytest.raises(ValueError, match="NFL_PROP_ACQUIRE_REDIRECT_UNTRUSTED"):
        acquire_one("players/players.csv", "a" * 64, 4, tmp_path)
    assert requested == [
        "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv"
    ]
    assert not (tmp_path / "players/players.csv").exists()


def test_allowed_release_asset_redirect_still_downloads(tmp_path, monkeypatch):
    """A reviewed GitHub release asset redirect must continue to work."""
    blob = b"verified research bytes"
    requested = []

    class RedirectingHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            requested.append(req.full_url)
            headers = Message()
            if req.full_url.startswith("https://github.com/"):
                headers["Location"] = "https://release-assets.githubusercontent.com/asset.csv"
                response = urllib.response.addinfourl(
                    io.BytesIO(b""), headers, req.full_url, 302,
                )
                response.msg = "Found"
                return response
            assert req.full_url == "https://release-assets.githubusercontent.com/asset.csv"
            response = urllib.response.addinfourl(
                io.BytesIO(blob), headers, req.full_url, 200,
            )
            response.msg = "OK"
            return response

    original_build = urllib.request.build_opener
    monkeypatch.setattr(
        urllib.request, "build_opener",
        lambda *handlers: original_build(*handlers, RedirectingHTTPSHandler()),
    )
    result = acquire_one("players/players.csv", hashlib.sha256(blob).hexdigest(),
                         len(blob), tmp_path)
    assert result["action"] == "downloaded_verified"
    assert requested == [
        "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv",
        "https://release-assets.githubusercontent.com/asset.csv",
    ]


def test_verify_only_missing_is_fail_closed(tmp_path):
    with pytest.raises(ValueError, match="NFL_PROP_ACQUIRE_FILE_MISSING"):
        acquire_one("players/players.csv", "a"*64, 9, tmp_path, verify_only=True)


def test_symlink_is_not_accepted(tmp_path):
    (tmp_path / "players").mkdir()
    raw = tmp_path / "other.csv"
    raw.write_bytes(b"test")
    (tmp_path / "players/players.csv").symlink_to(raw)
    with pytest.raises(ValueError, match="NFL_PROP_ACQUIRE_FILE_MISSING"):
        acquire_one("players/players.csv", hashlib.sha256(b"test").hexdigest(), 4,
                    tmp_path, verify_only=True)


def test_pr2605_observe_official_source_reconciliation(tmp_path):
    """One PR-scoped live-source *observation*, never a required source gate.

    The standard rapid-regression runner has network access unavailable to the
    ChatGPT container. This probe runs only in that existing PR/workflow, makes
    no modification to protected workflows and intentionally SKIPs with an
    explicit real-data receipt or typed HOLD rather than claiming certification.
    It does not apply on main, other branches or other test shards.
    """
    import json
    import os
    import pandas as pd

    if (os.environ.get("GITHUB_WORKFLOW") != "wow-v17-rapid-repair"
            or os.environ.get("GITHUB_HEAD_REF")
            != "engineering/nfl-five-count-candidates-20261111"):
        pytest.skip("NFL_PROP_SOURCE_OBSERVATION_NOT_PR2605_RAPID_RUNNER")

    from acquire_nfl_count_prop_sources import frozen_assets
    assets = frozen_assets()
    downloaded = []
    # Every real network request must use the trusted redirect handler.
    for name, digest, size in assets:
        try:
            receipt = acquire_one(name, digest, size, tmp_path, timeout=20)
        except (ValueError, OSError) as exc:
            pytest.skip(
                "NFL_PROP_SOURCE_ACQUIRE_HOLD:"
                + type(exc).__name__ + ":" + str(exc)[:180]
                + f":verified_prior_assets={len(downloaded)}/11"
            )
        downloaded.append(receipt)

    # This never means modeled-count outcomes are safe to impute or publish.
    from train_nfl_missing_prop_candidates import (
        load_source_files, EXPECTED_RELEASE_SHA256, SEASONS,
    )
    from audit_nfl_count_prop_participation import (
        _verified_csv, SNAP_SHA256, PLAYERS_SHA256, reconcile_player_games,
    )
    try:
        stats, provenance = load_source_files(
            tmp_path / "stats_player", expected_sha256=EXPECTED_RELEASE_SHA256
        )
        snaps = pd.concat([
            _verified_csv(tmp_path / "snap_counts" / f"snap_counts_{season}.csv",
                          SNAP_SHA256[str(season)])
            for season in SEASONS
        ], ignore_index=True)
        players = _verified_csv(tmp_path / "players" / "players.csv", PLAYERS_SHA256)
        result = reconcile_player_games(stats, snaps, players)
        note = {
            "source_asset_count": len(downloaded),
            "source_bytes_sha_verified": sum(item["size_bytes"] for item in downloaded),
            "source_provenance_hash": provenance.get("bundle_sha256"),
            "participation_status": result["status"],
            "unattributed_identity_quarantine_count": len(
                provenance["unattributed_identity_quarantine"]
            ),
            "unattributed_identity_quarantine_by_season": {
                str(year): sum(
                    row["source_season"] == year
                    for row in provenance["unattributed_identity_quarantine"]
                )
                for year in SEASONS
            },
            "source_identity_quarantine_independently_reviewed": False,
            "unattributed_identity_quarantine_examples":
                provenance["unattributed_identity_quarantine"][:3],
            "offensive_participant_games": result["offensive_participant_games"],
            "missing_player_games": result["missing_player_games"],
            "unmapped_pfr_player_games": result["unmapped_pfr_player_games"],
            "unmapped_distinct_pfr_ids": result["unmapped_distinct_pfr_ids"],
            "unmapped_gap_cause_player_games": result["unmapped_gap_cause_player_games"],
            "unmapped_gap_cause_distinct_ids": result["unmapped_gap_cause_distinct_ids"],
            "unmapped_gap_examples": result["unmapped_examples"][:8],
            "unmapped_player_profiles": result["unmapped_player_profiles"],
            "missing_weekly_label_history_exposure":
                result["missing_weekly_label_history_exposure"],
            # Offline observational exposure only: previous ten recorded
            # weekly rows may skip real snap-positive participant games.
            "observed_l10_history_gap_exposure": {
                "status": result["observed_l10_history_gap_exposure"]["status"],
                "by_route": {
                    route: {
                        "eligible_recorded_l10_rows": values["eligible_recorded_l10_rows"],
                        "observed_l10_windows_with_unrecorded_snap_positive_game":
                            values["observed_l10_windows_with_unrecorded_snap_positive_game"],
                        "by_season": values["by_season"],
                        "examples": values["examples"][:2],
                    }
                    for route, values in result[
                        "observed_l10_history_gap_exposure"
                    ]["by_route"].items()
                },
            },
            "unmapped_profile_total_pfr_ids": result["unmapped_profile_total_pfr_ids"],
            "participation_breakdown": result["participation_breakdown"],
            "missing_examples": result["missing_examples"][:2],
        }
    except (ValueError, KeyError, TypeError) as exc:
        pytest.skip(
            "NFL_PROP_SOURCE_ALL_11_DIGESTS_VERIFIED_BUT_COHORT_HOLD:"
            + type(exc).__name__ + ":" + str(exc)[:250]
        )

    # The PBP presence probe is *secondary* to the full official-source
    # checksum and snap-participation HOLD above. It never makes a zero count.
    from audit_nfl_count_prop_pbp_presence import (
        pbp_assets, read_verified_pbp_presence, classify_missing_pbp_presence,
    )
    pbp_receipts = []
    pbp_games = set()
    pbp_actors = set()
    try:
        for relative, digest, size in pbp_assets():
            receipt = acquire_one(relative, digest, size, tmp_path, timeout=20)
            # Acquired and SHA-verified bytes remain proven if the parser
            # subsequently rejects this archived event schema.
            pbp_receipts.append(receipt)
            season = int(relative.split("_")[-1].split(".")[0])
            games, actors = read_verified_pbp_presence(tmp_path / relative, season)
            pbp_games.update(games)
            pbp_actors.update(actors)
        pbp_result = classify_missing_pbp_presence(
            stats, snaps, players, pbp_games, pbp_actors,
        )
        note["pbp_status"] = pbp_result["status"]
        note["pbp_presence_counts"] = pbp_result["pbp_presence_counts"]
        note["pbp_matched_positive_control"] = pbp_result["pbp_matched_positive_control"]
        note["pbp_positive_opportunity_control"] = pbp_result["pbp_positive_opportunity_control"]
        note["pbp_actor_id_alignment_observed"] = pbp_result["pbp_actor_id_alignment_observed"]
        note["pbp_game_count"] = len(pbp_games)
    except (ValueError, OSError) as exc:
        note["pbp_status"] = "HOLD_PBP_EVIDENCE_INCOMPLETE"
        note["pbp_failure"] = type(exc).__name__ + ":" + str(exc)[:160]
    note["pbp_verified_asset_count"] = len(pbp_receipts)
    note["pbp_verified_compressed_bytes"] = sum(
        receipt["size_bytes"] for receipt in pbp_receipts
    )
    # Reuse only the already-frozen PBP archives; event totals are provisional
    # source-comparison signals, not certified settlement labels.
    if note.get("pbp_status") == "HOLD_PBP_PRESENCE_ONLY":
        from audit_nfl_count_prop_pbp_stats import (
            read_pinned_pbp_signals, compare_weekly_source_to_pbp_signals,
            add_quality_totals, SOURCE_ROLES,
        )
        from collections import Counter
        candidates = {name: Counter() for name in SOURCE_ROLES}
        stat_games = set()
        source_issues = {"flag_nulls": {}, "unattributed": {}}
        try:
            for year in SEASONS:
                path = tmp_path / "pbp" / f"play_by_play_{year}.csv.gz"
                year_signals, year_games, issues = read_pinned_pbp_signals(path, year)
                for name in SOURCE_ROLES:
                    candidates[name].update(year_signals[name])
                stat_games.update(year_games)
                add_quality_totals(source_issues, issues)
            fidelity = compare_weekly_source_to_pbp_signals(
                stats, candidates, stat_games,
            )
            note["pbp_stat_signal_status"] = fidelity["status"]
            note["pbp_stat_signal_comparison"] = fidelity["by_source_stat"]
            from audit_nfl_count_prop_pbp_stats import (
                inspect_pinned_residual_actor_plays,
            )
            residuals = []
            for target in ("attempts", "carries"):
                for example in fidelity["by_source_stat"][target]["examples"][:2]:
                    season = int(example["game_id"].split("_", 1)[0])
                    residuals.append(inspect_pinned_residual_actor_plays(
                        tmp_path / "pbp" / f"play_by_play_{season}.csv.gz",
                        season, example["game_id"], example["player_id"], target,
                    ))
            note["pbp_stat_residual_event_evidence"] = residuals
            note["pbp_stat_flag_quality"] = source_issues
        except (ValueError, OSError, KeyError) as exc:
            note["pbp_stat_signal_status"] = "HOLD_PBP_STAT_EVIDENCE_INCOMPLETE"
            note["pbp_stat_signal_failure"] = type(exc).__name__ + ":" + str(exc)[:160]
    # Source-authorized one-asset ID bridge probe; the roster never enters
    # the snap/stat join or fitted model. Older roster vintages have no
    # published SHA-256 and remain unavailable for this frozen test.
    from audit_nfl_count_prop_roster_crosswalk import (
        read_pinned_2025_roster, inspect_roster_id_candidates,
    )
    try:
        roster, roster_receipt = read_pinned_2025_roster(timeout=20)
        candidates = inspect_roster_id_candidates(
            roster, [p["pfr_player_id"] for p in result["unmapped_player_profiles"]],
        )
        note["roster_2025_asset"] = roster_receipt
        note["roster_2025_crosswalk"] = candidates
    except (ValueError, OSError) as exc:
        note["roster_2025_status"] = "HOLD_ROSTER_CROSSWALK_SOURCE_UNAVAILABLE"
        note["roster_2025_failure"] = type(exc).__name__ + ":" + str(exc)[:200]
    # A separately SHA-256-pinned historical weekly roster from the same
    # public provider; no unpinned year or identity guessing is accepted.
    try:
        weekly, weekly_receipt = read_pinned_2025_roster(timeout=20, weekly=True)
        weekly_candidates = inspect_roster_id_candidates(
            weekly, [p["pfr_player_id"] for p in result["unmapped_player_profiles"]],
        )
        note["roster_2025_weekly_asset"] = weekly_receipt
        note["roster_2025_weekly_crosswalk"] = weekly_candidates
    except (ValueError, OSError) as exc:
        note["roster_2025_weekly_status"] = "HOLD_ROSTER_CROSSWALK_SOURCE_UNAVAILABLE"
        note["roster_2025_weekly_failure"] = type(exc).__name__ + ":" + str(exc)[:200]
    # Before any acceptance, inspect four older, size-matched public archive
    # reads twice. GitHub publishes no SHA for those assets: results are always
    # explicitly unapproved, read-only provenance candidates.
    from audit_nfl_count_prop_roster_crosswalk import (
        read_two_copy_historical_roster,
    )
    historical_notes = []
    for year in (2021, 2022, 2023, 2024):
        targets = [
            profile["pfr_player_id"]
            for profile in result["unmapped_player_profiles"]
            if year in profile["seasons"]
        ]
        if not targets:
            continue
        try:
            historical, receipt = read_two_copy_historical_roster(year, timeout=20)
            match = inspect_roster_id_candidates(
                historical, targets, expected_season=year,
            )
            historical_notes.append({
                "year": year, "source": receipt, "exact_pair_candidates": match,
            })
        except (ValueError, OSError) as exc:
            historical_notes.append({
                "year": year, "status": "HOLD_HISTORICAL_ROSTER_SOURCE_UNAVAILABLE",
                "failure": type(exc).__name__ + ":" + str(exc)[:180],
            })
    note["historical_roster_research_only"] = historical_notes
    note["historical_roster_digest_independently_published"] = False
    note["historical_roster_candidates_applied"] = False
    note["pbp_absence_proves_zero"] = False

    pytest.skip(
        "NFL_PROP_OFFLINE_SOURCE_RESEARCH_RECEIPT:"
        + json.dumps(note, sort_keys=True, separators=(",", ":"), default=str)
        + ":source_rights_independently_approved=false"
        + ":fitted_model_certified=false:can_execute=false"
    )

