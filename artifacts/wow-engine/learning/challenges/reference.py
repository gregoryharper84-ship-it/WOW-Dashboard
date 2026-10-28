"""Offline pedagogical reference behavior, NOT a production fix or runtime authority."""


def select_adoption_source(sources: list[dict], active_wip: int, *, cap: int = 3, cursor: int = 0) -> int | None:
    if type(active_wip) is not int or type(cap) is not int or active_wip < 0 or cap <= 0 or active_wip >= cap or not isinstance(sources, list):
        return None
    if type(cursor) is not int or cursor < 0 or not sources:
        return None
    for offset in range(len(sources)):
        row = sources[(cursor + offset) % len(sources)]
        if not isinstance(row, dict):
            continue
        # Unknown mergeability must be proven separately; never assume safe.
        if row.get("mergeable") is True and type(row.get("number")) is int and row["number"] > 0:
            return row["number"]
    return None


def authenticated_qa_reentry(pr: dict, check: dict, review: dict, reservation: dict) -> bool:
    if not all(isinstance(v, dict) for v in (pr, check, review, reservation)):
        return False
    head = pr.get("head_sha")
    if not isinstance(head, str) or len(head) != 40 or any(c not in "0123456789abcdef" for c in head):
        return False
    if pr.get("author_is_owner") is not False or pr.get("native_ci_green") is not True:
        return False
    if check.get("app_id") != 5245832 or check.get("head_sha") != head or check.get("conclusion") != "failure":
        return False
    if review.get("state") != "APPROVED" or review.get("head_sha") != head or review.get("authenticated") is not True:
        return False
    if not isinstance(review.get("submitted_at"), int) or not isinstance(check.get("completed_at"), int) or review["submitted_at"] <= check["completed_at"]:
        return False
    if review.get("actor") in (None, "", pr.get("author")):
        return False
    return (
        reservation.get("signed") is True
        and type(reservation.get("previous_count")) is int
        and reservation["previous_count"] == 1
        and reservation.get("previous_terminal_failed") is True
        and reservation.get("ambiguous_ack") is False
        and reservation.get("competing_writer") is False
    )


def safe_release(reservation: dict, terminal: dict, active_reservations: list[dict]) -> bool:
    if not isinstance(reservation, dict) or not isinstance(terminal, dict) or not isinstance(active_reservations, list):
        return False
    rid = reservation.get("comment_id")
    if type(rid) is not int or rid <= 0 or reservation.get("hold") is not True or reservation.get("released") is not False:
        return False
    if not isinstance(reservation.get("source_sha"), str) or len(reservation["source_sha"]) != 40 or any(c not in "0123456789abcdef" for c in reservation["source_sha"]):
        return False
    if terminal.get("comment_id") != rid or terminal.get("agent_id") != reservation.get("agent_id") or terminal.get("run_id") != reservation.get("run_id"):
        return False
    if terminal.get("source_sha") != reservation["source_sha"] or terminal.get("original_digest") != reservation.get("digest"):
        return False
    if terminal.get("authenticated") is not True or terminal.get("status") != "FINISHED" or terminal.get("idle") is not True:
        return False
    # Any malformed ledger row can conceal a competing writer; fail closed.
    if any(
        not isinstance(entry, dict)
        or type(entry.get("comment_id")) is not int
        or entry["comment_id"] <= 0
        or type(entry.get("hold")) is not bool
        for entry in active_reservations
    ):
        return False
    matched = [r for r in active_reservations if isinstance(r, dict) and r.get("comment_id") == rid and r.get("hold") is True]
    if len(matched) != 1 or matched[0] != reservation:
        return False
    if any(isinstance(r, dict) and r.get("hold") is True and r.get("comment_id") != rid for r in active_reservations):
        return False
    return True
