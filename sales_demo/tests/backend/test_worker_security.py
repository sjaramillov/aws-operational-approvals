from __future__ import annotations

import hashlib
import json
import logging

import pytest

from sales_demo.backend.domain import Decision, isoformat
from sales_demo.backend.errors import BadRequest, Conflict, DependencyFailure
from sales_demo.backend.lambda_worker import handle_event
from sales_demo.backend.memory import LocalTokenVault, build_local_bundle


def _pending(bundle):
    return bundle.api.create_application(
        "customer-a",
        {"vehicleCount": 51},
        "pending-secret-0123456789abcdef",
    )[0]["application"]["applicationId"]


def test_local_vault_binds_ciphertext_to_tenant_and_application() -> None:
    vault = LocalTokenVault(b"x" * 32)
    token = "local-task-token-" + "a" * 64
    encrypted = vault.encrypt(token, tenant_id="tenant-a", application_id="app_" + "1" * 32)

    assert token not in encrypted
    assert vault.decrypt(encrypted, tenant_id="tenant-a", application_id="app_" + "1" * 32) == token
    with pytest.raises(ValueError, match="context mismatch"):
        vault.decrypt(encrypted, tenant_id="tenant-b", application_id="app_" + "1" * 32)


def test_worker_response_and_logs_never_contain_task_token(caplog) -> None:
    bundle = build_local_bundle()
    application_id = _pending(bundle)
    # Un Retry de Step Functions puede emitir otro token. Mientras el estado es
    # READY se sustituye de forma segura, sin duplicar auditoría.
    token = "local-task-token-" + "sensitive-value-" * 8
    with caplog.at_level(logging.INFO):
        result = handle_event(
            {
                "action": "CAPTURE_APPROVAL_TOKEN",
                "tenantId": "tenant-a",
                "applicationId": application_id,
                "taskToken": token,
            },
            bundle.worker,
        )
    encoded = json.dumps(result)
    assert token not in encoded
    assert token not in caplog.text
    assert "ciphertext" not in encoded.lower()
    secret = bundle.repository.approval_secrets[("tenant-a", application_id)]
    assert secret.token_fingerprint == hashlib.sha256(token.encode()).hexdigest()


def test_callback_rejects_token_larger_than_step_functions_api_limit() -> None:
    bundle = build_local_bundle()
    application_id = _pending(bundle)
    with pytest.raises(BadRequest, match="Invalid Step Functions task token"):
        bundle.worker.capture_approval_token(
            tenant_id="tenant-a",
            application_id=application_id,
            task_token="x" * 2049,
        )


def test_internal_manager_command_revalidates_decision_reason_pair_before_claim() -> None:
    bundle = build_local_bundle()
    application_id = _pending(bundle)
    secret = bundle.repository.approval_secrets[("tenant-a", application_id)]

    with pytest.raises(BadRequest, match="not allowed"):
        bundle.worker.complete_manager_decision(
            tenant_id="tenant-a",
            application_id=application_id,
            actor_id="actor_1234567890abcdef",
            actor_role="MANAGER",
            decision=Decision.APPROVE,
            reason_code="CAPACITY_NOT_AVAILABLE",
        )

    assert secret.state == "READY"
    assert secret.claim_id is None


def test_callback_failure_leaves_lease_and_reclaims_compatible_decision_after_t31() -> None:
    bundle = build_local_bundle()
    application_id = _pending(bundle)
    original = bundle.callback.send_success

    def fail_once(*, token, output):
        raise RuntimeError("synthetic dependency failure")

    bundle.callback.send_success = fail_once
    with pytest.raises(DependencyFailure):
        bundle.worker.complete_manager_decision(
            tenant_id="tenant-a",
            application_id=application_id,
            actor_id="actor_1234567890abcdef",
            actor_role="MANAGER",
            decision=Decision.APPROVE,
            reason_code="CAPACITY_CONFIRMED",
        )
    bundle.callback.send_success = original
    with pytest.raises(Conflict, match="in progress"):
        bundle.worker.complete_manager_decision(
            tenant_id="tenant-a",
            application_id=application_id,
            actor_id="actor_1234567890abcdef",
            actor_role="MANAGER",
            decision=Decision.APPROVE,
            reason_code="CAPACITY_CONFIRMED",
        )
    bundle.clock.advance(seconds=31)
    accepted = bundle.worker.complete_manager_decision(
        tenant_id="tenant-a",
        application_id=application_id,
        actor_id="actor_1234567890abcdef",
        actor_role="MANAGER",
        decision=Decision.APPROVE,
        reason_code="CAPACITY_CONFIRMED",
    )
    assert accepted == {
        "applicationId": application_id,
        "accepted": True,
        "status": "PENDING_MANAGER",
    }
    bundle.callback.flush()
    application = bundle.repository.get_application("tenant-a", application_id)
    assert application is not None and application.status.value == "CONTRACT_ACTIVE"


