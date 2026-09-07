from __future__ import annotations

import io
import json

import pytest

from sales_demo.backend.aws_adapters import LambdaDecisionDispatcher, StepFunctionsWorkflow
from sales_demo.backend.domain import Decision, Identity, Role
from sales_demo.backend.errors import DependencyFailure


STATE_MACHINE_ARN = "arn:aws:states:us-east-1:<account_id>:stateMachine:approvals-sales"
APPLICATION_ID = "app_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


class ExecutionAlreadyExists(Exception):
    pass


class FakeStepFunctions:
    class exceptions:
        ExecutionAlreadyExists = ExecutionAlreadyExists

    def __init__(self, existing: dict | None = None) -> None:
        self.existing = existing
        self.started: list[dict] = []
        self.described: list[str] = []
        self.redriven: list[str] = []

    def start_execution(self, **kwargs):
        self.started.append(kwargs)
        if self.existing is not None:
            raise ExecutionAlreadyExists()
        return {"executionArn": "new"}

    def describe_execution(self, *, executionArn):
        self.described.append(executionArn)
        return self.existing

    def redrive_execution(self, *, executionArn):
        self.redriven.append(executionArn)
        return {"redriveDate": "synthetic"}


def expected_input(vehicle_count: int = 51) -> str:
    return json.dumps(
        {
            "tenantId": "tenant-a",
            "applicationId": APPLICATION_ID,
            "vehicleCount": vehicle_count,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def start(workflow: StepFunctionsWorkflow, vehicle_count: int = 51) -> None:
    workflow.start_application(
        tenant_id="tenant-a",
        application_id=APPLICATION_ID,
        vehicle_count=vehicle_count,
    )


def test_new_execution_starts_without_describe() -> None:
    client = FakeStepFunctions()
    start(StepFunctionsWorkflow(STATE_MACHINE_ARN, client=client))
    assert client.started[0]["input"] == expected_input()
    assert client.described == []


@pytest.mark.parametrize("status", ["RUNNING", "SUCCEEDED"])
def test_compatible_active_or_successful_execution_is_idempotent(status: str) -> None:
    client = FakeStepFunctions({"status": status, "input": expected_input()})
    start(StepFunctionsWorkflow(STATE_MACHINE_ARN, client=client))
    assert client.described == [
        "arn:aws:states:us-east-1:<account_id>:execution:approvals-sales:" + APPLICATION_ID
    ]
    assert client.redriven == []


def test_execution_name_collision_with_different_input_fails_closed() -> None:
    client = FakeStepFunctions({"status": "RUNNING", "input": expected_input(52)})
    with pytest.raises(DependencyFailure, match="incompatible input"):
        start(StepFunctionsWorkflow(STATE_MACHINE_ARN, client=client))


@pytest.mark.parametrize("status", ["FAILED", "TIMED_OUT", "ABORTED"])
def test_closed_execution_redrives_only_when_api_marks_redrivable(status: str) -> None:
    execution = {
        "status": status,
        "input": expected_input(),
        "redriveStatus": "REDRIVABLE",
    }
    client = FakeStepFunctions(execution)
    start(StepFunctionsWorkflow(STATE_MACHINE_ARN, client=client))
    assert client.redriven == [
        "arn:aws:states:us-east-1:<account_id>:execution:approvals-sales:" + APPLICATION_ID
    ]


@pytest.mark.parametrize("status", ["FAILED", "TIMED_OUT", "ABORTED"])
def test_closed_execution_without_redrivable_signal_is_dependency_failure(status: str) -> None:
    client = FakeStepFunctions(
        {"status": status, "input": expected_input(), "redriveStatus": "NOT_REDRIVABLE"}
    )
    with pytest.raises(DependencyFailure, match="not REDRIVABLE"):
        start(StepFunctionsWorkflow(STATE_MACHINE_ARN, client=client))
    assert client.redriven == []


def test_unknown_closed_status_never_becomes_false_success() -> None:
    client = FakeStepFunctions({"status": "PENDING_REDRIVE", "input": expected_input()})
    with pytest.raises(DependencyFailure, match="unsupported status"):
        start(StepFunctionsWorkflow(STATE_MACHINE_ARN, client=client))


class FakeLambda:
    def __init__(self, result: dict, *, function_error: bool = False) -> None:
        self.result = result
        self.function_error = function_error

    def invoke(self, **_kwargs):
        response = {"Payload": io.BytesIO(json.dumps(self.result).encode())}
        if self.function_error:
            response["FunctionError"] = "Unhandled"
        return response


def test_lambda_dispatcher_accepts_only_safe_eventual_dto() -> None:
    accepted = {
        "applicationId": APPLICATION_ID,
        "accepted": True,
        "status": "PENDING_MANAGER",
    }
    dispatcher = LambdaDecisionDispatcher(
        "synthetic-worker",
        client=FakeLambda({"ok": True, "decision": accepted}),
    )
    result = dispatcher.dispatch(
        tenant_id="tenant-a",
        application_id=APPLICATION_ID,
        actor=Identity("manager-a", "tenant-a", Role.MANAGER, "Manager A"),
        decision=Decision.APPROVE,
        reason_code="CAPACITY_CONFIRMED",
    )
    assert result == accepted


def test_lambda_dispatcher_rejects_false_final_or_function_error() -> None:
    actor = Identity("manager-a", "tenant-a", Role.MANAGER, "Manager A")
    incompatible = LambdaDecisionDispatcher(
        "synthetic-worker",
        client=FakeLambda(
            {
                "decision": {
                    "applicationId": APPLICATION_ID,
                    "accepted": True,
                    "status": "CONTRACT_ACTIVE",
                }
            }
        ),
    )
    with pytest.raises(DependencyFailure, match="incompatible acceptance"):
        incompatible.dispatch(
            tenant_id="tenant-a",
            application_id=APPLICATION_ID,
            actor=actor,
            decision=Decision.APPROVE,
            reason_code="CAPACITY_CONFIRMED",
        )
    failed = LambdaDecisionDispatcher(
        "synthetic-worker", client=FakeLambda({}, function_error=True)
    )
    with pytest.raises(DependencyFailure, match="rejected"):
        failed.dispatch(
            tenant_id="tenant-a",
            application_id=APPLICATION_ID,
            actor=actor,
            decision=Decision.REJECT,
            reason_code="INCOMPLETE_COMMERCIAL_CASE",
        )
