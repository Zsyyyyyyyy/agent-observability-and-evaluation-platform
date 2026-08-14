from src.schema import CURRENT_VERSION, empty_settings


def load_profile(payload):
    """Load a persisted profile into the current v2 shape."""

    # BUG: version-1 snapshots stored ``name`` instead of ``display_name``.
    return {
        "version": CURRENT_VERSION,
        "id": payload["id"],
        "display_name": payload["display_name"],
        "settings": payload.get("settings", empty_settings()),
    }
