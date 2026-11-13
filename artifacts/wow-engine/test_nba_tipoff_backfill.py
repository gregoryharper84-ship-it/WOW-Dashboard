"""No network or production writes; exact stored-vs-source match regressions."""
from types import SimpleNamespace
import copy

import pytest
from v17.nba_tipoff_backfill import classify, run


def fixture():
    row = {
        "game_id": "espn-123", "season": 2027, "game_date": "2026-11-10",
        "home_team_id": "espn-1", "away_team_id": "espn-2",
        "home_score": 99, "away_score": 98, "home_win": True,
        "source_provider": "SPORTSDATAVERSE_ESPN", "source_endpoint": "https://example.test/a.csv",
        "source_payload_sha256": "a"*64, "source_retrieved_at": "2026-11-11T19:00:00+00:00",
        "scheduled_start_utc": None,
    }
    source = {**row, "scheduled_start_utc": "2026-11-11T00:00:00+00:00"}
    return row, source


class Query:
    def __init__(self, db):
        self.db, self.filters, self.newval, self.window = db, [], None, (0, 100000)
    def select(self, _): return self
    def order(self, _): return self
    def range(self, a,b): self.window=(a,b+1); return self
    def eq(self, k,v): self.filters.append(lambda row: row.get(k)==v); return self
    def is_(self,k,v): self.filters.append(lambda row: row.get(k) is None); return self
    def limit(self,n): self.window=(0,n); return self
    def update(self, changes): self.newval=changes; return self
    def execute(self):
        rows = [r for r in self.db.rows if all(f(r) for f in self.filters)]
        rows = rows[self.window[0]:self.window[1]]
        if self.newval is not None:
            for row in rows: row.update(self.newval)
        return SimpleNamespace(data=[copy.deepcopy(r) for r in rows])
class DB:
    def __init__(self, row): self.rows=[copy.deepcopy(row)]
    def table(self, name): assert name=="wow_nba_training_games"; return Query(self)


def test_matching_timestamp_is_eligible():
    old, source=fixture()
    assert classify(old, source)=="VERIFIED_BACKFILL_ELIGIBLE"


@pytest.mark.parametrize("key,value", [
    ("game_id","espn-other"), ("home_team_id","espn-5"),
    ("home_score",100), ("season",2026), ("source_payload_sha256","b"*64),
])
def test_conflicting_identity_or_hash_never_applies(key,value):
    old, source=fixture(); source[key]=value
    assert classify(old,source)=="SOURCE_IDENTITY_OR_HASH_CONFLICT"


def test_missing_and_naive_times_remain_unverified():
    old,source=fixture(); source["scheduled_start_utc"]=None
    assert classify(old,source)=="SOURCE_TIME_UNVERIFIED"
    source["scheduled_start_utc"]="2026-11-10T19:00:00"
    assert classify(old,source)=="CHRONOLOGY_UNVERIFIED"


def test_wrong_et_day_and_after_observation_blocked():
    old,source=fixture(); source["scheduled_start_utc"]="2026-11-12T00:00:00+00:00"
    assert classify(old,source)=="CHRONOLOGY_CONFLICT"
    source["scheduled_start_utc"]="2026-11-11T20:00:00+00:00"
    assert classify(old,source)=="CHRONOLOGY_CONFLICT"


def test_dry_run_does_not_write_and_apply_changes_only_start():
    old,src=fixture(); db=DB(old)
    pre=run(db,source_rows={src["game_id"]:src})
    assert pre["eligible"]==1 and pre["updated"]==0
    assert db.rows[0]==old
    applied=run(db,source_rows={src["game_id"]:src},apply=True)
    assert applied["updated"]==1
    assert db.rows[0]=={**old,"scheduled_start_utc":src["scheduled_start_utc"]}
    again=run(db,source_rows={src["game_id"]:src},apply=True)
    assert again["updated"]==0 and again["reason_counts"]["ALREADY_PRESENT"]==1


def test_max_updates_is_bounded():
    old,src=fixture(); db=DB(old)
    result=run(db,source_rows={src["game_id"]:src},apply=True,max_updates=1)
    assert result["updated"]==1
    with pytest.raises(ValueError):run(db,source_rows={src["game_id"]:src},max_updates=0)


def test_hash_conflict_keeps_row_untouched():
    old,src=fixture(); db=DB(old); src["source_payload_sha256"]="0"*64
    result=run(db,source_rows={src["game_id"]:src},apply=True)
    assert result["updated"]==0
    assert result["reason_counts"]["SOURCE_IDENTITY_OR_HASH_CONFLICT"]==1
    assert db.rows[0]==old
