"""FOSS stub. Error types raised by Max AI tools."""


class MaxToolError(Exception):
    """Raised when an AI tool fails non-recoverably. Never raised on FOSS."""


class MaxToolRetryableError(MaxToolError):
    """Raised for transient tool failures that should be retried."""
