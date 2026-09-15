class QuickPRError(Exception):
    """An error that can be shown directly to the user."""


class APIError(QuickPRError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class NetworkError(QuickPRError):
    """A request failed without a definitive GitHub response."""
