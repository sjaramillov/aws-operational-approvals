"""Adaptadores AWS del piloto comercial.

``boto3`` se importa únicamente al construir Lambdas. Las pruebas de dominio y
contrato pueden importar todos los demás módulos sin SDK ni credenciales.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

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
    isoformat,
    new_contract_id,
    parse_datetime,
)
from .errors import (
    Conflict,
    DailyQuotaExceeded,
    DependencyFailure,
    NotFound,
    PilotExpired,
    PilotNotReady,
)
from .service import ApiService, WorkerService


def _sdk():
    import boto3

    return boto3


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class DynamoRepository:
    """Single-table adapter with tenant-scoped keys and conditional writes."""

    def __init__(self, table_name: str, pilot_id: str) -> None:
        from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

        self.client = _sdk().client("dynamodb")
        self.table_name = table_name
        self.pilot_id = pilot_id
        self._serializer = TypeSerializer()
        self._deserializer = TypeDeserializer()

    def pilot_snapshot(self, now: datetime) -> PilotSnapshot:
        response = self.client.get_item(
            TableName=self.table_name,
            Key=self._key(f"PILOT#{self.pilot_id}", "META"),
            ConsistentRead=True,
        )
        item = self._load(response.get("Item"))
        if not item:
            return PilotSnapshot(PilotStatus.EXPIRED, now)
        if item.get("status") == PilotStatus.PREPARED.value:
            return PilotSnapshot(PilotStatus.PREPARED, None)
        if item.get("status") not in {
            PilotStatus.ACTIVE.value,
            PilotStatus.EXPIRED.value,
        } or item.get("expires_at_epoch") is None:
            return PilotSnapshot(PilotStatus.EXPIRED, now)
        try:
            expires_at = datetime.fromtimestamp(int(item["expires_at_epoch"]), timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            return PilotSnapshot(PilotStatus.EXPIRED, now)
        status = (
            PilotStatus.ACTIVE
            if item.get("status") == PilotStatus.ACTIVE.value and now < expires_at
            else PilotStatus.EXPIRED
        )
        return PilotSnapshot(status, expires_at)

    def resolve_identity(self, subject: str) -> Identity | None:
        subject_hash = hashlib.sha256(subject.encode()).hexdigest()
        response = self.client.get_item(
            TableName=self.table_name,
            Key=self._key(f"IDENTITY#{subject_hash}", "PROFILE"),
            ConsistentRead=True,
        )
        item = self._load(response.get("Item"))
        if not item or item.get("enabled") is not True:
            return None
        return Identity(
            subject=subject,
            tenant_id=item["tenant_id"],
            role=Role(item["role"]),
            display_name=item["display_name"],
        )

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
        self._validate_tenant(tenant_id)
        idem_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        fingerprint = hashlib.sha256(
            json.dumps({"vehicleCount": vehicle_count}, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()
        tenant_pk = f"TENANT#{tenant_id}"
        idem_key = self._key(tenant_pk, f"IDEMPOTENCY#{idem_hash}")
        day = now.astimezone(timezone.utc).date().isoformat()
        status = ApplicationStatus.APPROVED if vehicle_count <= 50 else ApplicationStatus.PENDING_MANAGER
        audit = AuditEntry(
            AuditAction.APPLICATION_SUBMITTED,
            actor.actor_id,
            actor.role,
            now,
        )
        app = Application(
            application_id=application_id,
            tenant_id=tenant_id,
            vehicle_count=vehicle_count,
            status=status,
            created_at=now,
            updated_at=now,
            audit_trail=[audit],
            decision=Decision.APPROVE if vehicle_count <= 50 else None,
            reason_code="AUTO_APPROVED" if vehicle_count <= 50 else None,
        )
        app_item = self._application_item(app)
        idem_item = {
            "pk": tenant_pk,
            "sk": f"IDEMPOTENCY#{idem_hash}",
            "application_id": application_id,
            "request_fingerprint": fingerprint,
            "created_at": isoformat(now),
        }
        values = self._values({":zero": 0, ":one": 1, ":limit": daily_limit})
        try:
            self.client.transact_write_items(
                TransactItems=[
                    self._active_condition(now),
                    {
                        "Update": {
                            "TableName": self.table_name,
                            "Key": self._key(tenant_pk, f"QUOTA#{day}"),
                            "UpdateExpression": "SET #count = if_not_exists(#count, :zero) + :one",
                            "ConditionExpression": "attribute_not_exists(#count) OR #count < :limit",
                            "ExpressionAttributeNames": {"#count": "request_count"},
                            "ExpressionAttributeValues": values,
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": self._dump(idem_item),
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": self._dump(app_item),
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                ]
            )
            return CreateApplicationResult(app, False)
        except self.client.exceptions.TransactionCanceledException:
            existing = self._load(
                self.client.get_item(
                    TableName=self.table_name,
                    Key=idem_key,
                    ConsistentRead=True,
                ).get("Item")
            )
            if existing:
                if existing.get("request_fingerprint") != fingerprint:
                    raise Conflict("Idempotency-Key was already used with different input.")
                application = self.get_application(tenant_id, existing["application_id"])
                if application is None:
                    raise Conflict("Idempotency record has no application.")
                return CreateApplicationResult(application, True)
            self._raise_if_not_active(now)
            quota = self._load(
                self.client.get_item(
                    TableName=self.table_name,
                    Key=self._key(tenant_pk, f"QUOTA#{day}"),
                    ConsistentRead=True,
                ).get("Item")
            )
            if int((quota or {}).get("request_count", 0)) >= daily_limit:
                raise DailyQuotaExceeded()
            raise Conflict("Application transaction was rejected.")

    def get_application(self, tenant_id: str, application_id: str) -> Application | None:
        self._validate_tenant(tenant_id)
        item = self._load(
            self.client.get_item(
                TableName=self.table_name,
                Key=self._key(f"TENANT#{tenant_id}", f"APP#{application_id}"),
                ConsistentRead=True,
            ).get("Item")
        )
        return self._application_from_item(item) if item else None

    def list_applications(self, tenant_id: str) -> list[Application]:
        self._validate_tenant(tenant_id)
        response = self.client.query(
            TableName=self.table_name,
            KeyConditionExpression="pk = :pk AND begins_with(sk, :prefix)",
            ExpressionAttributeValues=self._values({":pk": f"TENANT#{tenant_id}", ":prefix": "APP#"}),
            ScanIndexForward=False,
            Limit=100,
        )
        return [self._application_from_item(self._load(item)) for item in response.get("Items", [])]

    def list_pending_approvals(self, tenant_id: str) -> list[Application]:
        self._validate_tenant(tenant_id)
        response = self.client.query(
            TableName=self.table_name,
            KeyConditionExpression="pk = :pk AND begins_with(sk, :prefix)",
            ExpressionAttributeValues=self._values(
                {":pk": f"TENANT#{tenant_id}", ":prefix": "APP#"}
            ),
            ScanIndexForward=False,
            Limit=100,
        )
        pending: list[Application] = []
        for encoded in response.get("Items", []):
            item = self._load(encoded)
            if (
                item.get("status") == ApplicationStatus.PENDING_MANAGER.value
                and item.get("callback_state") in {"READY", "CLAIMED"}
                and item.get("approval_token_ciphertext")
            ):
                pending.append(self._application_from_item(item))
        return pending

    def get_plan_plus(self, tenant_id: str) -> PlanPlus:
        self._validate_tenant(tenant_id)
        item = self._load(
            self.client.get_item(
                TableName=self.table_name,
                Key=self._key(f"TENANT#{tenant_id}", "PLAN#PLUS"),
                ConsistentRead=True,
            ).get("Item")
        )
        if not item:
            return PlanPlus(tenant_id)
        return PlanPlus(
            tenant_id=tenant_id,
            status=item["status"],
            application_id=item.get("application_id"),
            contract_id=item.get("contract_id"),
            activated_at=parse_datetime(item["activated_at"]) if item.get("activated_at") else None,
        )

    def store_approval_token(
        self,
        *,
        tenant_id: str,
        application_id: str,
        encrypted_token: str,
        token_fingerprint: str,
        now: datetime,
    ) -> Application:
        audit = AuditEntry(
            AuditAction.MANAGER_APPROVAL_REQUESTED,
            "actor_system",
            Role.MANAGER,
            now,
        ).public_dict()
        try:
            self.client.transact_write_items(
                TransactItems=[
                    self._active_condition(now),
                    {
                        "Update": {
                            "TableName": self.table_name,
                            "Key": self._key(f"TENANT#{tenant_id}", f"APP#{application_id}"),
                            "UpdateExpression": (
                                "SET approval_token_ciphertext = :token, "
                                "approval_token_fingerprint = :fingerprint, callback_state = :ready, "
                                "updated_at = :now, audit_trail = list_append(audit_trail, :audit)"
                            ),
                            "ConditionExpression": "#status = :pending AND attribute_not_exists(approval_token_ciphertext)",
                            "ExpressionAttributeNames": {"#status": "status"},
                            "ExpressionAttributeValues": self._values(
                                {
                                    ":token": encrypted_token,
                                    ":fingerprint": token_fingerprint,
                                    ":ready": "READY",
                                    ":now": isoformat(now),
                                    ":audit": [audit],
                                    ":pending": ApplicationStatus.PENDING_MANAGER.value,
                                }
                            ),
                        }
                    },
                ]
            )
        except self.client.exceptions.TransactionCanceledException:
            self._raise_if_not_active(now)
            raw = self._raw_application(tenant_id, application_id)
            app = self._application_from_item(raw)
            if app.status is ApplicationStatus.PENDING_MANAGER and raw.get(
                "approval_token_ciphertext"
            ):
                if raw.get("callback_state") == "READY":
                    try:
                        self.client.transact_write_items(
                            TransactItems=[
                                self._active_condition(now),
                                {
                                    "Update": {
                                        "TableName": self.table_name,
                                        "Key": self._key(
                                            f"TENANT#{tenant_id}", f"APP#{application_id}"
                                        ),
                                        "UpdateExpression": (
                                            "SET approval_token_ciphertext = :token, "
                                            "approval_token_fingerprint = :fingerprint, updated_at = :now"
                                        ),
                                        "ConditionExpression": (
                                            "#status = :pending AND callback_state = :ready"
                                        ),
                                        "ExpressionAttributeNames": {"#status": "status"},
                                        "ExpressionAttributeValues": self._values(
                                            {
                                                ":token": encrypted_token,
                                                ":fingerprint": token_fingerprint,
                                                ":now": isoformat(now),
                                                ":pending": ApplicationStatus.PENDING_MANAGER.value,
                                                ":ready": "READY",
                                            }
                                        ),
                                    }
                                },
                            ]
                        )
                    except self.client.exceptions.TransactionCanceledException as exc:
                        raise Conflict("Callback token changed while retrying capture.") from exc
                    return self.get_application(tenant_id, application_id) or app
                if raw.get("approval_token_fingerprint") == token_fingerprint:
                    return app
                raise Conflict("A claimed callback token cannot be replaced.")
            raise Conflict("Application is not pending Manager approval.")
        application = self.get_application(tenant_id, application_id)
        if application is None:
            raise NotFound()
        return application

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
        key = self._key(f"TENANT#{tenant_id}", f"APP#{application_id}")
        raw = self._load(
            self.client.get_item(TableName=self.table_name, Key=key, ConsistentRead=True).get("Item")
        )
        if not raw:
            raise NotFound()
        encrypted = raw.get("approval_token_ciphertext")
        token_fingerprint = raw.get("approval_token_fingerprint")
        if not encrypted or not token_fingerprint:
            raise Conflict("Approval callback is not ready.")
        try:
            self.client.transact_write_items(
                TransactItems=[
                    self._active_condition(now),
                    {
                        "Update": {
                            "TableName": self.table_name,
                            "Key": key,
                            "UpdateExpression": (
                                "SET callback_state = :claimed, callback_claim_id = :claim, "
                                "callback_claim_expires_at = :claim_exp, manager_actor_id = :actor, "
                                "manager_actor_role = :role, manager_decision = :decision, "
                                "manager_reason_code = :reason, updated_at = :now"
                            ),
                            "ConditionExpression": (
                                "#status = :pending AND attribute_exists(approval_token_ciphertext) "
                                "AND approval_token_fingerprint = :fingerprint "
                                "AND (callback_state = :ready OR (callback_state = :claimed "
                                "AND callback_claim_expires_at <= :now_epoch "
                                "AND manager_actor_id = :actor AND manager_actor_role = :role "
                                "AND manager_decision = :decision AND manager_reason_code = :reason))"
                            ),
                            "ExpressionAttributeNames": {"#status": "status"},
                            "ExpressionAttributeValues": self._values(
                                {
                                    ":ready": "READY",
                                    ":claimed": "CLAIMED",
                                    ":claim": claim_id,
                                    ":claim_exp": int(claim_expires_at.timestamp()),
                                    ":actor": actor_id,
                                    ":role": actor_role.value,
                                    ":decision": decision.value,
                                    ":reason": reason_code,
                                    ":fingerprint": token_fingerprint,
                                    ":now": isoformat(now),
                                    ":now_epoch": int(now.timestamp()),
                                    ":pending": ApplicationStatus.PENDING_MANAGER.value,
                                }
                            ),
                        }
                    },
                ]
            )
        except self.client.exceptions.TransactionCanceledException as exc:
            raise Conflict("Approval token is not READY or its compatible claim lease is active.") from exc
        application = self._application_from_item(raw)
        return ClaimedApproval(application, encrypted, claim_id)

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
        current = self._raw_application(tenant_id, application_id)
        if current.get("status") == ApplicationStatus.CONTRACT_ACTIVE.value:
            return self._application_from_item(current)
        vehicle_count = int(current["vehicle_count"])
        allowed = "#status = :approved" if vehicle_count <= 50 else (
            "#status = :pending AND callback_state = :claimed "
            "AND manager_decision = :approve AND manager_actor_id = :actor "
            "AND manager_actor_role = :role AND manager_reason_code = :reason"
        )
        contract_id = new_contract_id(application_id)
        final_audit = AuditEntry(
            AuditAction.CONTRACT_ACTIVATED,
            actor_id,
            Role(actor_role),
            now,
            Decision.APPROVE,
            reason_code,
        ).public_dict()
        audits = [final_audit]
        if vehicle_count > 50:
            manager_audit = AuditEntry(
                AuditAction.MANAGER_DECISION_RECORDED,
                actor_id,
                Role(actor_role),
                now,
                Decision.APPROVE,
                reason_code,
            ).public_dict()
            audits = [manager_audit, final_audit]
        values: dict[str, Any] = {
            ":active": ApplicationStatus.CONTRACT_ACTIVE.value,
            ":approve": Decision.APPROVE.value,
            ":reason": reason_code,
            ":contract": contract_id,
            ":now": isoformat(now),
            ":audit": audits,
        }
        if vehicle_count <= 50:
            values[":approved"] = ApplicationStatus.APPROVED.value
        else:
            values.update(
                {
                    ":pending": ApplicationStatus.PENDING_MANAGER.value,
                    ":claimed": "CLAIMED",
                    ":actor": actor_id,
                    ":role": actor_role,
                }
            )
        plan_item = {
            "pk": f"TENANT#{tenant_id}",
            "sk": "PLAN#PLUS",
            "status": "ACTIVE",
            "application_id": application_id,
            "contract_id": contract_id,
            "activated_at": isoformat(now),
        }
        try:
            self.client.transact_write_items(
                TransactItems=[
                    self._active_condition(now),
                    {
                        "Update": {
                            "TableName": self.table_name,
                            "Key": self._key(f"TENANT#{tenant_id}", f"APP#{application_id}"),
                            "UpdateExpression": (
                                "SET #status = :active, #decision = :approve, reason_code = :reason, "
                                "contract_id = :contract, plan_activated_at = :now, updated_at = :now, "
                                "audit_trail = list_append(audit_trail, :audit) "
                                "REMOVE approval_token_ciphertext, approval_token_fingerprint, "
                                "callback_state, callback_claim_id, "
                                "callback_claim_expires_at, manager_actor_id, manager_actor_role, "
                                "manager_decision, manager_reason_code"
                            ),
                            "ConditionExpression": allowed,
                            "ExpressionAttributeNames": {"#status": "status", "#decision": "decision"},
                            "ExpressionAttributeValues": self._values(values),
                        }
                    },
                    {"Put": {"TableName": self.table_name, "Item": self._dump(plan_item)}},
                ]
            )
        except self.client.exceptions.TransactionCanceledException as exc:
            raise Conflict("Application cannot activate Servicio in its current state.") from exc
        application = self.get_application(tenant_id, application_id)
        if application is None:
            raise NotFound()
        return application

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
        current = self._raw_application(tenant_id, application_id)
        if current.get("status") == ApplicationStatus.REJECTED.value:
            return self._application_from_item(current)
        final_audit = AuditEntry(
            AuditAction.APPLICATION_REJECTED,
            actor_id,
            Role(actor_role),
            now,
            Decision.REJECT,
            reason_code,
        ).public_dict()
        system_failure = actor_id == "actor_system" and reason_code in {
            "MANAGER_APPROVAL_TIMEOUT",
        }
        audits = [final_audit]
        if not system_failure:
            manager_audit = AuditEntry(
                AuditAction.MANAGER_DECISION_RECORDED,
                actor_id,
                Role(actor_role),
                now,
                Decision.REJECT,
                reason_code,
            ).public_dict()
            audits = [manager_audit, final_audit]
        condition = (
            "#status = :pending"
            if system_failure
            else (
                "#status = :pending AND callback_state = :claimed "
                "AND manager_decision = :reject AND manager_actor_id = :actor "
                "AND manager_actor_role = :role AND manager_reason_code = :reason"
            )
        )
        expression_values: dict[str, Any] = {
            ":rejected": ApplicationStatus.REJECTED.value,
            ":pending": ApplicationStatus.PENDING_MANAGER.value,
            ":reject": Decision.REJECT.value,
            ":reason": reason_code,
            ":now": isoformat(now),
            ":audit": audits,
        }
        if not system_failure:
            expression_values.update(
                {":claimed": "CLAIMED", ":actor": actor_id, ":role": actor_role}
            )
        try:
            self.client.transact_write_items(
                TransactItems=[
                    self._active_condition(now),
                    {
                        "Update": {
                            "TableName": self.table_name,
                            "Key": self._key(f"TENANT#{tenant_id}", f"APP#{application_id}"),
                            "UpdateExpression": (
                                "SET #status = :rejected, #decision = :reject, reason_code = :reason, "
                                "updated_at = :now, "
                                "audit_trail = list_append(audit_trail, :audit) "
                                "REMOVE approval_token_ciphertext, approval_token_fingerprint, "
                                "callback_state, callback_claim_id, "
                                "callback_claim_expires_at, manager_actor_id, manager_actor_role, "
                                "manager_decision, manager_reason_code"
                            ),
                            "ConditionExpression": condition,
                            "ExpressionAttributeNames": {"#status": "status", "#decision": "decision"},
                            "ExpressionAttributeValues": self._values(expression_values),
                        }
                    },
                ]
            )
        except self.client.exceptions.TransactionCanceledException as exc:
            raise Conflict("Application cannot be rejected in its current state.") from exc
        application = self.get_application(tenant_id, application_id)
        if application is None:
            raise NotFound()
        return application

    def expire_pilot(self, *, expected_expires_at: datetime, now: datetime) -> bool:
        expected = int(expected_expires_at.timestamp())
        if int(now.timestamp()) < expected:
            raise Conflict("Pilot cannot expire before its durable expires_at.")
        key = self._key(f"PILOT#{self.pilot_id}", "META")
        try:
            self.client.update_item(
                TableName=self.table_name,
                Key=key,
                UpdateExpression="SET #status = :expired, expired_at = :now",
                ConditionExpression=(
                    "#status = :active AND expires_at_epoch = :expected "
                    "AND expires_at_epoch <= :now_epoch"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues=self._values(
                    {
                        ":expired": PilotStatus.EXPIRED.value,
                        ":active": PilotStatus.ACTIVE.value,
                        ":expected": expected,
                        ":now_epoch": int(now.timestamp()),
                        ":now": isoformat(now),
                    }
                ),
            )
            return True
        except self.client.exceptions.ConditionalCheckFailedException:
            item = self._load(
                self.client.get_item(
                    TableName=self.table_name,
                    Key=key,
                    ConsistentRead=True,
                ).get("Item")
            )
            if item.get("status") == PilotStatus.EXPIRED.value and int(
                item.get("expires_at_epoch", -1)
            ) == expected:
                return False
            raise Conflict("Scheduled expiration does not match the durable pilot contract.")

    def _active_condition(self, now: datetime) -> dict[str, Any]:
        return {
            "ConditionCheck": {
                "TableName": self.table_name,
                "Key": self._key(f"PILOT#{self.pilot_id}", "META"),
                "ConditionExpression": "#status = :active AND expires_at_epoch > :now_epoch",
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": self._values(
                    {":active": PilotStatus.ACTIVE.value, ":now_epoch": int(now.timestamp())}
                ),
            }
        }

    def _raise_if_not_active(self, now: datetime) -> None:
        snapshot = self.pilot_snapshot(now)
        if snapshot.status is PilotStatus.PREPARED or snapshot.expires_at is None:
            raise PilotNotReady()
        if snapshot.status is not PilotStatus.ACTIVE or now >= snapshot.expires_at:
            raise PilotExpired()

    def _raw_application(self, tenant_id: str, application_id: str) -> dict[str, Any]:
        item = self._load(
            self.client.get_item(
                TableName=self.table_name,
                Key=self._key(f"TENANT#{tenant_id}", f"APP#{application_id}"),
                ConsistentRead=True,
            ).get("Item")
        )
        if not item:
            raise NotFound()
        return item

    def _application_item(self, application: Application) -> dict[str, Any]:
        value = {
            "pk": f"TENANT#{application.tenant_id}",
            "sk": f"APP#{application.application_id}",
            "application_id": application.application_id,
            "tenant_id": application.tenant_id,
            "vehicle_count": application.vehicle_count,
            "status": application.status.value,
            "created_at": isoformat(application.created_at),
            "updated_at": isoformat(application.updated_at),
            "audit_trail": [entry.public_dict() for entry in application.audit_trail],
        }
        if application.decision:
            value["decision"] = application.decision.value
        if application.reason_code:
            value["reason_code"] = application.reason_code
        return value

    @staticmethod
    def _application_from_item(item: dict[str, Any]) -> Application:
        trail = [
            AuditEntry(
                action=AuditAction(entry["action"]),
                actor_id=entry["actorId"],
                actor_role=Role(entry["actorRole"]),
                occurred_at=parse_datetime(entry["occurredAt"]),
                decision=Decision(entry["decision"]) if entry.get("decision") else None,
                reason_code=entry.get("reasonCode"),
            )
            for entry in item.get("audit_trail", [])
        ]
        return Application(
            application_id=item["application_id"],
            tenant_id=item["tenant_id"],
            vehicle_count=int(item["vehicle_count"]),
            status=ApplicationStatus(item["status"]),
            created_at=parse_datetime(item["created_at"]),
            updated_at=parse_datetime(item["updated_at"]),
            audit_trail=trail,
            decision=Decision(item["decision"]) if item.get("decision") else None,
            reason_code=item.get("reason_code"),
            contract_id=item.get("contract_id"),
            plan_activated_at=parse_datetime(item["plan_activated_at"])
            if item.get("plan_activated_at")
            else None,
        )

    @staticmethod
    def _validate_tenant(value: str) -> None:
        if not value or len(value) > 128 or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in value):
            raise ValueError("invalid tenant identifier")

    def _key(self, pk: str, sk: str) -> dict[str, Any]:
        return self._dump({"pk": pk, "sk": sk})

    def _dump(self, value: dict[str, Any]) -> dict[str, Any]:
        return {key: self._serializer.serialize(item) for key, item in value.items()}

    def _load(self, value: dict[str, Any] | None) -> dict[str, Any]:
        if not value:
            return {}
        return {key: self._deserializer.deserialize(item) for key, item in value.items()}

    def _values(self, value: dict[str, Any]) -> dict[str, Any]:
        return self._dump(value)


class StepFunctionsWorkflow:
    def __init__(self, state_machine_arn: str, client: Any | None = None) -> None:
        self.client = client or _sdk().client("stepfunctions")
        self.state_machine_arn = state_machine_arn

    def start_application(self, *, tenant_id: str, application_id: str, vehicle_count: int) -> None:
        payload = json.dumps(
            {"tenantId": tenant_id, "applicationId": application_id, "vehicleCount": vehicle_count},
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            self.client.start_execution(
                stateMachineArn=self.state_machine_arn,
                name=application_id,
                input=payload,
            )
        except self.client.exceptions.ExecutionAlreadyExists:
            execution_arn = self._execution_arn(application_id)
            existing = self.client.describe_execution(executionArn=execution_arn)
            try:
                existing_input = json.loads(existing.get("input", ""))
            except (TypeError, json.JSONDecodeError) as exc:
                raise DependencyFailure("Existing workflow input is unreadable.") from exc
            expected_input = json.loads(payload)
            if existing_input != expected_input:
                raise DependencyFailure("Execution name exists with incompatible input.")
            status = existing.get("status")
            if status in {"RUNNING", "SUCCEEDED"}:
                return
            if status in {"FAILED", "TIMED_OUT", "ABORTED"}:
                if existing.get("redriveStatus") != "REDRIVABLE":
                    raise DependencyFailure(
                        f"Existing {status} execution is not REDRIVABLE."
                    )
                self.client.redrive_execution(executionArn=execution_arn)
                return
            raise DependencyFailure(f"Existing execution has unsupported status {status!r}.")

    def _execution_arn(self, application_id: str) -> str:
        marker = ":stateMachine:"
        if marker not in self.state_machine_arn:
            raise DependencyFailure("State machine ARN cannot derive an execution ARN.")
        prefix, state_machine_name = self.state_machine_arn.split(marker, 1)
        return f"{prefix}:execution:{state_machine_name}:{application_id}"


class LambdaDecisionDispatcher:
    def __init__(self, function_name: str, client: Any | None = None) -> None:
        self.client = client or _sdk().client("lambda")
        self.function_name = function_name

    def dispatch(
        self,
        *,
        tenant_id: str,
        application_id: str,
        actor: Identity,
        decision: Decision,
        reason_code: str,
    ) -> dict[str, object]:
        payload = {
            "action": "COMPLETE_MANAGER_DECISION",
            "tenantId": tenant_id,
            "applicationId": application_id,
            "actorId": actor.actor_id,
            "actorRole": actor.role.value,
            "decision": decision.value,
            "reasonCode": reason_code,
        }
        response = self.client.invoke(
            FunctionName=self.function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload, separators=(",", ":")).encode(),
        )
        if response.get("FunctionError"):
            raise DependencyFailure("Worker rejected Manager decision.")
        payload_stream = response.get("Payload")
        raw = payload_stream.read() if hasattr(payload_stream, "read") else payload_stream
        try:
            result = json.loads(raw or b"{}")
            accepted = result["decision"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise DependencyFailure("Worker returned an invalid acceptance DTO.") from exc
        expected_keys = {"applicationId", "accepted", "status"}
        if (
            not isinstance(accepted, dict)
            or set(accepted) != expected_keys
            or accepted.get("applicationId") != application_id
            or accepted.get("accepted") is not True
            or accepted.get("status") != ApplicationStatus.PENDING_MANAGER.value
        ):
            raise DependencyFailure("Worker returned an incompatible acceptance DTO.")
        return accepted


class KmsTokenVault:
    def __init__(self, key_id: str, pilot_id: str) -> None:
        self.client = _sdk().client("kms")
        self.key_id = key_id
        self.pilot_id = pilot_id

    def encrypt(self, token: str, *, tenant_id: str, application_id: str) -> str:
        result = self.client.encrypt(
            KeyId=self.key_id,
            Plaintext=token.encode(),
            EncryptionContext=self._context(tenant_id, application_id),
        )
        return base64.b64encode(result["CiphertextBlob"]).decode()

    def decrypt(self, encrypted_token: str, *, tenant_id: str, application_id: str) -> str:
        result = self.client.decrypt(
            KeyId=self.key_id,
            CiphertextBlob=base64.b64decode(encrypted_token.encode(), validate=True),
            EncryptionContext=self._context(tenant_id, application_id),
        )
        return result["Plaintext"].decode()

    def _context(self, tenant_id: str, application_id: str) -> dict[str, str]:
        return {"pilot": self.pilot_id, "tenant": tenant_id, "application": application_id}


class StepFunctionsTaskCallback:
    def __init__(self) -> None:
        self.client = _sdk().client("stepfunctions")

    def send_success(self, *, token: str, output: dict[str, str]) -> None:
        self.client.send_task_success(
            taskToken=token,
            output=json.dumps(output, sort_keys=True, separators=(",", ":")),
        )


EXPECTED_SYNTHETIC_USERNAMES = (
    "customer-a",
    "customer-b",
    "manager-a",
    "manager-b",
)


class CognitoUserDisabler:
    def __init__(self, user_pool_id: str, configured_usernames: str) -> None:
        usernames = tuple(sorted(part.strip() for part in configured_usernames.split(",") if part.strip()))
        if usernames != EXPECTED_SYNTHETIC_USERNAMES:
            raise RuntimeError("SALES_SYNTHETIC_USERNAMES must contain the four exact pilot slots")
        self.client = _sdk().client("cognito-idp")
        self.user_pool_id = user_pool_id
        self.usernames = usernames

    def disable_allowlisted_users(self) -> int:
        for username in self.usernames:
            self.client.admin_disable_user(UserPoolId=self.user_pool_id, Username=username)
        return len(self.usernames)


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def build_api_service() -> ApiService:
    pilot_id = _required_env("SALES_PILOT_ID")
    repository = DynamoRepository(_required_env("SALES_TABLE_NAME"), pilot_id)
    return ApiService(
        repository=repository,
        workflow=StepFunctionsWorkflow(_required_env("SALES_STATE_MACHINE_ARN")),
        decision_dispatcher=LambdaDecisionDispatcher(
            _required_env("SALES_WORKER_FUNCTION_NAME")
        ),
        clock=SystemClock(),
        daily_limit=int(os.getenv("SALES_DAILY_LIMIT", "10")),
    )


def build_worker_service() -> WorkerService:
    pilot_id = _required_env("SALES_PILOT_ID")
    repository = DynamoRepository(_required_env("SALES_TABLE_NAME"), pilot_id)
    return WorkerService(
        repository=repository,
        token_vault=KmsTokenVault(_required_env("SALES_KMS_KEY_ID"), pilot_id),
        task_callback=StepFunctionsTaskCallback(),
        user_disabler=CognitoUserDisabler(
            _required_env("SALES_USER_POOL_ID"),
            _required_env("SALES_SYNTHETIC_USERNAMES"),
        ),
        clock=SystemClock(),
    )
