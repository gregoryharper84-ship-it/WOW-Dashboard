"""Board processing completion is separate from selection approval."""
import pytest
from v17.pick_request_run_status import governed_run_status

@pytest.mark.parametrize('total,completed,held,rejected,pending,unresolved,expected', [
    (4,0,0,4,0,0,'COMPLETE'),
    (4,2,0,2,0,0,'COMPLETE'),
    (1,1,0,0,0,0,'COMPLETE'),
    (1,0,1,0,0,0,'BLOCKED'),
    (2,1,1,0,0,0,'DEGRADED'),
    (2,0,1,1,0,0,'BLOCKED'),
    (2,0,0,1,1,1,'RUNNING'),
    (1,0,0,1,0,1,'BLOCKED'),
    (2,1,0,0,0,0,'BLOCKED'),
    (1,1,0,1,0,0,'BLOCKED'),
    (0,0,0,0,0,0,'BLOCKED'),
])
def test_governed_board_classification(total,completed,held,rejected,pending,unresolved,expected):
    assert governed_run_status(total=total,completed=completed,held=held,rejected=rejected,
                               pending=pending,unresolved=unresolved) == expected
