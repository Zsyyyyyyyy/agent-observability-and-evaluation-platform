"""Small helpers for the configuration inheritance fixture."""


def parent_name(profile):
    return profile.get("extends")
