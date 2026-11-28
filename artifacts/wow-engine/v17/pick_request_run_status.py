"""Processing status only; never grants publication, ranking or execution."""

def governed_run_status(*, total: int, completed: int, held: int, rejected: int,
                        pending: int = 0, unresolved: int = 0) -> str:
    counts = (total, completed, held, rejected, pending, unresolved)
    if any(type(value) is not int or value < 0 for value in counts):
        return "BLOCKED"
    if total == 0 or completed + held + rejected + pending != total:
        return "BLOCKED"
    if pending:
        return "RUNNING"
    if unresolved:
        return "BLOCKED"
    if held == 0:
        return "COMPLETE"
    return "DEGRADED" if completed else "BLOCKED"
