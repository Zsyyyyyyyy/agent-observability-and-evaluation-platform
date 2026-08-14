"""Encode and decode a small opaque pagination cursor."""


def encode_cursor(revision, offset):
    return f"{revision}:{offset}"


def decode_cursor(token):
    try:
        revision, offset = token.split(":", 1)
        return revision, int(offset)
    except (AttributeError, ValueError):
        raise ValueError("invalid cursor")
