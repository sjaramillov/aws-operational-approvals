"""Casos de uso del API y del worker/callback.

Las clases de este módulo no importan AWS. Los efectos laterales están detrás de
puertos para que expiración, aislamiento, idempotencia y callback puedan probarse
sin credenciales ni ``boto3``.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Any

from .domain import (
    Application,
    ApplicationStatus,
    Decision,
    Identity,
    PilotSnapshot,
    PilotStatus,
    Role,
    body_object,
    new_application_id,
    require_application_id,
    require_decision,
    require_idempotency_key,
    require_reason_code,
    require_vehicle_count,
)
from .errors import (
    BadRequest,
    Conflict,
    DependencyFailure,
    Forbidden,
    NotFound,
    PilotExpired,
    PilotNotReady,
    Unauthorized,
)
from .ports import (
    Clock,
    DecisionDispatcher,
    Repository,
    TaskCallback,
    TokenVault,
    UserDisabler,
    WorkflowStarter,
)


class ApiService:
    """Autoriza y ejecuta los endpoints públicos del piloto."""

    def __init__(
        self,
        *,
        repository: Repository,
        workflow: WorkflowStarter,
        decision_dispatcher: DecisionDispatcher,
        clock: Clock,
        daily_limit: int = 10,
    ) -> None:
        if not 1 <= daily_limit <= 100:
            raise ValueError("daily_limit must be between 1 and 100")
        self._repository = repository
        self._workflow = workflow
        self._decision_dispatcher = decision_dispatcher
        self._clock = clock
        self._daily_limit = daily_limit

    def health(self) -> dict[str, Any]:
        snapshot = self._repository.pilot_snapshot(self._clock.now())
        return snapshot.public_dict()

    def me(self, subject: str | None) -> dict[str, Any]:
        # Read-only identity hydration is intentionally available in PREPARED
        # so all four Cognito sessions can be proven before T0. Every business
        # operation still goes through _active_identity and remains blocked.
        now = self._clock.now()
        snapshot = self._repository.pilot_snapshot(now)
        if snapshot.status is PilotStatus.EXPIRED or (
            snapshot.status is PilotStatus.ACTIVE
            and (snapshot.expires_at is None or now >= snapshot.expires_at)
        ):
            raise PilotExpired()
        if snapshot.status not in {PilotStatus.PREPARED, PilotStatus.ACTIVE}:
            raise PilotNotReady()
        if not subject or not isinstance(subject, str):
            raise Unauthorized()
        identity = self._repository.resolve_identity(subject)
        if identity is None:
            raise Unauthorized()
        return identity.public_dict()

    def create_application(
        self,
        subject: str | None,
        payload: Any,
        idempotency_key: str | None,
    ) -> tuple[dict[str, Any], bool]:
        identity = self._active_identity(subject)
        self._require_role(identity, Role.CUSTOMER)
        try:
            body = body_object(payload)
            if set(body) != {"vehicleCount"}:
                raise ValueError("Only vehicleCount is accepted in the request body")
            vehicle_count = require_vehicle_count(body.get("vehicleCount"))
            key = require_idempotency_key(idempotency_key)
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc

        now = self._clock.now()
        result = self._repository.create_application(
            tenant_id=identity.tenant_id,
            application_id=new_application_id(),
            vehicle_count=vehicle_count,
            idempotency_key=key,
            actor=identity,
            now=now,
            daily_limit=self._daily_limit,
        )

        # El nombre de ejecución es application_id. Ante ExecutionAlreadyExists,
        # el adaptador compara el input y solo acepta RUNNING/SUCCEEDED o redrive
        # explícitamente permitido por AWS; nunca convierte un cierre rojo en éxito.
        if result.application.status not in {
            ApplicationStatus.CONTRACT_ACTIVE,
            ApplicationStatus.REJECTED,
            ApplicationStatus.EXPIRED,
        }:
            try:
                self._workflow.start_application(
                    tenant_id=identity.tenant_id,
                    application_id=result.application.application_id,
                    vehicle_count=result.application.vehicle_count,
                )
            except DependencyFailure:
                raise
            except Exception as exc:  # pragma: no cover - adapter-specific details
                raise DependencyFailure() from exc

        return {
            "application": result.application.public_dict(),
            "replayed": result.replayed,
        }, result.replayed

    def list_applications(self, subject: str | None) -> dict[str, Any]:
        identity = self._active_identity(subject)
        return {
            "items": [
                application.public_dict()
                for application in self._repository.list_applications(identity.tenant_id)
            ]
        }

    def get_application(self, subject: str | None, application_id: str) -> dict[str, Any]:
        identity = self._active_identity(subject)
        application = self._tenant_application(identity, application_id)
        return {"application": application.public_dict()}

    def list_approvals(self, subject: str | None) -> dict[str, Any]:
        identity = self._active_identity(subject)
        self._require_role(identity, Role.MANAGER)
        applications = self._repository.list_pending_approvals(identity.tenant_id)
        return {
            "items": [
                {
                    "applicationId": item.application_id,
                    "vehicleCount": item.vehicle_count,
                    "status": item.status.value,
                    "createdAt": item.public_dict()["createdAt"],
                }
                for item in applications
            ]
        }

    def decide_approval(
        self,
        subject: str | None,
        application_id: str,
        payload: Any,
    ) -> dict[str, Any]:
        identity = self._active_identity(subject)
        self._require_role(identity, Role.MANAGER)
        application = self._tenant_application(identity, application_id)
        if application.vehicle_count <= 50:
            raise Conflict("This application does not require Manager approval.")
        if application.status is not ApplicationStatus.PENDING_MANAGER:
            raise Conflict("This application is not awaiting a Manager decision.")

        try:
            body = body_object(payload)
            if set(body) != {"decision", "reasonCode"}:
                raise ValueError("Only decision and reasonCode are accepted in the request body")
            decision = require_decision(body.get("decision"))
            reason_code = require_reason_code(body.get("reasonCode"), decision=decision)
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc

        accepted = self._decision_dispatcher.dispatch(
            tenant_id=identity.tenant_id,
            application_id=application.application_id,
            actor=identity,
            decision=decision,
            reason_code=reason_code,
        )
        # La decisión fue aceptada por Step Functions, pero su finalizador es
        # asíncrono. El cliente debe observar el estado durable con GET.
        return accepted

    def get_plan_plus(self, subject: str | None) -> dict[str, Any]:
        identity = self._active_identity(subject)
        return {"plan": self._repository.get_plan_plus(identity.tenant_id).public_dict()}

    def _active_identity(self, subject: str | None) -> Identity:
        now = self._clock.now()
        snapshot = self._repository.pilot_snapshot(now)
        _assert_pilot_active(snapshot, now)
        if not subject or not isinstance(subject, str):
            raise Unauthorized()
        identity = self._repository.resolve_identity(subject)
        if identity is None:
            raise Unauthorized()
        return identity

    @staticmethod
    def _require_role(identity: Identity, role: Role) -> None:
        if identity.role is not role:
            raise Forbidden()

    def _tenant_application(self, identity: Identity, application_id: str) -> Application:
        try:
            safe_id = require_application_id(application_id)
        except ValueError as exc:
            # Mismo 404 para id malformado, inexistente o perteneciente a otro tenant.
            raise NotFound() from exc
        application = self._repository.get_application(identity.tenant_id, safe_id)
        if application is None:
            raise NotFound()
        return application


class WorkerService:
    """Opera tokens y transiciones internas sin exponer secretos al API."""

    def __init__(
        self,
        *,
        repository: Repository,
        token_vault: TokenVault,
        task_callback: TaskCallback,
        user_disabler: UserDisabler,
        clock: Clock,
        claim_seconds: int = 30,
    ) -> None:
        if not 5 <= claim_seconds <= 300:
            raise ValueError("claim_seconds must be between 5 and 300")
        self._repository = repository
        self._token_vault = token_vault
        self._task_callback = task_callback
        self._user_disabler = user_disabler
        self._clock = clock
        self._claim_seconds = claim_seconds

    def capture_approval_token(
        self,
        *,
        tenant_id: str,
        application_id: str,
        task_token: str,
    ) -> Application:
        now = self._require_active()
        if not isinstance(task_token, str) or not 32 <= len(task_token) <= 2048:
            raise BadRequest("Invalid Step Functions task token.")
        try:
            safe_id = require_application_id(application_id)
        except ValueError as exc:
            raise NotFound() from exc
        encrypted = self._token_vault.encrypt(
            task_token,
            tenant_id=tenant_id,
            application_id=safe_id,
        )
        return self._repository.store_approval_token(
            tenant_id=tenant_id,
            application_id=safe_id,
            encrypted_token=encrypted,
            token_fingerprint=hashlib.sha256(task_token.encode("utf-8")).hexdigest(),
            now=now,
        )

    def complete_manager_decision(
        self,
        *,
        tenant_id: str,
        application_id: str,
        actor_id: str,
        actor_role: str,
        decision: Decision,
        reason_code: str,
    ) -> dict[str, object]:
        now = self._require_active()
        try:
            parsed_decision = require_decision(decision)
            parsed_reason_code = require_reason_code(reason_code, decision=parsed_decision)
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc
        try:
            parsed_role = Role(actor_role)
        except ValueError as exc:
            raise Forbidden() from exc
        if parsed_role is not Role.MANAGER or not actor_id.startswith("actor_"):
            raise Forbidden()
        claim_id = uuid.uuid4().hex
        claimed = self._repository.claim_approval_decision(
            tenant_id=tenant_id,
            application_id=application_id,
            actor_id=actor_id,
            actor_role=parsed_role,
            decision=parsed_decision,
            reason_code=parsed_reason_code,
            claim_id=claim_id,
            claim_expires_at=now + timedelta(seconds=self._claim_seconds),
            now=now,
        )
        try:
            task_token = self._token_vault.decrypt(
                claimed.encrypted_token,
                tenant_id=tenant_id,
                application_id=application_id,
            )
            self._task_callback.send_success(
                token=task_token,
                output={
                    "applicationId": application_id,
                    "tenantId": tenant_id,
                    "decision": parsed_decision.value,
                    "reasonCode": parsed_reason_code,
                    "actorId": actor_id,
                    "actorRole": parsed_role.value,
                    "decidedAt": now.isoformat(),
                },
            )
        except Exception as exc:
            # El claim queda recuperable al vencer su lease. No hacemos ninguna
            # escritura después de SendTaskSuccess, incluso ante resultado de red
            # ambiguo, y nunca incluimos el token en el error.
            raise DependencyFailure() from exc
        return {
            "applicationId": application_id,
            "accepted": True,
            "status": ApplicationStatus.PENDING_MANAGER.value,
        }

    def finalize_approved(
        self,
        *,
        tenant_id: str,
        application_id: str,
        actor_id: str,
        actor_role: str,
        reason_code: str,
    ) -> Application:
        now = self._require_active()
        return self._repository.activate_plan(
            tenant_id=tenant_id,
            application_id=application_id,
            actor_id=actor_id,
            actor_role=actor_role,
            reason_code=reason_code,
            now=now,
        )

    def finalize_rejected(
        self,
        *,
        tenant_id: str,
        application_id: str,
        actor_id: str,
        actor_role: str,
        reason_code: str,
    ) -> Application:
        now = self._require_active()
        return self._repository.reject_application(
            tenant_id=tenant_id,
            application_id=application_id,
            actor_id=actor_id,
            actor_role=actor_role,
            reason_code=reason_code,
            now=now,
        )

    def expire_pilot(
        self,
        *,
        scheduled_expires_at: datetime,
        expected_user_count: int,
    ) -> dict[str, Any]:
        """Redundancia EventBridge; el control primario sigue en cada request.

        La transición se valida contra el timestamp durable. En retries, los
        usuarios se vuelven a deshabilitar idempotentemente aunque el piloto ya
        esté marcado EXPIRED.
        """
        now = self._clock.now()
        if scheduled_expires_at.tzinfo is None:
            raise BadRequest("scheduled_expires_at must include a timezone")
        if expected_user_count != 4:
            raise BadRequest("expected_user_count must be exactly 4")
        changed = self._repository.expire_pilot(
            expected_expires_at=scheduled_expires_at,
            now=now,
        )
        disabled = self._user_disabler.disable_allowlisted_users()
        if disabled != 4:
            raise DependencyFailure("The four synthetic users were not disabled.")
        return {
            "ok": True,
            "pilotStatus": "EXPIRED",
            "stateChanged": changed,
            "usersDisabled": disabled,
        }

    def _require_active(self):
        now = self._clock.now()
        snapshot = self._repository.pilot_snapshot(now)
        _assert_pilot_active(snapshot, now)
        return now


def _assert_pilot_active(snapshot: PilotSnapshot, now: datetime) -> None:
    """Distingue un piloto aún no staged de uno que ya expiró."""
    if snapshot.status is PilotStatus.PREPARED or snapshot.expires_at is None:
        raise PilotNotReady()
    if snapshot.status is not PilotStatus.ACTIVE or now >= snapshot.expires_at:
        raise PilotExpired()
