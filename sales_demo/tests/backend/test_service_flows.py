from __future__ import annotations

import json

import pytest

from sales_demo.backend.domain import ApplicationStatus, Decision, PilotStatus
from sales_demo.backend.errors import (
    BadRequest,
    Conflict,
    DailyQuotaExceeded,
    Forbidden,
    NotFound,
    PilotExpired,
    PilotNotReady,
)
from sales_demo.backend.memory import build_local_bundle


def create(bundle, subject: str, count: int, key: str):
    return bundle.api.create_application(
        subject,
        {"vehicleCount": count},
        f"{key}-0123456789abcdef",
    )[0]


def test_50_vehicles_auto_approves_contract_and_plan() -> None:
    bundle = build_local_bundle()

    created = create(bundle, "customer-a", 50, "fleet-50")
    application_id = created["application"]["applicationId"]
    durable = bundle.api.get_application("customer-a", application_id)["application"]

    assert created["application"]["status"] == ApplicationStatus.APPROVED.value
    assert durable["status"] == ApplicationStatus.CONTRACT_ACTIVE.value
    assert durable["decision"] == Decision.APPROVE.value
    assert durable["reasonCode"] == "AUTO_APPROVED"
    assert durable["contractId"].startswith("ctr_")
    assert [entry["action"] for entry in durable["auditTrail"]] == [
        "APPLICATION_SUBMITTED",
        "CONTRACT_ACTIVATED",
    ]
    plan = bundle.api.get_plan_plus("customer-a")["plan"]
    assert plan["status"] == "ACTIVE"
    assert plan["applicationId"] == application_id


def test_51_vehicles_requires_real_manager_approval() -> None:
    bundle = build_local_bundle()
    created = create(bundle, "customer-a", 51, "fleet-51")
    application_id = created["application"]["applicationId"]

    pending = bundle.api.list_approvals("manager-a")["items"]
    assert pending == [
        {
            "applicationId": application_id,
            "vehicleCount": 51,
            "status": "PENDING_MANAGER",
            "createdAt": created["application"]["createdAt"],
        }
    ]
    accepted = bundle.api.decide_approval(
        "manager-a",
        application_id,
        {"decision": "APPROVE", "reasonCode": "CAPACITY_CONFIRMED"},
    )
    assert accepted == {
        "applicationId": application_id,
        "accepted": True,
        "status": "PENDING_MANAGER",
    }
    assert bundle.api.get_application("customer-a", application_id)["application"]["status"] == "PENDING_MANAGER"
    bundle.callback.flush()
    decided = bundle.api.get_application("customer-a", application_id)["application"]

    assert decided["status"] == "CONTRACT_ACTIVE"
    assert decided["decision"] == "APPROVE"
    assert [entry["action"] for entry in decided["auditTrail"]] == [
        "APPLICATION_SUBMITTED",
        "MANAGER_APPROVAL_REQUESTED",
        "MANAGER_DECISION_RECORDED",
        "CONTRACT_ACTIVATED",
    ]
    manager_entry = decided["auditTrail"][2]
    assert manager_entry["actorId"].startswith("actor_")
    assert manager_entry["actorRole"] == "MANAGER"
    assert manager_entry["reasonCode"] == "CAPACITY_CONFIRMED"


def test_manager_queue_only_exposes_applications_after_callback_is_ready() -> None:
    bundle = build_local_bundle()
    actor = bundle.repository.resolve_identity("customer-a")
    assert actor is not None
    application_id = "app_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    bundle.repository.create_application(
        tenant_id="tenant-a",
        application_id=application_id,
        vehicle_count=51,
        idempotency_key="queue-race-0123456789abcdef",
        actor=actor,
        now=bundle.clock.now(),
        daily_limit=10,
    )

    assert bundle.api.get_application("customer-a", application_id)["application"]["status"] == "PENDING_MANAGER"
    assert bundle.api.list_approvals("manager-a") == {"items": []}

    bundle.worker.capture_approval_token(
        tenant_id="tenant-a",
        application_id=application_id,
        task_token="local-task-token-" + "r" * 64,
    )
    assert [item["applicationId"] for item in bundle.api.list_approvals("manager-a")["items"]] == [
        application_id
    ]


