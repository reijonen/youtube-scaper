"""Exceptions for the Chrome lifecycle package."""


class ChromeInUseError(Exception):
    """A live Chrome process already holds `data-dir` or `data-dir-template`."""


class HandshakeTimeout(Exception):
    """The extension did not complete its handshake within the startup deadline."""
