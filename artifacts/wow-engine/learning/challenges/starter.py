"""Deliberately flawed offline exercises. NEVER import this into production."""


def select_adoption_source(sources: list[dict], active_wip: int, *, cap: int = 3, cursor: int = 0) -> int | None:
    """Honor WIP, conflict skip and fair rotation. BUG: always picks the first source."""
    if active_wip >= cap:
        return None
    return sources[0]["number"] if sources else None


def authenticated_qa_reentry(pr: dict, check: dict, review: dict, reservation: dict) -> bool:
    """Safe single replay after new authenticated prerequisite. BUG: stale failure is terminal."""
    return check.get("conclusion") == "success"


def safe_release(reservation: dict, terminal: dict, active_reservations: list[dict]) -> bool:
    """Release a uniquely authenticated finished writer. BUG: ignores competing writers."""
    return terminal.get("status") == "FINISHED"
