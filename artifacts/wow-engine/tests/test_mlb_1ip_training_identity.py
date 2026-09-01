from mlb_1ip_training_dataset import game_training_rows


def test_training_manifest_preserves_opener_identity_without_reliever_contamination():
    def fake_get(url, params=None, timeout=None):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "allPlays": [
                        {"about": {"inning": 1, "halfInning": "top"}, "matchup": {"pitcher": {"id": 10}}, "playEvents": [{"isPitch": True}] * 4},
                        {"about": {"inning": 1, "halfInning": "top"}, "matchup": {"pitcher": {"id": 10}}, "playEvents": [{"isPitch": True}] * 5},
                        {"about": {"inning": 1, "halfInning": "top"}, "matchup": {"pitcher": {"id": 10}}, "playEvents": [{"isPitch": True}] * 4},
                        {"about": {"inning": 1, "halfInning": "top"}, "matchup": {"pitcher": {"id": 99}}, "playEvents": [{"isPitch": True}] * 6},
                        {"about": {"inning": 1, "halfInning": "bottom"}, "matchup": {"pitcher": {"id": 20}}, "playEvents": [{"isPitch": True}] * 4},
                        {"about": {"inning": 1, "halfInning": "bottom"}, "matchup": {"pitcher": {"id": 20}}, "playEvents": [{"isPitch": True}] * 4},
                        {"about": {"inning": 1, "halfInning": "bottom"}, "matchup": {"pitcher": {"id": 20}}, "playEvents": [{"isPitch": True}] * 4},
                    ]
                }

        return Response()

    rows, manifest = game_training_rows(123, http_get=fake_get)
    assert [(r.bf, r.pitches) for r in rows] == [(3, 13), (3, 12)]
    assert manifest["rows_detail"] == [
        {"half": "TOP", "pitcher_id": 10, "bf": 3, "pitches": 13},
        {"half": "BOTTOM", "pitcher_id": 20, "bf": 3, "pitches": 12},
    ]
    assert manifest["relief_pitch_events_excluded"] == 6
    assert 99 not in manifest["opener_pitcher_ids"]
