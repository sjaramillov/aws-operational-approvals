"""Adaptadores deterministas solo para tests y demo local.

No son una alternativa al cifrado KMS ni a las condiciones DynamoDB. Mantienen
la misma semántica de puertos para ejecutar contratos/E2E sin una cuenta AWS.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from .domain import (
    Application,
    ApplicationStatus,
    AuditAction,
    AuditEntry,
    ClaimedApproval,
    CreateApplicationResult,
    Decision,
    Identity,
    PilotSnapshot,
    PilotStatus,
    PlanPlus,
    Role,
    new_contract_id,
)
from .errors import Conflict, DailyQuotaExceeded, NotFound, PilotExpired, PilotNotReady
from .service import ApiService, WorkerService


@dataclass(slots=True)
class MutableClock:
    value: datetime

    def now(self) -> datetime:
        return self.value

    def advance(self, **delta) -> None:
        self.value += timedelta(**delta)


@dataclass(slots=True)
class _ApprovalSecret:
    encrypted_token: str
    token_fingerprint: str
    state: str = "READY"
    claim_id: str | None = None
    claim_expires_at: datetime | None = None
    actor_id: str | None = None
    actor_role: Role | None = None
    decision: Decision | None = None
    reason_code: str | None = None


class InMemoryRepository:
    def __init__(
        self,
        *,
        expires_at: datetime | None,
        identities: list[Identity],
    ) -> None:
        self.expires_at = expires_at
        self.pilot_status = PilotStatus.ACTIVE if expires_at is not None else PilotStatus.PREPARED
        self.identities = {identity.subject: identity for identity in identities}
        self.applications: dict[tuple[str, str], Application] = {}
        self.idempotency: dict[tuple[str, str], tuple[str, int]] = {}
        self.daily_counts: dict[tuple[str, str], int] = {}
        self.plans: dict[str, PlanPlus] = {}
        self.approval_secrets: dict[tuple[str, str], _ApprovalSecret] = {}
        self._lock = threading.RLock()

    def pilot_snapshot(self, now: datetime) -> PilotSnapshot:
        if self.pilot_status is PilotStatus.PREPARED or self.expires_at is None:
            return PilotSnapshot(status=PilotStatus.PREPARED, expires_at=None)
        status = self.pilot_status
        if status is PilotStatus.ACTIVE and now >= self.expires_at:
            status = PilotStatus.EXPIRED
        return PilotSnapshot(status=status, expires_at=self.expires_at)

    def resolve_identity(self, subject: str) -> Identity | None:
        identity = self.identities.get(subject)
        return copy.deepcopy(identity)

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
    ) -> CreateApplicationResult:
        with self._lock:
            self._require_active(now)
            idem = (tenant_id, hashlib.sha256(idempotency_key.encode()).hexdigest())
            if idem in self.idempotency:
                existing_id, existing_vehicle_count = self.idempotency[idem]
                if existing_vehicle_count != vehicle_count:
                    raise Conflict("Idempotency-Key was already used with different input.")
                return CreateApplicationResult(
                    copy.deepcopy(self.applications[(tenant_id, existing_id)]), True
                )
            day_key = (tenant_id, now.astimezone(timezone.utc).date().isoformat())
            if self.daily_counts.get(day_key, 0) >= daily_limit:
                raise DailyQuotaExceeded()
            status = (
                ApplicationStatus.APPROVED
                if vehicle_count <= 50
                else ApplicationStatus.PENDING_MANAGER
            )
            application = Application(
                application_id=application_id,
                tenant_id=tenant_id,
                vehicle_count=vehicle_count,
                status=status,
                created_at=now,
                updated_at=now,
                decision=Decision.APPROVE if vehicle_count <= 50 else None,
                reason_code="AUTO_APPROVED" if vehicle_count <= 50 else None,
                audit_trail=[
                    AuditEntry(
                        action=AuditAction.APPLICATION_SUBMITTED,
                        actor_id=actor.actor_id,
                        actor_role=actor.role,
                        occurred_at=now,
                    )
                ],
            )
            self.applications[(tenant_id, application_id)] = application
            self.idempotency[idem] = (application_id, vehicle_count)
            self.daily_counts[day_key] = self.daily_counts.get(day_key, 0) + 1
            return CreateApplicationResult(copy.deepcopy(application), False)

    def get_application(self, tenant_id: str, application_id: str) -> Application | None:
        value = self.applications.get((tenant_id, application_id))
        return copy.deepcopy(value)

    def list_applications(self, tenant_id: str) -> list[Application]:
        values = [
            copy.deepcopy(value)
            for (tenant, _), value in self.applications.items()
            if tenant == tenant_id
        ]
        return sorted(values, key=lambda value: value.created_at, reverse=True)

    def list_pending_approvals(self, tenant_id: str) -> list[Application]:
        return [
            value
            for value in self.list_applications(tenant_id)
            if value.status is ApplicationStatus.PENDING_MANAGER
            and (tenant_id, value.application_id) in self.approval_secrets
            and self.approval_secrets[(tenant_id, value.application_id)].state
            in {"READY", "CLAIMED"}
        ]

    def get_plan_plus(self, tenant_id: str) -> PlanPlus:
        return copy.deepcopy(self.plans.get(tenant_id, PlanPlus(tenant_id=tenant_id)))

    def store_approval_token(
        self,
        *,
        tenant_id: str,
        application_id: str,
        encrypted_token: str,
        token_fingerprint: str,
        now: datetime,
    ) -> Application:
        with self._lock:
            self._require_active(now)
            key = (tenant_id, application_id)
            application = self.applications.get(key)
            if application is None:
                raise NotFound()
            if application.status is not ApplicationStatus.PENDING_MANAGER:
                raise Conflict("Application is not pending Manager approval.")
            if key in self.approval_secrets:
                secret = self.approval_secrets[key]
                if secret.state == "READY":
                    # Un Retry de Step Functions puede emitir un token nuevo. Solo
                    # se reemplaza antes de que exista un claim humano.
                    secret.encrypted_token = encrypted_token
                    secret.token_fingerprint = token_fingerprint
                    application.updated_at = now
                    return copy.deepcopy(application)
                if secret.token_fingerprint != token_fingerprint:
                    raise Conflict("A claimed callback token cannot be replaced.")
                return copy.deepcopy(application)
            self.approval_secrets[key] = _ApprovalSecret(
                encrypted_token=encrypted_token,
                token_fingerprint=token_fingerprint,
            )
            application.updated_at = now
            application.audit_trail.append(
                AuditEntry(
                    action=AuditAction.MANAGER_APPROVAL_REQUESTED,
                    actor_id="actor_system",
                    actor_role=Role.MANAGER,
                    occurred_at=now,
                )
            )
            return copy.deepcopy(application)

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
    ) -> ClaimedApproval:
        with self._lock:
            self._require_active(now)
            key = (tenant_id, application_id)
            application = self.applications.get(key)
            secret = self.approval_secrets.get(key)
            if application is None:
                raise NotFound()
            if application.status is not ApplicationStatus.PENDING_MANAGER or secret is None:
                raise Conflict("Approval callback is not ready.")
            if secret.state == "CLAIMED" and secret.claim_expires_at and secret.claim_expires_at > now:
                raise Conflict("Another Manager decision is in progress.")
            if secret.state == "CLAIMED" and (
                secret.actor_id != actor_id
                or secret.actor_role is not actor_role
                or secret.decision is not decision
                or secret.reason_code != reason_code
            ):
                raise Conflict("An expired claim can only replay the same Manager decision.")
            if secret.state not in {"READY", "CLAIMED"}:
                raise Conflict("Approval callback is not ready.")
            secret.state = "CLAIMED"
            secret.claim_id = claim_id
            secret.claim_expires_at = claim_expires_at
            secret.actor_id = actor_id
            secret.actor_role = actor_role
            secret.decision = decision
            secret.reason_code = reason_code
            return ClaimedApproval(
                application=copy.deepcopy(application),
                encrypted_token=secret.encrypted_token,
                claim_id=claim_id,
            )

    def activate_plan(
        self,
        *,
        tenant_id: str,
        application_id: str,
        actor_id: str,
        actor_role: str,
        reason_code: str,
        now: datetime,
    ) -> Application:
        with self._lock:
            self._require_active(now)
            key = (tenant_id, application_id)
            application = self.applications.get(key)
            if application is None:
                raise NotFound()
            if application.status is ApplicationStatus.CONTRACT_ACTIVE:
                return copy.deepcopy(application)
            if application.vehicle_count > 50:
                secret = self.approval_secrets.get(key)
                if (
                    not secret
                    or secret.state != "CLAIMED"
                    or secret.decision is not Decision.APPROVE
                    or secret.actor_id != actor_id
                    or secret.actor_role is not Role(actor_role)
                    or secret.reason_code != reason_code
                ):
                    raise Conflict("A successful Manager callback is required.")
                application.decision = Decision.APPROVE
                application.audit_trail.append(
                    AuditEntry(
                        action=AuditAction.MANAGER_DECISION_RECORDED,
                        actor_id=secret.actor_id,
                        actor_role=secret.actor_role,
                        occurred_at=now,
                        decision=secret.decision,
                        reason_code=secret.reason_code,
                    )
                )
            contract_id = new_contract_id(application_id)
            application.status = ApplicationStatus.CONTRACT_ACTIVE
            application.reason_code = reason_code
            application.contract_id = contract_id
            application.plan_activated_at = now
            application.updated_at = now
            application.audit_trail.append(
                AuditEntry(
                    action=AuditAction.CONTRACT_ACTIVATED,
                    actor_id=actor_id,
                    actor_role=Role(actor_role),
                    occurred_at=now,
                    decision=Decision.APPROVE,
                    reason_code=reason_code,
                )
            )
            self.plans[tenant_id] = PlanPlus(
                tenant_id=tenant_id,
                status="ACTIVE",
                application_id=application_id,
                contract_id=contract_id,
                activated_at=now,
            )
            self.approval_secrets.pop(key, None)
            return copy.deepcopy(application)

    def reject_application(
        self,
        *,
        tenant_id: str,
        application_id: str,
        actor_id: str,
        actor_role: str,
        reason_code: str,
        now: datetime,
    ) -> Application:
        with self._lock:
            self._require_active(now)
            key = (tenant_id, application_id)
            application = self.applications.get(key)
            if application is None:
                raise NotFound()
            if application.status is ApplicationStatus.REJECTED:
                return copy.deepcopy(application)
            if application.vehicle_count <= 50:
                raise Conflict("Automatically approved applications cannot be rejected.")
            secret = self.approval_secrets.get(key)
            system_failure = actor_id == "actor_system" and reason_code in {
                "MANAGER_APPROVAL_TIMEOUT",
            }
            if not system_failure and (
                not secret
                or secret.state != "CLAIMED"
                or secret.decision is not Decision.REJECT
                or secret.actor_id != actor_id
                or secret.actor_role is not Role(actor_role)
                or secret.reason_code != reason_code
            ):
                raise Conflict("A successful Manager rejection callback is required.")
            if not system_failure:
                application.audit_trail.append(
                    AuditEntry(
                        action=AuditAction.MANAGER_DECISION_RECORDED,
                        actor_id=secret.actor_id,
                        actor_role=secret.actor_role,
                        occurred_at=now,
                        decision=secret.decision,
                        reason_code=secret.reason_code,
                    )
                )
            application.status = ApplicationStatus.REJECTED
            application.decision = Decision.REJECT
            application.reason_code = reason_code
            application.updated_at = now
            application.audit_trail.append(
                AuditEntry(
                    action=AuditAction.APPLICATION_REJECTED,
                    actor_id=actor_id,
                    actor_role=Role(actor_role),
                    occurred_at=now,
                    decision=Decision.REJECT,
                    reason_code=reason_code,
                )
            )
            self.approval_secrets.pop(key, None)
            return copy.deepcopy(application)

    def expire_pilot(self, *, expected_expires_at: datetime, now: datetime) -> bool:
        with self._lock:
            if self.expires_at is None or self.pilot_status is PilotStatus.PREPARED:
                raise Conflict("Pilot has not been staged for expiration.")
            if expected_expires_at.astimezone(timezone.utc) != self.expires_at.astimezone(
                timezone.utc
            ):
                raise Conflict("Scheduled expiration does not match the durable pilot contract.")
            if now < self.expires_at:
                raise Conflict("Pilot cannot expire before its durable expires_at.")
            if self.pilot_status is PilotStatus.EXPIRED:
                return False
            self.pilot_status = PilotStatus.EXPIRED
            return True

    def _require_active(self, now: datetime) -> None:
        snapshot = self.pilot_snapshot(now)
        if snapshot.status is PilotStatus.PREPARED:
            raise PilotNotReady()
        if snapshot.status is not PilotStatus.ACTIVE:
            raise PilotExpired()


class LocalTokenVault:
    """Envelope reversible local con MAC; producción usa KMS EncryptionContext."""

    def __init__(self, key: bytes | None = None) -> None:
        self._key = key or secrets.token_bytes(32)

    def encrypt(self, token: str, *, tenant_id: str, application_id: str) -> str:
        context = f"{tenant_id}\0{application_id}".encode()
        nonce = secrets.token_bytes(16)
        plaintext = token.encode()
        stream = _keystream(self._key, nonce + context, len(plaintext))
        ciphertext = bytes(a ^ b for a, b in zip(plaintext, stream, strict=True))
        mac = hmac.new(self._key, context + nonce + ciphertext, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(nonce + mac + ciphertext).decode()

    def decrypt(self, encrypted_token: str, *, tenant_id: str, application_id: str) -> str:
        envelope = base64.urlsafe_b64decode(encrypted_token.encode())
        nonce, mac, ciphertext = envelope[:16], envelope[16:48], envelope[48:]
        context = f"{tenant_id}\0{application_id}".encode()
        expected = hmac.new(self._key, context + nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected):
            raise ValueError("local token envelope context mismatch")
        stream = _keystream(self._key, nonce + context, len(ciphertext))
        return bytes(a ^ b for a, b in zip(ciphertext, stream, strict=True)).decode()


def _keystream(key: bytes, seed: bytes, size: int) -> bytes:
    blocks = bytearray()
    counter = 0
    while len(blocks) < size:
        blocks.extend(hmac.new(key, seed + counter.to_bytes(4, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(blocks[:size])


class LocalTaskCallback:
    def __init__(self) -> None:
        self.on_success: Callable[[dict[str, str]], None] | None = None
        self.calls: list[dict[str, str]] = []
        self._delivered = 0
        self._lock = threading.RLock()

    def send_success(self, *, token: str, output: dict[str, str]) -> None:
        # El token se usa para validar el callback, pero jamás se conserva.
        if not token.startswith("local-task-token-"):
            raise ValueError("unknown local callback token")
        with self._lock:
            self.calls.append(copy.deepcopy(output))

    def flush(self) -> None:
        while True:
            with self._lock:
                if self._delivered >= len(self.calls):
                    return
                output = self.calls[self._delivered]
                self._delivered += 1
            if self.on_success:
                self.on_success(output)


class LocalUserDisabler:
    def __init__(self) -> None:
        self.usernames = ("customer-a", "manager-a", "customer-b", "manager-b")
        self.disabled: set[str] = set()

    def disable_allowlisted_users(self) -> int:
        self.disabled.update(self.usernames)
        return len(self.disabled)


class LocalWorkflow:
    def __init__(self, worker: WorkerService, callback: LocalTaskCallback) -> None:
        self.worker = worker
        self.callback = callback
        self.started: set[str] = set()
        self.current: dict[str, str] = {}

    def start_application(self, *, tenant_id: str, application_id: str, vehicle_count: int) -> None:
        if application_id in self.started:
            return
        self.started.add(application_id)
        if vehicle_count <= 50:
            self.worker.finalize_approved(
                tenant_id=tenant_id,
                application_id=application_id,
                actor_id="actor_system",
                actor_role=Role.MANAGER.value,
                reason_code="AUTO_APPROVED",
            )
            return
        self.current[application_id] = tenant_id
        self.worker.capture_approval_token(
            tenant_id=tenant_id,
            application_id=application_id,
            task_token=f"local-task-token-{application_id}-{secrets.token_hex(16)}",
        )

    def complete(self, application_id: str, output: dict[str, str]) -> None:
        tenant_id = self.current[application_id]
        if output["decision"] == Decision.APPROVE.value:
            self.worker.finalize_approved(
                tenant_id=tenant_id,
                application_id=application_id,
                actor_id=output["actorId"],
                actor_role=output["actorRole"],
                reason_code=output["reasonCode"],
            )
        else:
            self.worker.finalize_rejected(
                tenant_id=tenant_id,
                application_id=application_id,
                actor_id=output["actorId"],
                actor_role=output["actorRole"],
                reason_code=output["reasonCode"],
            )


class LocalDecisionDispatcher:
    def __init__(
        self,
        worker: WorkerService,
        callback: LocalTaskCallback,
        *,
        auto_finalize: bool,
    ) -> None:
        self.worker = worker
        self.callback = callback
        self.auto_finalize = auto_finalize

    def dispatch(
        self,
        *,
        tenant_id: str,
        application_id: str,
        actor: Identity,
        decision: Decision,
        reason_code: str,
    ) -> dict[str, object]:
        accepted = self.worker.complete_manager_decision(
            tenant_id=tenant_id,
            application_id=application_id,
            actor_id=actor.actor_id,
            actor_role=actor.role.value,
            decision=decision,
            reason_code=reason_code,
        )
        if self.auto_finalize:
            timer = threading.Timer(0.01, self.callback.flush)
            timer.daemon = True
            timer.start()
        return accepted


@dataclass(slots=True)
class LocalBundle:
    api: ApiService
    worker: WorkerService
    repository: InMemoryRepository
    clock: MutableClock
    callback: LocalTaskCallback
    user_disabler: LocalUserDisabler


def build_local_bundle(
    now: datetime | None = None, *, auto_finalize: bool = False
) -> LocalBundle:
    clock = MutableClock(now or datetime(2026, 8, 26, 16, 0, tzinfo=timezone.utc))
    identities = [
        Identity("customer-a", "tenant-a", Role.CUSTOMER, "Cliente A"),
        Identity("manager-a", "tenant-a", Role.MANAGER, "Manager A"),
        Identity("customer-b", "tenant-b", Role.CUSTOMER, "Cliente B"),
        Identity("manager-b", "tenant-b", Role.MANAGER, "Manager B"),
    ]
    repository = InMemoryRepository(expires_at=clock.now() + timedelta(hours=192), identities=identities)
    callback = LocalTaskCallback()
    user_disabler = LocalUserDisabler()
    worker = WorkerService(
        repository=repository,
        token_vault=LocalTokenVault(hashlib.sha256(b"approvals-local-test-only").digest()),
        task_callback=callback,
        user_disabler=user_disabler,
        clock=clock,
    )
    workflow = LocalWorkflow(worker, callback)
    callback.on_success = lambda output: workflow.complete(output["applicationId"], output)
    api = ApiService(
        repository=repository,
        workflow=workflow,
        decision_dispatcher=LocalDecisionDispatcher(
            worker,
            callback,
            auto_finalize=auto_finalize,
        ),
        clock=clock,
    )
    return LocalBundle(api, worker, repository, clock, callback, user_disabler)
