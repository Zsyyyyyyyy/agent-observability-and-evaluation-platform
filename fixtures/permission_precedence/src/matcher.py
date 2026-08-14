from fnmatch import fnmatchcase


def matches(patterns, value):
    return any(fnmatchcase(value, pattern) for pattern in patterns)
