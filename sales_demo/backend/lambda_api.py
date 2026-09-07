"""Entrada Lambda para API Gateway HTTP API v2."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any

from .errors import BadRequest, PilotError
from .service import ApiService


LOGGER = logging.getLogger("sales_demo.api")
LOGGER.setLevel(logging.INFO)
MAX_BODY_BYTES = 16_384
APPLICATION_PATH = re.compile(r"^/applications/(app_[0-9a-f]{32})$")
DECISION_PATH = re.compile(r"^/approvals/(app_[0-9a-f]{32})/decision$")
FORBIDDEN_RESPONSE_KEY_PARTS = ("token", "ciphertext", "encrypted", "credential", "secret")
_SERVICE: ApiService | None = None


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    return handle_event(event, _service())


def handle_event(event: dict[str, Any], service: ApiService) -> dict[str, Any]:
    request_id = _request_id(event)
    method, path = _method_path(event)
    try:
        subject = _subject(event)
        if method == "GET" and path == "/health":
            return _json_response(200, service.health(), request_id)
        if method == "GET" and path == "/me":
            return _json_response(200, service.me(subject), request_id)
        if method == "POST" and path == "/applications":
            body = _json_body(event)
            payload, replayed = service.create_application(
                subject,
                body,
                _header(event, "idempotency-key"),
            )
            return _json_response(200 if replayed else 201, payload, request_id)
        if method == "GET" and path == "/applications":
            return _json_response(200, service.list_applications(subject), request_id)
        application_match = APPLICATION_PATH.fullmatch(path)
        if method == "GET" and application_match:
            return _json_response(
                200,
                service.get_application(subject, application_match.group(1)),
                request_id,
            )
        if method == "GET" and path == "/approvals":
            return _json_response(200, service.list_approvals(subject), request_id)
        decision_match = DECISION_PATH.fullmatch(path)
        if method == "POST" and decision_match:
            return _json_response(
                202,
                service.decide_approval(
                    subject,
                    decision_match.group(1),
                    _json_body(event),
                ),
                request_id,
            )
        if method == "GET" and path == "/plan-plus":
            return _json_response(200, service.get_plan_plus(subject), request_id)
        return _problem_response(
            BadRequest("The method and path do not match a pilot operation."),
            path,
            request_id,
            status_override=404,
            code_override="ROUTE_NOT_FOUND",
            title_override="Route not found",
        )
    except PilotError as exc:
        return _problem_response(exc, path, request_id)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return _problem_response(BadRequest(str(exc)), path, request_id)
    except Exception:
        LOGGER.exception(
            json.dumps(
                {"event": "request_failed", "requestId": request_id, "route": f"{method} {path}"}
            )
        )
        return _problem_response(PilotError(), path, request_id)


def _service() -> ApiService:
    global _SERVICE
    if _SERVICE is None:
        # Import tardío: los tests unitarios no requieren boto3 instalado.
        from .aws_adapters import build_api_service

        _SERVICE = build_api_service()
    return _SERVICE


def _method_path(event: dict[str, Any]) -> tuple[str, str]:
    http = event.get("requestContext", {}).get("http", {})
    method = str(http.get("method") or "").upper()
    path = str(event.get("rawPath") or http.get("path") or "/")
    return method, path


def _request_id(event: dict[str, Any]) -> str:
    value = str(event.get("requestContext", {}).get("requestId") or "request-unavailable")
    return value[:128]


def _subject(event: dict[str, Any]) -> str | None:
    value = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
        .get("sub")
    )
    return value if isinstance(value, str) else None


def _header(event: dict[str, Any], name: str) -> str | None:
    headers = event.get("headers") or {}
    for key, value in headers.items():
        if str(key).lower() == name:
            return str(value)
    return None


def _json_body(event: dict[str, Any]) -> Any:
    raw = event.get("body")
    if raw is None:
        raise BadRequest("A JSON request body is required.")
    if not isinstance(raw, str):
        raise BadRequest("The request body must be encoded as text.")
    encoded = raw.encode("utf-8")
    if event.get("isBase64Encoded"):
        try:
            encoded = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise BadRequest("The request body is not valid base64.") from exc
    if len(encoded) > MAX_BODY_BYTES:
        raise BadRequest("The request body exceeds 16 KiB.")
    return json.loads(encoded.decode("utf-8"))


def _assert_safe_payload(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in FORBIDDEN_RESPONSE_KEY_PARTS):
                raise RuntimeError(f"unsafe response key blocked at {path}.{key}")
            _assert_safe_payload(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_safe_payload(child, f"{path}[{index}]")


def _json_response(status: int, payload: dict[str, Any], request_id: str) -> dict[str, Any]:
    _assert_safe_payload(payload)
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
            "x-request-id": request_id,
        },
        "body": json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
    }


def _problem_response(
    error: PilotError,
    path: str,
    request_id: str,
    *,
    status_override: int | None = None,
    code_override: str | None = None,
    title_override: str | None = None,
) -> dict[str, Any]:
    status = status_override or error.status
    payload = {
        "type": f"https://approvals.example/problems/{(code_override or error.code).lower()}",
        "title": title_override or error.title,
        "status": status,
        "detail": error.detail,
        "instance": path,
        "code": code_override or error.code,
        "requestId": request_id,
    }
    response = _json_response(status, payload, request_id)
    response["headers"]["content-type"] = "application/problem+json; charset=utf-8"
    return response
