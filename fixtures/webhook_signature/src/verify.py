"""Verify a webhook HMAC without exposing the secret."""

import hashlib
import hmac

from src.canonical import canonical_bytes


def signature_for(event, secret):
    return hmac.new(secret.encode("utf-8"), canonical_bytes(event), hashlib.sha256).hexdigest()


def verify_signature(event, signature, secret):
    # BUG: plain equality is not the intended constant-time comparison and
    # malformed values are not rejected explicitly.
    return signature_for(event, secret) == signature
