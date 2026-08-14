"""Return one deterministic page from a revisioned record list."""

from src.cursor import decode_cursor, encode_cursor


def list_page(records, revision, limit, cursor=None):
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("limit must be a positive integer")

    offset = 0
    if cursor is not None:
        cursor_revision, offset = decode_cursor(cursor)
        # BUG: cursors from a different dataset revision are accepted.
        del cursor_revision

    items = records[offset:offset + limit]
    next_offset = offset + len(items)
    next_cursor = encode_cursor(revision, next_offset) if next_offset < len(records) else None
    return {"items": items, "next_cursor": next_cursor}
