from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from sales_demo.backend.aws_adapters import DynamoRepository
from sales_demo.backend.domain import Identity, PilotStatus, Role
from sales_demo.backend.errors import (
    Conflict,
    DailyQuotaExceeded,
    PilotExpired,
    PilotNotReady,
)


NOW = datetime(2026, 8, 26, 15, 0, tzinfo=timezone.utc)
APP_ID = "app_" + "a" * 32


class TransactionCanceledException(Exception):
    pass


class ConditionalCheckFailedException(Exception):
    pass


class StubExceptions:
    TransactionCanceledException = TransactionCanceledException
    ConditionalCheckFailedException = ConditionalCheckFailedException


class PassthroughCodec:
    def serialize(self, value):
        return value

    def deserialize(self, value):
        return value


class StubDynamo:
    """Stub secuencial: conserva requests completas sin emular DynamoDB."""

    exceptions = StubExceptions

    def __init__(self, *, reads=(), transaction_effects=()) -> None:
        self.reads = list(reads)
        self.transaction_effects = list(transaction_effects)
        self.get_calls: list[dict] = []
        self.transaction_calls: list[dict] = []

    def get_item(self, **request):
        self.get_calls.append(request)
        if not self.reads:
            raise AssertionError(f"unexpected get_item: {request}")
        item = self.reads.pop(0)
        return {} if item is None else {"Item": item}

    def transact_write_items(self, **request):
        self.transaction_calls.append(request)
        effect = self.transaction_effects.pop(0) if self.transaction_effects else None
        if isinstance(effect, BaseException):
            raise effect
        return {}


def repository(client: StubDynamo) -> DynamoRepository:
    value = object.__new__(DynamoRepository)
    value.client = client
    value.table_name = "sales-table"
    value.pilot_id = "pilot-local"
    value._serializer = PassthroughCodec()
    value._deserializer = PassthroughCodec()
    return value


def application_item(
    *,
    status: str = "PENDING_MANAGER",
    tenant_id: str = "tenant-a",
    vehicle_count: int = 51,
) -> dict:
    value = {
        "pk": f"TENANT#{tenant_id}",
        "sk": f"APP#{APP_ID}",
        "application_id": APP_ID,
        "tenant_id": tenant_id,
        "vehicle_count": vehicle_count,
        "status": status,
        "created_at": "2026-08-26T15:00:00Z",
        "updated_at": "2026-08-26T15:00:00Z",
        "audit_trail": [
            {
                "action": "APPLICATION_SUBMITTED",
                "actorId": "actor_0123456789abcdef",
                "actorRole": "CUSTOMER",
                "occurredAt": "2026-08-26T15:00:00Z",
            }
        ],
    }
    if status == "CONTRACT_ACTIVE":
        value.update(
            {
                "decision": "APPROVE",
                "reason_code": "CAPACITY_CONFIRMED",
                "contract_id": "ctr_" + "b" * 24,
                "plan_activated_at": "2026-08-26T15:01:00Z",
            }
        )
    return value


def actor() -> Identity:
    return Identity("customer-a", "tenant-a", Role.CUSTOMER, "Synthetic Customer A")


def test_pilot_snapshot_requires_explicit_prepared_and_malformed_is_fail_closed() -> None:
    prepared = repository(StubDynamo(reads=[{"status": "PREPARED"}])).pilot_snapshot(NOW)
    missing = repository(StubDynamo(reads=[None])).pilot_snapshot(NOW)
    malformed = repository(
        StubDynamo(reads=[{"status": "ACTIVE", "expires_at_epoch": "not-an-epoch"}])
    ).pilot_snapshot(NOW)
    active = repository(
        StubDynamo(
            reads=[
                {
                    "status": "ACTIVE",
                    "expires_at_epoch": int((NOW + timedelta(hours=1)).timestamp()),
                }
            ]
        )
    ).pilot_snapshot(NOW)

    assert prepared.status is PilotStatus.PREPARED and prepared.expires_at is None
    assert missing.status is PilotStatus.EXPIRED and missing.expires_at == NOW
    assert malformed.status is PilotStatus.EXPIRED and malformed.expires_at == NOW
    assert active.status is PilotStatus.ACTIVE


def test_create_application_transaction_starts_with_active_gate_and_tenant_scoped_writes() -> None:
    client = StubDynamo()
    result = repository(client).create_application(
        tenant_id="tenant-a",
        application_id=APP_ID,
        vehicle_count=51,
        idempotency_key="dynamo-create-0123456789abcdef",
        actor=actor(),
        now=NOW,
        daily_limit=10,
    )

    assert result.replayed is False
    items = client.transaction_calls[0]["TransactItems"]
    active = items[0]["ConditionCheck"]
    assert active["Key"] == {"pk": "PILOT#pilot-local", "sk": "META"}
    assert active["ConditionExpression"] == "#status = :active AND expires_at_epoch > :now_epoch"
    assert active["ExpressionAttributeValues"] == {
        ":active": "ACTIVE",
        ":now_epoch": int(NOW.timestamp()),
    }
    assert items[1]["Update"]["Key"] == {
        "pk": "TENANT#tenant-a",
        "sk": "QUOTA#2026-08-26",
    }
    assert items[2]["Put"]["Item"]["pk"] == "TENANT#tenant-a"
    assert items[3]["Put"]["Item"]["tenant_id"] == "tenant-a"


