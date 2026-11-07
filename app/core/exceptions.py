"""
app/core/exceptions.py

Custom domain exceptions.

Having explicit exception types (instead of raw ValueError / RuntimeError) lets
callers handle errors precisely and makes stack-traces self-documenting.
"""


class BulkProcessorError(Exception):
    """Base exception for all application-level errors."""


class CSVValidationError(BulkProcessorError):
    """Raised when the uploaded CSV file is invalid (format, size, columns)."""


class HospitalAPIError(BulkProcessorError):
    """Raised when the external Hospital Directory API returns an error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class BatchNotFoundError(BulkProcessorError):
    """Raised when a requested batch ID does not exist in our in-memory store."""


class BatchActivationError(BulkProcessorError):
    """Raised when activating a batch on the external API fails."""

