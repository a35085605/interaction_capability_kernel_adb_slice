"""ADB server availability errors."""


class AdbServerUnavailableError(RuntimeError):
    """No usable ADB server is currently available to the owning runtime."""


__all__ = ["AdbServerUnavailableError"]
