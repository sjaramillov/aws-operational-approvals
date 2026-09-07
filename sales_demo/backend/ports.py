"""Puertos que mantienen el dominio independiente de boto3."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .domain import (
    Application,
    ClaimedApproval,
    CreateApplicationResult,
    Decision,
    Identity,
    PilotSnapshot,
    PlanPlus,
    Role,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class Repository(Protocol):
    def pilot_snapshot(self, now: datetime) -> PilotSnapshot: ...

    def resolve_identity(self, subject: str) -> Identity | None: ...

    def create_application(
        self,
        *,
        tenant_id: str,
        application_id: str,
        vehicle_count: int,
        idempotency_key: str,
        actor: Identity,
        now: datetime,
        daily_limit: int,
    ) -> CreateApplicationResult: ...

    def get_application(self, tenant_id: str, application_id: str) -> Application | None: ...

    def list_applications(self, tenant_id: str) -> list[Application]: ...

    def list_pending_approvals(self, tenant_id: str) -> list[Application]: ...

    def get_plan_plus(self, tenant_id: str) -> PlanPlus: ...

    def store_approval_token(
        self,
        *,
        tenant_id: str,
        application_id: str,
        encrypted_token: str,
        token_fingerprint: str,
        now: datetime,
    ) -> Application: ...

    def claim_approval_decision(
        self,
        *,
        tenant_id: str,
        application_id: str,
        actor_id: str,
        actor_role: Role,
        decision: Decision,
        reason_code: str,
        claim_id: str,
        claim_expires_at: datetime,
        now: datetime,
    ) -> ClaimedApproval: ...

    def activate_plan(
        self,
        *,
        tenant_id: str,
        application_id: str,
        actor_id: str,
        actor_role: str,
        reason_code: str,
        now: datetime,
    ) -> Application: ...

    def reject_application(
        self,
        *,
        tenant_id: str,
        application_id: str,
        actor_id: str,
        actor_role: str,
        reason_code: str,
        now: datetime,
    ) -> Application: ...

    def expire_pilot(
        self, *, expected_expires_at: datetime, now: datetime
    ) -> bool: ...


class WorkflowStarter(Protocol):
    def start_application(self, *, tenant_id: str, application_id: str, vehicle_count: int) -> None: ...


class DecisionDispatcher(Protocol):
    def dispatch(
        self,
        *,
        tenant_id: str,
        application_id: str,
        actor: Identity,
        decision: Decision,
        reason_code: str,
    ) -> dict[str, object]: ...


class TokenVault(Protocol):
    def encrypt(self, token: str, *, tenant_id: str, application_id: str) -> str: ...

    def decrypt(self, encrypted_token: str, *, tenant_id: str, application_id: str) -> str: ...


class TaskCallback(Protocol):
    def send_success(self, *, token: str, output: dict[str, str]) -> None: ...


class UserDisabler(Protocol):
    def disable_allowlisted_users(self) -> int: ...