def test_52_vehicles_rejects_only_from_manager_decision_not_vehicle_number() -> None:
    bundle = build_local_bundle()
    created = create(bundle, "customer-a", 52, "fleet-52")
    application_id = created["application"]["applicationId"]

    accepted = bundle.api.decide_approval(
        "manager-a",
        application_id,
        {"decision": "REJECT", "reasonCode": "CAPACITY_NOT_AVAILABLE"},
    )
    assert accepted["accepted"] is True
    bundle.callback.flush()
    decided = bundle.api.get_application("customer-a", application_id)["application"]

    assert decided["status"] == "REJECTED"
    assert decided["decision"] == "REJECT"
    assert decided["reasonCode"] == "CAPACITY_NOT_AVAILABLE"
    assert bundle.api.get_plan_plus("customer-a")["plan"] == {"status": "INACTIVE"}
    assert decided["auditTrail"][-1]["action"] == "APPLICATION_REJECTED"


def test_52_can_be_approved_and_51_can_be_rejected() -> None:
    """La fixture de demo no codifica la decisión en el número 51/52."""
    bundle = build_local_bundle()
    app_52 = create(bundle, "customer-a", 52, "fleet-52-approved")["application"]["applicationId"]
    bundle.api.decide_approval(
        "manager-a",
        app_52,
        {"decision": "APPROVE", "reasonCode": "POLICY_EXCEPTION_APPROVED"},
    )
    bundle.callback.flush()
    approved = bundle.api.get_application("customer-a", app_52)["application"]
    app_51 = create(bundle, "customer-b", 51, "fleet-51-rejected")["application"]["applicationId"]
    bundle.api.decide_approval(
        "manager-b",
        app_51,
        {"decision": "REJECT", "reasonCode": "INCOMPLETE_COMMERCIAL_CASE"},
    )
    bundle.callback.flush()
    rejected = bundle.api.get_application("customer-b", app_51)["application"]

    assert approved["status"] == "CONTRACT_ACTIVE"
    assert rejected["status"] == "REJECTED"


@pytest.mark.parametrize(
    ("decision", "reason_code"),
    [
        ("APPROVE", "CAPACITY_NOT_AVAILABLE"),
        ("REJECT", "CAPACITY_CONFIRMED"),
        ("APPROVE", "ARBITRARY_UPPERCASE_CODE"),
    ],
)
def test_manager_reason_code_allowlist_rejects_crossed_or_arbitrary_pairs(
    decision: str, reason_code: str
) -> None:
    bundle = build_local_bundle()
    application_id = create(bundle, "customer-a", 51, f"reason-{decision}-{reason_code}")[
        "application"
    ]["applicationId"]
    with pytest.raises(BadRequest, match="not allowed"):
        bundle.api.decide_approval(
            "manager-a",
            application_id,
            {"decision": decision, "reasonCode": reason_code},
        )


def test_idempotency_is_tenant_scoped_durable_and_rejects_changed_input() -> None:
    bundle = build_local_bundle()
    key = "retry-key-0123456789abcdef"
    first, first_replay = bundle.api.create_application("customer-a", {"vehicleCount": 50}, key)
    second, second_replay = bundle.api.create_application("customer-a", {"vehicleCount": 50}, key)
    other_tenant, _ = bundle.api.create_application("customer-b", {"vehicleCount": 50}, key)

    assert first_replay is False
    assert second_replay is True
    assert first["application"]["applicationId"] == second["application"]["applicationId"]
    assert other_tenant["application"]["applicationId"] != first["application"]["applicationId"]
    with pytest.raises(Conflict, match="different input"):
        bundle.api.create_application("customer-a", {"vehicleCount": 51}, key)


def test_cross_tenant_resource_and_decision_are_indistinguishable_404() -> None:
    bundle = build_local_bundle()
    application_id = create(bundle, "customer-a", 51, "private-fleet")["application"]["applicationId"]

    with pytest.raises(NotFound):
        bundle.api.get_application("customer-b", application_id)
    with pytest.raises(NotFound):
        bundle.api.decide_approval(
            "manager-b",
            application_id,
            {"decision": "APPROVE", "reasonCode": "CAPACITY_CONFIRMED"},
        )


