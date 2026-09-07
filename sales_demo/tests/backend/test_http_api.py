from __future__ import annotations

import json

from sales_demo.backend.domain import PilotStatus
from sales_demo.backend.lambda_api import handle_event
from sales_demo.backend.memory import build_local_bundle


def event(method: str, path: str, *, subject: str | None = None, body=None, headers=None):
    return {
        "version": "2.0",
        "rawPath": path,
        "headers": headers or {},
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {
            "requestId": "req-test-001",
            "http": {"method": method, "path": path},
            "authorizer": {"jwt": {"claims": {"sub": subject}}} if subject else {},
        },
    }


def body(response):
    return json.loads(response["body"])


def test_health_is_public_but_other_routes_require_cognito_sub() -> None:
    bundle = build_local_bundle()
    health = handle_event(event("GET", "/health"), bundle.api)
    me = handle_event(event("GET", "/me"), bundle.api)

    assert health["statusCode"] == 200
    assert body(health)["syntheticData"] is True
    assert body(health)["pii"] is False
    assert me["statusCode"] == 401
    assert me["headers"]["content-type"].startswith("application/problem+json")


def test_unexpected_failure_returns_sanitized_documented_500() -> None:
    class FailingService:
        def health(self):
            raise RuntimeError("private dependency detail")

    response = handle_event(event("GET", "/health"), FailingService())
    value = body(response)

    assert response["statusCode"] == 500
    assert value["code"] == "INTERNAL_ERROR"
    assert value["detail"] == "The request could not be completed."
    assert "private dependency detail" not in response["body"]


def test_create_and_replay_http_statuses_and_no_store_headers() -> None:
    bundle = build_local_bundle()
    request = event(
        "POST",
        "/applications",
        subject="customer-a",
        body={"vehicleCount": 50},
        headers={"Idempotency-Key": "http-retry-0123456789abcdef"},
    )
    created = handle_event(request, bundle.api)
    replayed = handle_event(request, bundle.api)

    assert created["statusCode"] == 201
    assert replayed["statusCode"] == 200
    assert body(replayed)["replayed"] is True
    assert created["headers"]["cache-control"] == "no-store"
    assert created["headers"]["x-content-type-options"] == "nosniff"


def test_cross_tenant_http_returns_same_problem_404() -> None:
    bundle = build_local_bundle()
    created = handle_event(
        event(
            "POST",
            "/applications",
            subject="customer-a",
            body={"vehicleCount": 51},
            headers={"idempotency-key": "cross-http-0123456789abcdef"},
        ),
        bundle.api,
    )
    application_id = body(created)["application"]["applicationId"]

    foreign = handle_event(
        event("GET", f"/applications/{application_id}", subject="customer-b"), bundle.api
    )
    missing = handle_event(
        event("GET", "/applications/app_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", subject="customer-b"),
        bundle.api,
    )
    assert foreign["statusCode"] == missing["statusCode"] == 404
    assert body(foreign)["code"] == body(missing)["code"] == "NOT_FOUND"


def test_expired_http_routes_return_410_while_health_reports_expired() -> None:
    bundle = build_local_bundle()
    bundle.clock.advance(hours=193)
    health = handle_event(event("GET", "/health"), bundle.api)
    me = handle_event(event("GET", "/me", subject="customer-a"), bundle.api)
    applications = handle_event(event("GET", "/applications", subject="customer-a"), bundle.api)

    assert body(health)["status"] == "EXPIRED"
    assert me["statusCode"] == 410
    assert applications["statusCode"] == 410
    assert body(applications)["code"] == "PILOT_EXPIRED"


def test_prepared_http_health_is_honest_and_business_returns_503_not_410() -> None:
    bundle = build_local_bundle()
    bundle.repository.pilot_status = PilotStatus.PREPARED
    bundle.repository.expires_at = None

    health = handle_event(event("GET", "/health"), bundle.api)
    me = handle_event(event("GET", "/me", subject="customer-a"), bundle.api)
    applications = handle_event(event("GET", "/applications", subject="customer-a"), bundle.api)

    assert health["statusCode"] == 200
    assert body(health)["status"] == "PREPARED"
    assert body(health)["expiresAt"] is None
    assert me["statusCode"] == 200
    assert body(me)["role"] == "CUSTOMER"
    assert applications["statusCode"] == 503
    assert body(applications)["code"] == "PILOT_NOT_READY"


def test_unknown_route_uses_problem_details() -> None:
    response = handle_event(event("DELETE", "/admin", subject="manager-a"), build_local_bundle().api)
    value = body(response)
    assert response["statusCode"] == 404
    assert set(value) == {"type", "title", "status", "detail", "instance", "code", "requestId"}


def test_manager_decision_is_eventual_202_with_safe_acceptance_dto() -> None:
    bundle = build_local_bundle()
    created = handle_event(
        event(
            "POST",
            "/applications",
            subject="customer-a",
            body={"vehicleCount": 51},
            headers={"idempotency-key": "eventual-http-0123456789abcdef"},
        ),
        bundle.api,
    )
    application_id = body(created)["application"]["applicationId"]
    response = handle_event(
        event(
            "POST",
            f"/approvals/{application_id}/decision",
            subject="manager-a",
            body={"decision": "APPROVE", "reasonCode": "CAPACITY_CONFIRMED"},
        ),
        bundle.api,
    )

    assert response["statusCode"] == 202
    assert body(response) == {
        "applicationId": application_id,
        "accepted": True,
        "status": "PENDING_MANAGER",
    }
    assert "token" not in response["body"].lower()
    assert "cipher" not in response["body"].lower()
