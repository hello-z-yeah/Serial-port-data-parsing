"""Application-specific exception hierarchy.

Domain exceptions also inherit the closest historical built-in exception where
that preserves third-party/test compatibility, but GUI classification relies on
``UserCorrectableError`` rather than broadly accepting every built-in error.
"""
from __future__ import annotations


class AppError(Exception):
    """Base class for application-defined failures."""


class UserCorrectableError(AppError):
    """A condition the user can correct without changing program code."""


class ValidationError(UserCorrectableError, ValueError):
    """Invalid value or malformed user-supplied content."""


class AttributeValidationError(ValidationError):
    """Attribute value, range, enum, length, or permission violation."""


class ProductConfigError(ValidationError):
    """Invalid imported product or protocol configuration."""


class CommandValidationError(ValidationError):
    """Invalid command payload or command-library entry."""


class EnvironmentStateError(UserCorrectableError, RuntimeError):
    """The current console/runtime environment cannot perform an operation."""


class SerialOperationError(UserCorrectableError, OSError):
    """Serial port or driver operation failed."""


class SerialStateError(UserCorrectableError, RuntimeError):
    """Operation requested while the serial connection is unavailable."""


class TxQueueFullError(SerialOperationError):
    """The asynchronous transmit queue cannot accept more frames."""


class StorageOperationError(UserCorrectableError, OSError):
    """Raw data/log/session storage failed."""


class StorageQueueFullError(StorageOperationError):
    """Raw-data writer queue is full and records had to be dropped."""


class SnapshotError(StorageOperationError):
    """Session snapshot could not be saved or loaded safely."""