def test_create_application_replay_checks_fingerprint_before_returning_durable_app() -> None:
    key = "dynamo-replay-0123456789abcdef"
    fingerprint = hashlib.sha256(
        json.dumps({"vehicleCount": 51}, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    idem = {
        "application_id": APP_ID,
        "request_fingerprint": fingerprint,
    }
    client = StubDynamo(
        reads=[idem, application_item()],
        transaction_effects=[TransactionCanceledException()],
    )
    replay = repository(client).create_application(
        tenant_id="tenant-a",
        application_id="app_" + "c" * 32,
        vehicle_count=51,
        idempotency_key=key,
        actor=actor(),
        now=NOW,
        daily_limit=10,
    )

    assert replay.replayed is True
    assert replay.application.application_id == APP_ID

    mismatched = StubDynamo(
        reads=[{"application_id": APP_ID, "request_fingerprint": "different"}],
        transaction_effects=[TransactionCanceledException()],
    )
    with pytest.raises(Conflict, match="different input"):
        repository(mismatched).create_application(
            tenant_id="tenant-a",
            application_id="app_" + "d" * 32,
            vehicle_count=51,
            idempotency_key=key,
            actor=actor(),
            now=NOW,
            daily_limit=10,
        )


def test_create_application_distinguishes_prepared_expired_and_daily_quota() -> None:
    prepared = StubDynamo(
        reads=[None, {"status": "PREPARED"}],
        transaction_effects=[TransactionCanceledException()],
    )
    expired = StubDynamo(
        reads=[
            None,
            {
                "status": "EXPIRED",
                "expires_at_epoch": int((NOW - timedelta(minutes=1)).timestamp()),
            },
        ],
        transaction_effects=[TransactionCanceledException()],
    )
    quota = StubDynamo(
        reads=[
            None,
            {
                "status": "ACTIVE",
                "expires_at_epoch": int((NOW + timedelta(hours=1)).timestamp()),
            },
            {"request_count": 10},
        ],
        transaction_effects=[TransactionCanceledException()],
    )
    arguments = {
        "tenant_id": "tenant-a",
        "application_id": APP_ID,
        "vehicle_count": 51,
        "idempotency_key": "example-key-0000",
        "actor": actor(),
        "now": NOW,
        "daily_limit": 10,
    }

    with pytest.raises(PilotNotReady):
        repository(prepared).create_application(**arguments)
    with pytest.raises(PilotExpired):
        repository(expired).create_application(**arguments)
    with pytest.raises(DailyQuotaExceeded):
        repository(quota).create_application(**arguments)


def test_manager_finalizer_is_atomic_active_gated_and_tenant_conditioned() -> None:
    current = application_item()
    current.update(
        {
            "callback_state": "CLAIMED",
            "approval_token_ciphertext": "kms-ciphertext",
            "approval_token_fingerprint": "f" * 64,
            "manager_actor_id": "actor_1234567890abcdef",
            "manager_actor_role": "MANAGER",
            "manager_decision": "APPROVE",
            "manager_reason_code": "CAPACITY_CONFIRMED",
        }
    )
    final = application_item(status="CONTRACT_ACTIVE")
    client = StubDynamo(reads=[current, final])
    result = repository(client).activate_plan(
        tenant_id="tenant-a",
        application_id=APP_ID,
        actor_id="actor_1234567890abcdef",
        actor_role="MANAGER",
        reason_code="CAPACITY_CONFIRMED",
        now=NOW + timedelta(minutes=1),
    )

    assert result.status.value == "CONTRACT_ACTIVE"
    items = client.transaction_calls[0]["TransactItems"]
    assert "ConditionCheck" in items[0]
    update = items[1]["Update"]
    assert update["Key"] == {"pk": "TENANT#tenant-a", "sk": f"APP#{APP_ID}"}
    for clause in (
        "callback_state = :claimed",
        "manager_decision = :approve",
        "manager_actor_id = :actor",
        "manager_actor_role = :role",
        "manager_reason_code = :reason",
    ):
        assert clause in update["ConditionExpression"]
    assert "REMOVE approval_token_ciphertext" in update["UpdateExpression"]
    plan = items[2]["Put"]["Item"]
    assert plan["pk"] == "TENANT#tenant-a"
    assert plan["application_id"] == APP_ID