def test_successful_callback_writes_nothing_after_send_and_finalizer_cleans_atomically() -> None:
    bundle = build_local_bundle()
    application_id = _pending(bundle)
    accepted = bundle.worker.complete_manager_decision(
        tenant_id="tenant-a",
        application_id=application_id,
        actor_id="actor_1234567890abcdef",
        actor_role="MANAGER",
        decision=Decision.APPROVE,
        reason_code="CAPACITY_CONFIRMED",
    )

    assert accepted["status"] == "PENDING_MANAGER"
    secret = bundle.repository.approval_secrets[("tenant-a", application_id)]
    assert secret.state == "CLAIMED"
    assert secret.encrypted_token
    before = bundle.repository.get_application("tenant-a", application_id)
    assert before is not None
    assert [entry.action.value for entry in before.audit_trail] == [
        "APPLICATION_SUBMITTED",
        "MANAGER_APPROVAL_REQUESTED",
    ]

    bundle.callback.flush()
    assert ("tenant-a", application_id) not in bundle.repository.approval_secrets
    final = bundle.repository.get_application("tenant-a", application_id)
    assert final is not None and final.status.value == "CONTRACT_ACTIVE"
    assert [entry.action.value for entry in final.audit_trail][-2:] == [
        "MANAGER_DECISION_RECORDED",
        "CONTRACT_ACTIVATED",
    ]


def test_capture_token_rejects_non_pending_application() -> None:
    bundle = build_local_bundle()
    application_id = bundle.api.create_application(
        "customer-a", {"vehicleCount": 50}, "auto-secret-0123456789abcdef"
    )[0]["application"]["applicationId"]
    with pytest.raises(Conflict):
        bundle.worker.capture_approval_token(
            tenant_id="tenant-a",
            application_id=application_id,
            task_token="local-task-token-" + "x" * 64,
        )


def test_expiry_action_marks_pilot_and_disables_exactly_four_users_idempotently() -> None:
    bundle = build_local_bundle()
    bundle.clock.advance(hours=192)
    event = {
        "operation": "EXPIRE_PILOT",
        "scheduled_expires_at": isoformat(bundle.repository.expires_at),
        "expected_user_count": 4,
    }

    first = handle_event(event, bundle.worker)
    second = handle_event(event, bundle.worker)

    assert first == {
        "ok": True,
        "pilotStatus": "EXPIRED",
        "stateChanged": True,
        "usersDisabled": 4,
    }
    assert second["stateChanged"] is False
    assert bundle.user_disabler.disabled == {
        "customer-a",
        "manager-a",
        "customer-b",
        "manager-b",
    }
    assert bundle.api.health()["status"] == "EXPIRED"


def test_expiry_action_rejects_early_mismatched_or_expanded_scope() -> None:
    bundle = build_local_bundle()
    valid_timestamp = isoformat(bundle.repository.expires_at)

    with pytest.raises(Conflict, match="before"):
        handle_event(
            {
                "operation": "EXPIRE_PILOT",
                "scheduled_expires_at": valid_timestamp,
                "expected_user_count": 4,
            },
            bundle.worker,
        )
    bundle.clock.advance(hours=192)
    with pytest.raises(BadRequest, match="exactly 4"):
        handle_event(
            {
                "operation": "EXPIRE_PILOT",
                "scheduled_expires_at": valid_timestamp,
                "expected_user_count": 5,
            },
            bundle.worker,
        )
    assert bundle.user_disabler.disabled == set()
