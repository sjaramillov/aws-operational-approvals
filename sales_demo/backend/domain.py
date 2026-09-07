"""Modelo de dominio pequeño y explícito para el piloto comercial."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping


APPLICATION_ID_RE = re.compile(r"^app_[0-9a-f]{32}$")
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._~:+/=\-]{16,128}$")
REASON_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
MANAGER_REASON_CODES = {
    "APPROVE": frozenset({"CAPACITY_CONFIRMED", "POLICY_EXCEPTION_APPROVED"}),
    "REJECT": frozenset({"CAPACITY_NOT_AVAILABLE", "INCOMPLETE_COMMERCIAL_CASE"}),
}


class Role(StrEnum):
    CUSTOMER = "CUSTOMER"
    MANAGER = "MANAGER"


class Decision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class ApplicationStatus(StrEnum):
    PENDING_MANAGER = "PENDING_MANAGER"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CONTRACT_ACTIVE = "CONTRACT_ACTIVE"
    EXPIRED = "EXPIRED"


class PilotStatus(StrEnum):
    PREPARED = "PREPARED"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"


class AuditAction(StrEnum):
    APPLICATION_SUBMITTED = "APPLICATION_SUBMITTED"
    MANAGER_APPROVAL_REQUESTED = "MANAGER_APPROVAL_REQUESTED"
    MANAGER_DECISION_RECORDED = "MANAGER_DECISION_RECORDED"
    CONTRACT_ACTIVATED = "CONTRACT_ACTIVATED"
    APPLICATION_REJECTED = "APPLICATION_REJECTED"


@dataclass(frozen=True, slots=True)
class Identity:
    """Identidad resuelta server-side desde el ``sub`` de Cognito."""

    subject: str
    tenant_id: str
    role: Role
    display_name: str

    @property
    def actor_id(self) -> str:
        return f"actor_{hashlib.sha256(self.subject.encode('utf-8')).hexdigest()[:16]}"

    def public_dict(self) -> dict[str, Any]:
        return {
            "subject": self.actor_id,
            "tenantId": self.tenant_id,
            "role": self.role.value,
            "displayName": self.display_name,
            "syntheticData": True,
        }


@dataclass(frozen=True, slots=True)
class PilotSnapshot:
    status: PilotStatus
    expires_at: datetime | None

    def public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "expiresAt": isoformat(self.expires_at) if self.expires_at is not None else None,
            "syntheticData": True,
            "pii": False,
        }


@dataclass(frozen=True, slots=True)
class AuditEntry:
    action: AuditAction
    actor_id: str
    actor_role: Role
    occurred_at: datetime
    decision: Decision | None = None
    reason_code: str | None = None

    def public_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "action": self.action.value,
            "actorId": self.actor_id,
            "actorRole": self.actor_role.value,
            "occurredAt": isoformat(self.occurred_at),
        }
        if self.decision is not None:
            value["decision"] = self.decision.value
        if self.reason_code is not None:
            value["reasonCode"] = self.reason_code
        return value


@dataclass(slots=True)
class Application:
    application_id: str
    tenant_id: str
    vehicle_count: int
    status: ApplicationStatus
    created_at: datetime
    updated_at: datetime
    audit_trail: list[AuditEntry] = field(default_factory=list)
    decision: Decision | None = None
    reason_code: str | None = None
    contract_id: str | None = None
    plan_activated_at: datetime | None = None

    def public_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "applicationId": self.application_id,
            "tenantId": self.tenant_id,
            "vehicleCount": self.vehicle_count,
            "status": self.status.value,
            "createdAt": isoformat(self.created_at),
            "updatedAt": isoformat(self.updated_at),
            "auditTrail": [entry.public_dict() for entry in self.audit_trail],
        }
        if self.decision is not None:
            value["decision"] = self.decision.value
        if self.reason_code is not None:
            value["reasonCode"] = self.reason_code
        if self.contract_id is not None:
            value["contractId"] = self.contract_id
        if self.plan_activated_at is not None:
            value["planActivatedAt"] = isoformat(self.plan_activated_at)
        return value


@dataclass(frozen=True, slots=True)
class PlanPlus:
    tenant_id: str
    status: str = "INACTIVE"
    application_id: str | None = None
    contract_id: str | None = None
    activated_at: datetime | None = None

    def public_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"status": self.status}
        if self.application_id is not None:
            value["applicationId"] = self.application_id
        if self.contract_id is not None:
            value["contractId"] = self.contract_id
        if self.activated_at is not None:
            value["activatedAt"] = isoformat(self.activated_at)
        return value


@dataclass(frozen=True, slots=True)
class CreateApplicationResult:
    application: Application
    replayed: bool


@dataclass(frozen=True, slots=True)
class ClaimedApproval:
    application: Application
    encrypted_token: str
    claim_id: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("datetime must include a timezone")
    return parsed.astimezone(timezone.utc)


def new_application_id() -> str:
    return f"app_{uuid.uuid4().hex}"


def new_contract_id(application_id: str) -> str:
    digest = hashlib.sha256(f"contract:{application_id}".encode("utf-8")).hexdigest()
    return f"ctr_{digest[:24]}"


def require_application_id(value: str) -> str:
    if not APPLICATION_ID_RE.fullmatch(value):
        raise ValueError("invalid application id")
    return value


def require_idempotency_key(value: str | None) -> str:
    candidate = (value or "").strip()
    if not IDEMPOTENCY_KEY_RE.fullmatch(candidate):
        raise ValueError("Idempotency-Key must contain 16-128 safe characters")
    return candidate


def require_vehicle_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("vehicleCount must be an integer")
    if not 1 <= value <= 10_000:
        raise ValueError("vehicleCount must be between 1 and 10000")
    return value


def require_decision(value: Any) -> Decision:
    try:
        return Decision(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("decision must be APPROVE or REJECT") from exc


def require_reason_code(value: Any, *, decision: Decision) -> str:
    if not isinstance(value, str) or not REASON_CODE_RE.fullmatch(value.strip()):
        raise ValueError("reasonCode must be 3-64 uppercase letters, numbers or underscores")
    reason = value.strip()
    allowed = MANAGER_REASON_CODES[decision.value]
    if reason not in allowed:
        raise ValueError(
            f"reasonCode is not allowed for {decision.value}; expected one of "
            + ", ".join(sorted(allowed))
        )
    return reason


def body_object(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("request body must be a JSON object")
    return value
