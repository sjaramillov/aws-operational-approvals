"""Entrada Lambda interna para tareas y callbacks de Step Functions."""

from __future__ import annotations

import json
import logging
from typing import Any

from .domain import Decision, parse_datetime
from .errors import BadRequest, PilotError
from .service import WorkerService


LOGGER = logging.getLogger("sales_demo.worker")
LOGGER.setLevel(logging.INFO)
_SERVICE: WorkerService | None = None


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    return handle_event(event, _service())


def handle_event(event: dict[str, Any], service: WorkerService) -> dict[str, Any]:
    """Ejecuta una acción allowlisted sin registrar el evento ni sus valores."""
    action = event.get("action") or event.get("operation")
    application_id = ""
    try:
        if action == "EXPIRE_PILOT":
            expected_count = event.get("expected_user_count")
            if isinstance(expected_count, bool) or not isinstance(expected_count, int):
                raise BadRequest("Invalid internal field: expected_user_count.")
            return service.expire_pilot(
                scheduled_expires_at=parse_datetime(
                    _required_string(event, "scheduled_expires_at", 64)
                ),
                expected_user_count=expected_count,
            )

        application_id = _required_string(event, "applicationId", 64)
        tenant_id = _required_string(event, "tenantId", 128)
        if action == "CAPTURE_APPROVAL_TOKEN":
            application = service.capture_approval_token(
                tenant_id=tenant_id,
                application_id=application_id,
                task_token=_required_string(event, "taskToken", 2048),
            )
        elif action == "COMPLETE_MANAGER_DECISION":
            accepted = service.complete_manager_decision(
                tenant_id=tenant_id,
                application_id=application_id,
                actor_id=_required_string(event, "actorId", 64),
                actor_role=_required_string(event, "actorRole", 16),
                decision=Decision(_required_string(event, "decision", 16)),
                reason_code=_required_string(event, "reasonCode", 64),
            )
            return {"ok": True, "decision": accepted}
        elif action == "FINALIZE_APPROVED":
            application = service.finalize_approved(
                tenant_id=tenant_id,
                application_id=application_id,
                actor_id=_required_string(event, "actorId", 64),
                actor_role=_required_string(event, "actorRole", 16),
                reason_code=_required_string(event, "reasonCode", 64),
            )
        elif action == "FINALIZE_REJECTED":
            application = service.finalize_rejected(
                tenant_id=tenant_id,
                application_id=application_id,
                actor_id=_required_string(event, "actorId", 64),
                actor_role=_required_string(event, "actorRole", 16),
                reason_code=_required_string(event, "reasonCode", 64),
            )
        else:
            raise BadRequest("Unknown worker action.")
        # Los finalizadores usan el DTO público allowlisted: nunca token.
        return {"ok": True, "application": application.public_dict()}
    except PilotError:
        raise
    except (TypeError, ValueError) as exc:
        raise BadRequest("Invalid worker command.") from exc
    finally:
        LOGGER.info(
            json.dumps(
                {
                    "event": "worker_action_completed",
                    "action": action if action in _ACTIONS else "UNKNOWN",
                    "applicationIdHash": _short_hash(application_id),
                }
            )
        )


def _service() -> WorkerService:
    global _SERVICE
    if _SERVICE is None:
        from .aws_adapters import build_worker_service

        _SERVICE = build_worker_service()
    return _SERVICE


_ACTIONS = {
    "CAPTURE_APPROVAL_TOKEN",
    "COMPLETE_MANAGER_DECISION",
    "FINALIZE_APPROVED",
    "FINALIZE_REJECTED",
    "EXPIRE_PILOT",
}


def _required_string(event: dict[str, Any], key: str, max_length: int) -> str:
    value = event.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise BadRequest(f"Invalid internal field: {key}.")
    return value.strip()


def _short_hash(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
