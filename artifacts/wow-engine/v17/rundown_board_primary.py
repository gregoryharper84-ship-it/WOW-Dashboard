"""Compatibility import for the V17 TheRundown board-primary adapter.

The active implementation lives in :mod:`v17.rundown_board_primary_v2`.  Keep
this module name stable for validation and callers while the branch is under
review.  No probability or execution authority is introduced here.
"""
from v17.rundown_board_primary_v2 import *  # noqa: F401,F403
