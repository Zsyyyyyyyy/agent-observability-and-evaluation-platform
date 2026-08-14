from src.matcher import matches


def is_allowed(tool_name, policy):
    """Apply a glob-based tool policy with deny taking precedence."""

    # BUG: a broad allow pattern can accidentally bypass an explicit deny.
    if matches(policy.get("allow", []), tool_name):
        return True
    return False
