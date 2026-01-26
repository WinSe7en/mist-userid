"""Shared utility functions."""


def sanitize_username(username: str) -> str:
    """Mask username for logging (FERPA/privacy compliance).

    Examples:
        jane.q.doe@example.edu -> j***@example.edu
        jsmith@example.edu -> j***@example.edu
        psk_device_name -> p***
    """
    if not username:
        return ""
    if "@" in username:
        local, domain = username.split("@", 1)
        if local:
            return f"{local[0]}***@{domain}"
        return f"***@{domain}"
    # No @ sign (PSK name or simple username)
    if len(username) > 0:
        return f"{username[0]}***"
    return "***"
