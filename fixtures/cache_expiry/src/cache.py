from src.clock import is_fresh


def read_cached(cache, key, now):
    entry = cache.get(key)
    if entry is None:
        return None
    # BUG: stale entries are returned and fresh entries are treated as misses.
    if not is_fresh(entry["expires_at"], now):
        return entry["value"]
    return None
