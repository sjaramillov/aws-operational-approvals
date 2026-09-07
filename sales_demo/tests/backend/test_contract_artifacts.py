from __future__ import annotations

import json
import inspect
from pathlib import Path

from sales_demo.backend.aws_adapters import DynamoRepository
from sales_demo.backend.memory import InMemoryRepository
from sales_demo.backend.ports import Repository


ROOT = Path(__file__).resolve().parents[3]
OPENAPI = ROOT / "sales_demo" / "openapi.yaml"
WORKFLOW = ROOT / "sales_demo" / "workflow.asl.json"


def test_openapi_exposes_exact_bounded_routes_and_states() -> None:
    text = OPENAPI.read_text(encoding="utf-8")
    for route in (
        "/health:",
        "/me:",
        "/applications:",
        "/applications/{applicationId}:",
        "/approvals:",
        "/approvals/{applicationId}/decision:",
        "/plan-plus:",
    ):
        assert text.count(f"  {route}") == 1
    assert "enum: [PENDING_MANAGER, APPROVED, REJECTED, CONTRACT_ACTIVE, EXPIRED]" in text
    assert "additionalProperties: false" in text
    assert "Idempotency-Key" in text


def test_openapi_public_schemas_never_define_sensitive_callback_fields() -> None:
    text = OPENAPI.read_text(encoding="utf-8")
    for field in (
        "taskToken:",
        "task_token:",
        "ciphertext:",
        "encryptedToken:",
        "callbackClaimId:",
    ):
        assert field not in text


def test_openapi_models_eventual_decision_and_real_error_surface() -> None:
    import yaml

    document = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    health = document["components"]["schemas"]["Health"]
    assert health["properties"]["status"]["enum"] == ["PREPARED", "ACTIVE", "EXPIRED"]
    assert {branch["type"] for branch in health["properties"]["expiresAt"]["oneOf"]} == {
        "string",
        "null",
    }
    decision = document["paths"]["/approvals/{applicationId}/decision"]["post"]
    assert "202" in decision["responses"]
    assert "200" not in decision["responses"]
    schema = document["components"]["schemas"]["DecisionAccepted"]
    assert schema["required"] == ["applicationId", "accepted", "status"]
    for operation_path, method in (
        ("/applications", "post"),
        ("/approvals/{applicationId}/decision", "post"),
    ):
        responses = document["paths"][operation_path][method]["responses"]
        for status in ("400", "401", "403", "503"):
            assert status in responses

    operations = [
        path_item[method]
        for path_item in document["paths"].values()
        for method in ("get", "post")
        if method in path_item
    ]
    assert len(operations) == 8
    assert all("500" in operation["responses"] for operation in operations)
    assert "InternalError" in document["components"]["responses"]

    manager_decision = document["components"]["schemas"]["ManagerDecision"]
    variants = {
        variant["properties"]["decision"]["const"]: set(
            variant["properties"]["reasonCode"]["enum"]
        )
        for variant in manager_decision["oneOf"]
    }
    assert variants == {
        "APPROVE": {"CAPACITY_CONFIRMED", "POLICY_EXCEPTION_APPROVED"},
        "REJECT": {"CAPACITY_NOT_AVAILABLE", "INCOMPLETE_COMMERCIAL_CASE"},
    }
    assert variants["APPROVE"].isdisjoint(variants["REJECT"])


def test_workflow_is_standard_callback_shape_with_one_hour_timeout() -> None:
    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    wait = workflow["States"]["WaitForManager"]
    assert wait["Resource"] == "arn:aws:states:::lambda:invoke.waitForTaskToken"
    assert workflow["TimeoutSeconds"] == 3600
    assert wait["TimeoutSeconds"] == 3540
    assert wait["Parameters"]["Payload"]["taskToken.$"] == "$$.Task.Token"
    assert workflow["States"]["RequiresManager"]["Choices"][0]["NumericGreaterThan"] == 50
    serialized = json.dumps(workflow)
    assert "51" not in serialized and "52" not in serialized
    assert "MANAGER_CALLBACK_FAILED" not in serialized
    assert workflow["States"]["TechnicalCallbackFailure"]["Type"] == "Fail"
    catch_by_error = {
        tuple(branch["ErrorEquals"]): branch["Next"] for branch in wait["Catch"]
    }
    assert catch_by_error[("States.Timeout",)] == "RejectApprovalTimeout"
    assert catch_by_error[("States.ALL",)] == "TechnicalCallbackFailure"
    lambda_tasks = [
        state
        for state in workflow["States"].values()
        if state.get("Type") == "Task" and "lambda:invoke" in state.get("Resource", "")
    ]
    assert len(lambda_tasks) == 5
    for task in lambda_tasks:
        assert task["Retry"] == [
            {
                "ErrorEquals": [
                    "Lambda.ServiceException",
                    "Lambda.AWSLambdaException",
                    "Lambda.SdkClientException",
                    "Lambda.ClientExecutionTimeoutException",
                    "Lambda.TooManyRequestsException",
                ],
                "IntervalSeconds": 2,
                "MaxAttempts": 3,
                "BackoffRate": 2,
                "JitterStrategy": "FULL",
            }
        ]


def test_worker_environment_contract_is_explicit() -> None:
    source = (ROOT / "sales_demo" / "backend" / "aws_adapters.py").read_text(encoding="utf-8")
    for name in (
        "SALES_PILOT_ID",
        "SALES_TABLE_NAME",
        "SALES_STATE_MACHINE_ARN",
        "SALES_WORKER_FUNCTION_NAME",
        "SALES_KMS_KEY_ID",
        "SALES_DAILY_LIMIT",
        "SALES_USER_POOL_ID",
        "SALES_SYNTHETIC_USERNAMES",
    ):
        assert name in source


def test_repository_adapters_match_port_keyword_contract() -> None:
    methods = (
        "pilot_snapshot",
        "resolve_identity",
        "create_application",
        "get_application",
        "list_applications",
        "list_pending_approvals",
        "get_plan_plus",
        "store_approval_token",
        "claim_approval_decision",
        "activate_plan",
        "reject_application",
        "expire_pilot",
    )
    for method in methods:
        expected = list(inspect.signature(getattr(Repository, method)).parameters)
        assert list(inspect.signature(getattr(InMemoryRepository, method)).parameters) == expected
        assert list(inspect.signature(getattr(DynamoRepository, method)).parameters) == expected


def test_callback_cleanup_is_only_reachable_from_atomic_finalizers() -> None:
    sources = {
        path.name: path.read_text(encoding="utf-8")
        for path in (ROOT / "sales_demo" / "backend").glob("*.py")
    }
    assert "consume_approval_token" not in "\n".join(sources.values())
    assert "release_approval_claim" not in "\n".join(sources.values())
    adapter = sources["aws_adapters.py"]
    assert adapter.count("REMOVE approval_token_ciphertext, approval_token_fingerprint") == 2
    assert "AND approval_token_fingerprint = :fingerprint" in adapter
