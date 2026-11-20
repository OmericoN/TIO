from uuid import UUID


def plan_speaker(
    ranked_host_ids: list[UUID],
    *,
    addressed_host_id: UUID | None = None,
    reply_to_host_id: UUID | None = None,
) -> tuple[list[UUID], UUID | None]:
    if len(set(ranked_host_ids)) != len(ranked_host_ids):
        raise ValueError("Host IDs must be unique.")
    if addressed_host_id is not None and addressed_host_id not in ranked_host_ids:
        raise ValueError("The addressed host is unavailable.")

    roster = ranked_host_ids[:2]
    required_host = addressed_host_id or reply_to_host_id
    if required_host in ranked_host_ids and required_host not in roster:
        roster = [ranked_host_ids[0], required_host]

    next_host = addressed_host_id or next(
        (host_id for host_id in roster if host_id != reply_to_host_id), None
    )
    return roster, next_host
