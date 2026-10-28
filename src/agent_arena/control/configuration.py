"""Connection settings shared by foreground and login-service startup."""

from urllib.parse import urlsplit


def validate_origin(origin):
    value = urlsplit(origin)
    if (
        value.scheme not in {"http", "https"}
        or not value.hostname
        or value.username
        or value.password
        or value.path not in {"", "/"}
        or value.query
        or value.fragment
    ):
        raise ValueError("Origin must be a single HTTP(S) origin without credentials or a path")
    if value.scheme == "http" and value.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Remote devices require an HTTPS origin")
    # Accessing port also validates malformed/non-numeric port strings.
    if value.port == 0:
        raise ValueError("Origin port must be nonzero")
    return origin.rstrip("/")
