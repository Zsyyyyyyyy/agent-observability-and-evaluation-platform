"""Resolve named configuration profiles."""

from src.catalog import parent_name


def resolve_settings(profiles, name):
    """Return the settings for one profile.

    Profiles may declare an ``extends`` parent. Child settings should take
    precedence over inherited settings.
    """

    profile = profiles[name]
    parent = parent_name(profile)
    if parent:
        # BUG: inheritance currently ignores every parent profile.
        return dict(profile.get("settings", {}))
    return dict(profile.get("settings", {}))
