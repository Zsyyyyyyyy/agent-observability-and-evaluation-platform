from src.records import parse_record


def process_batch(rows):
    """Return accepted records and individual rejected rows."""

    # BUG: one malformed row aborts the complete batch.
    return {"accepted": [parse_record(row) for row in rows], "rejected": []}
