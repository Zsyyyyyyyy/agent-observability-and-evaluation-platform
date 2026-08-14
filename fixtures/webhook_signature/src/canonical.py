"""Canonicalize JSON-like webhook events before signing."""

import json


def canonical_bytes(event):
    # BUG: insertion order makes logically identical payloads sign differently.
    return json.dumps(event).encode("utf-8")
