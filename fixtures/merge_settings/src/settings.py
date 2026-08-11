DEFAULTS = {"retries": 3, "timeout": 30}


def merge_settings(overrides):
    merged = dict(DEFAULTS)
    merged.update(overrides)
    return merged
