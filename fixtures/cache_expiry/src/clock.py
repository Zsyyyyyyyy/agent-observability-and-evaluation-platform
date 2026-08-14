def is_fresh(expires_at, now):
    """An entry expires exactly at its expiry timestamp."""

    return now < expires_at