def test_roles_are_resolved_server_side() -> None:
    bundle = build_local_bundle()
    application_id = create(bundle, "customer-a", 51, "role-check")["application"]["applicationId"]

    with pytest.raises(Forbidden):
        bundle.api.list_approvals("customer-a")
    with pytest.raises(Forbidden):
        bundle.api.decide_approval(
            "customer-a",
            application_id,
            {"decision": "APPROVE", "reasonCode": "CAPACITY_CONFIRMED"},
        )
    with pytest.raises(Forbidden):
        bundle.api.create_application(
            "manager-a", {"vehicleCount": 50}, "manager-key-0123456789"
        )


def test_body_cannot_override_tenant_or_actor() -> None:
    bundle = build_local_bundle()
    with pytest.raises(BadRequest, match="Only vehicleCount"):
        bundle.api.create_application(
            "customer-a",
            {"vehicleCount": 50, "tenantId": "tenant-b"},
            "override-key-0123456789",
        )


def test_daily_quota_is_hard_and_idempotent_replay_does_not_increment() -> None:
    bundle = build_local_bundle()
    first_key = "example-key-0001"
    bundle.api.create_application("customer-a", {"vehicleCount": 50}, first_key)
    bundle.api.create_application("customer-a", {"vehicleCount": 50}, first_key)
    for index in range(1, 10):
        create(bundle, "customer-a", 50, f"quota-{index:02d}")
    with pytest.raises(DailyQuotaExceeded):
        create(bundle, "customer-a", 50, "quota-11")


def test_expiration_is_fail_closed_for_api_and_worker() -> None:
    bundle = build_local_bundle()
    pending_id = create(bundle, "customer-a", 51, "expiry-fleet")["application"]["applicationId"]
    bundle.clock.advance(hours=193)

    assert bundle.api.health()["status"] == PilotStatus.EXPIRED.value
    for call in (
        lambda: bundle.api.me("customer-a"),
        lambda: bundle.api.list_applications("customer-a"),
        lambda: bundle.api.get_plan_plus("customer-a"),
        lambda: bundle.api.decide_approval(
            "manager-a",
            pending_id,
            {"decision": "APPROVE", "reasonCode": "CAPACITY_CONFIRMED"},
        ),
        lambda: bundle.worker.finalize_approved(
            tenant_id="tenant-a",
            application_id=pending_id,
            actor_id="actor_system",
            actor_role="MANAGER",
            reason_code="AUTO_APPROVED",
        ),
    ):
        with pytest.raises(PilotExpired):
            call()


def test_prepared_pilot_is_honest_and_fails_business_and_worker_as_not_ready() -> None:
    bundle = build_local_bundle()
    bundle.repository.pilot_status = PilotStatus.PREPARED
    bundle.repository.expires_at = None

    assert bundle.api.health() == {
        "status": "PREPARED",
        "expiresAt": None,
        "syntheticData": True,
        "pii": False,
    }
    assert bundle.api.me("customer-a")["role"] == "CUSTOMER"
    for call in (
        lambda: bundle.api.create_application(
            "customer-a", {"vehicleCount": 50}, "prepared-0123456789abcdef"
        ),
        lambda: bundle.worker.capture_approval_token(
            tenant_id="tenant-a",
            application_id="app_" + "a" * 32,
            task_token="task-token-" + "x" * 64,
        ),
    ):
        with pytest.raises(PilotNotReady) as captured:
            call()
        assert captured.value.status == 503
        assert captured.value.code == "PILOT_NOT_READY"


def test_public_dtos_have_no_callback_or_ciphertext_material() -> None:
    bundle = build_local_bundle()
    application_id = create(bundle, "customer-a", 51, "secret-scan")["application"]["applicationId"]
    public_values = [
        bundle.api.get_application("customer-a", application_id),
        bundle.api.list_applications("customer-a"),
        bundle.api.list_approvals("manager-a"),
    ]
    encoded = json.dumps(public_values).lower()
    for forbidden in ("tasktoken", "task_token", "ciphertext", "encrypted_token", "callback_claim"):
        assert forbidden not in encoded


def test_approval_token_is_single_use() -> None:
    bundle = build_local_bundle()
    application_id = create(bundle, "customer-a", 51, "single-use")["application"]["applicationId"]
    bundle.api.decide_approval(
        "manager-a",
        application_id,
        {"decision": "APPROVE", "reasonCode": "CAPACITY_CONFIRMED"},
    )
    with pytest.raises(Conflict):
        bundle.api.decide_approval(
            "manager-a",
            application_id,
            {"decision": "REJECT", "reasonCode": "CAPACITY_NOT_AVAILABLE"},
        )
