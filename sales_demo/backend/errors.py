"""Errores de dominio transformables a RFC 9457 Problem Details."""

from __future__ import annotations


class PilotError(Exception):
    status = 500
    code = "INTERNAL_ERROR"
    title = "Internal error"
    public_detail = "The request could not be completed."

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.public_detail)
        self.detail = detail or self.public_detail


class BadRequest(PilotError):
    status = 400
    code = "INVALID_REQUEST"
    title = "Invalid request"


class Unauthorized(PilotError):
    status = 401
    code = "UNAUTHORIZED"
    title = "Authentication required"
    public_detail = "A valid Cognito identity is required."


class Forbidden(PilotError):
    status = 403
    code = "FORBIDDEN"
    title = "Operation not allowed"
    public_detail = "This identity cannot perform the requested operation."


class NotFound(PilotError):
    status = 404
    code = "NOT_FOUND"
    title = "Resource not found"
    public_detail = "The requested resource was not found."


class Conflict(PilotError):
    status = 409
    code = "CONFLICT"
    title = "Request conflict"


class DailyQuotaExceeded(PilotError):
    status = 429
    code = "DAILY_QUOTA_EXCEEDED"
    title = "Daily application quota exceeded"
    public_detail = "This tenant has reached its daily application limit."


class PilotExpired(PilotError):
    status = 410
    code = "PILOT_EXPIRED"
    title = "Pilot expired"
    public_detail = "The synthetic pilot is no longer active."


class PilotNotReady(PilotError):
    status = 503
    code = "PILOT_NOT_READY"
    title = "Pilot not ready"
    public_detail = "The synthetic pilot has not been staged for use."


class DependencyFailure(PilotError):
    status = 503
    code = "DEPENDENCY_FAILURE"
    title = "Temporary dependency failure"
    public_detail = "A required service did not accept the operation."
