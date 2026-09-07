#!/usr/bin/env python3
"""Fail-closed checks for the isolated ``sales_demo`` Terraform root.

This guard never calls AWS. ``--static-only`` is the pre-plan gate; the normal
mode additionally consumes ``terraform show -json`` output stored with mode
0600. Real identifiers remain in that private input and are never echoed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


IAM_MANAGED_POLICY_CHARACTER_LIMIT = 6144
MAX_FRONTEND_BUILD_BYTES = 10 * 1024 * 1024
ACCOUNT_ID_RE = re.compile(r"(?<![0-9])[0-9]{12}(?![0-9])")
RESOURCE_RE = re.compile(
    r'\bresource\s+"(?P<type>[a-z0-9_]+)"\s+"(?P<name>[A-Za-z0-9_]+)"\s*\{'
)
REQUIRED_STATIC_DECLARATIONS = {
    "aws_apigatewayv2_api": 1,
    "aws_apigatewayv2_authorizer": 1,
    "aws_apigatewayv2_integration": 1,
    "aws_apigatewayv2_route": 1,
    "aws_apigatewayv2_stage": 1,
    "aws_cloudfront_cache_policy": 1,
    "aws_cloudfront_distribution": 1,
    "aws_cloudfront_origin_access_control": 1,
    "aws_cloudfront_response_headers_policy": 1,
    "aws_cloudwatch_log_group": 3,
    "aws_cognito_user_group": 2,
    "aws_cognito_user_pool": 1,
    "aws_cognito_user_pool_client": 1,
    "aws_cognito_user_pool_domain": 1,
    "aws_cognito_user_pool_ui_customization": 1,
    "aws_dynamodb_table": 1,
    "aws_dynamodb_table_item": 1,
    "aws_iam_policy": 4,
    "aws_iam_role": 4,
    "aws_iam_role_policy_attachment": 4,
    "aws_lambda_function": 2,
    "aws_lambda_permission": 1,
    "aws_s3_bucket": 1,
    "aws_s3_bucket_lifecycle_configuration": 1,
    "aws_s3_bucket_ownership_controls": 1,
    "aws_s3_bucket_policy": 1,
    "aws_s3_bucket_public_access_block": 1,
    "aws_s3_bucket_server_side_encryption_configuration": 1,
    "aws_s3_bucket_versioning": 1,
    "aws_scheduler_schedule": 1,
    "aws_sfn_state_machine": 1,
    "terraform_data": 2,
}
REQUIRED_PLAN_COUNTS = dict(REQUIRED_STATIC_DECLARATIONS, aws_apigatewayv2_route=8)
REQUIRED_ROUTES = {
    "GET /health",
    "GET /me",
    "POST /applications",
    "GET /applications",
    "GET /applications/{id}",
    "GET /approvals",
    "POST /approvals/{id}/decision",
    "GET /plan-plus",
}
LIVE_PREFLIGHT_SCHEMA = "sales-live-preflight/1.0"


class SalesPlanGuardError(RuntimeError):
    """Raised when the commercial pilot cannot be proven bounded."""


def _tf_files(terraform_dir: Path) -> list[Path]:
    files = sorted(terraform_dir.glob("*.tf"))
    if not files:
        raise SalesPlanGuardError("No Terraform source files found")
    return files


def validate_static(terraform_dir: Path) -> dict[str, Any]:
    """Validate source invariants without evaluating a plan."""

    files = _tf_files(terraform_dir)
    joined = "\n".join(path.read_text(encoding="utf-8") for path in files)
    workflow_path = terraform_dir.parent / "workflow.asl.json"
    try:
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SalesPlanGuardError("Canonical workflow.asl.json is missing or invalid") from exc
    resources = Counter(match.group("type") for match in RESOURCE_RE.finditer(joined))

    unexpected_types = sorted(set(resources) - set(REQUIRED_STATIC_DECLARATIONS))
    if unexpected_types:
        raise SalesPlanGuardError(
            "Unexpected managed resource declarations: " + ", ".join(unexpected_types)
        )
    for resource_type, expected in REQUIRED_STATIC_DECLARATIONS.items():
        actual = resources[resource_type]
        if actual != expected:
            raise SalesPlanGuardError(
                f"Expected exactly {expected} {resource_type} resources; found {actual}"
            )

    if ACCOUNT_ID_RE.search(joined):
        raise SalesPlanGuardError("A literal 12-digit account identifier is versioned in HCL")
    if "reserved_concurrent_executions" in joined:
        raise SalesPlanGuardError("Reserved concurrency is forbidden for the quota-10 pilot")
    if "ALLOW_REFRESH_TOKEN_AUTH" in joined:
        raise SalesPlanGuardError(
            "ALLOW_REFRESH_TOKEN_AUTH is incompatible with refresh-token rotation"
        )
    if re.search(
        r'explicit_auth_flows\s*=\s*\["ALLOW_USER_SRP_AUTH"\]', joined
    ) is None:
        raise SalesPlanGuardError(
            "Cognito explicit auth flows must exclude the incompatible default"
        )
    if "https://*.auth." in joined or "https://*.execute-api." in joined:
        raise SalesPlanGuardError("CloudFront CSP must not use regional auth/API wildcards")
    if 'backend "s3" {}' not in joined:
        raise SalesPlanGuardError("The isolated root must declare its own S3 backend")
    if "allowed_account_ids = [var.expected_aws_account_id]" not in joined:
        raise SalesPlanGuardError("Provider must fail closed on expected_aws_account_id")
    if 'var.deployment_profile == "sales_demo"' not in joined:
        raise SalesPlanGuardError("deployment_profile is not pinned to sales_demo")
    if joined.count("< 6144") < 4:
        raise SalesPlanGuardError("All four runtime policies need a 6,144-character precondition")
    if "resource \"aws_iam_policy\" \"runtime_boundary\"" in joined:
        raise SalesPlanGuardError("The workload must not create its own permissions boundary")
    if joined.count(
        "permissions_boundary = var.application_role_permissions_boundary_arn"
    ) != 4:
        raise SalesPlanGuardError("All four roles must use the guardrails-owned boundary")

    missing_routes = sorted(route for route in REQUIRED_ROUTES if f'"{route}"' not in joined)
    if missing_routes:
        raise SalesPlanGuardError("Missing HTTP routes: " + ", ".join(missing_routes))

    required_literals = {
        'billing_mode = "PROVISIONED"': "DynamoDB must use provisioned capacity",
        "read_capacity  = 5": "DynamoDB must keep 5 RCU",
        "write_capacity = 5": "DynamoDB must keep 5 WCU",
        "retention_in_days = 14": "CloudWatch logs must retain 14 days",
        "allow_admin_create_user_only = true": "Cognito self-signup must stay disabled",
        "generate_secret": "Cognito public-client setting is missing",
        'signing_behavior                  = "always"': "CloudFront OAC must always sign",
        'force_destroy = false': "S3 bucket must not be destructively emptied by Terraform",
        "allow_credentials = false": "HTTP API CORS must never use browser credentials",
        "connect-src 'self' ${aws_apigatewayv2_api.sales.api_endpoint}": "CSP must bind connect-src to the exact API",
        "form-action 'self' https://${aws_cognito_user_pool_domain.pilot.domain}": "CSP must bind form-action to the exact Cognito domain",
        'authorization_scopes = (': "JWT routes must require an access-token scope",
        'enabled = false': "Ephemeral DynamoDB PITR must stay disabled for inventory-zero teardown",
    }
    for literal, message in required_literals.items():
        if literal not in joined:
            raise SalesPlanGuardError(message)

    if not re.search(r"generate_secret\s*=\s*false", joined):
        raise SalesPlanGuardError("Cognito client secret must remain disabled")
    if not re.search(r'type\s*=\s*"STANDARD"', joined):
        raise SalesPlanGuardError("Step Functions must remain Standard")
    if "templatefile(\"${path.module}/../workflow.asl.json\"" not in joined:
        raise SalesPlanGuardError("Terraform must consume the canonical backend ASL")
    if not re.search(r'allowed_oauth_flows\s*=\s*\["code"\]', joined):
        raise SalesPlanGuardError("Authorization Code flow is missing")
    if joined.count("throttling_rate_limit") != 2 or joined.count(
        "throttling_burst_limit"
    ) != 2:
        raise SalesPlanGuardError("Stage and write-route throttling targets must both exist")

    integration_literals = {
        'handler       = "sales_demo.backend.lambda_api.lambda_handler"': "API handler does not match the backend package",
        'handler       = "sales_demo.backend.lambda_worker.lambda_handler"': "Worker handler does not match the backend package",
        "SALES_PILOT_ID": "Lambda environment is missing SALES_PILOT_ID",
        "SALES_TABLE_NAME": "Lambda environment is missing SALES_TABLE_NAME",
        "SALES_STATE_MACHINE_ARN": "API environment is missing its Standard workflow",
        "SALES_WORKER_FUNCTION_NAME": "API environment is missing the decision worker",
        "SALES_DAILY_LIMIT": "API environment is missing the hard daily limit",
        "SALES_KMS_KEY_ID": "Worker environment is missing its token CMK",
        "SALES_USER_POOL_ID": "Worker environment is missing its Cognito pool",
        "SALES_SYNTHETIC_USERNAMES": "Worker environment is missing its exact expiry user set",
        'pk                       = { S = "PILOT#${local.pilot_id}" }': "Pilot seed key does not match the backend",
        'sk                       = { S = "META" }': "Pilot seed sort key does not match the backend",
        'status                   = { S = "PREPARED" }': "Pilot seed must start PREPARED",
        "expires_at_epoch": "Pilot seed is missing the numeric expiry",
        'operation            = "EXPIRE_PILOT"': "Scheduler is missing EXPIRE_PILOT",
        "scheduled_expires_at": "Scheduler payload uses the wrong expiry field",
        "expected_user_count  = 4": "Scheduler must fail closed on exactly four users",
        'state                        = "DISABLED"': "Expiry schedule must stay disabled before post-readiness activation",
        'schedule_expression          = "at(2099-01-01T00:00:00)"': "Terraform expiry schedule must be a disabled placeholder",
        'feature                    = "ENABLED"': "Cognito refresh-token rotation must remain enabled",
        "retry_grace_period_seconds = 10": "Cognito refresh-token rotation grace has drifted",
        'user_pool_tier           = "LITE"': "Cognito must remain on the bounded Lite tier",
        "managed_login_version = 1": "Cognito domain must use the classic Hosted UI",
        'css          = ".banner-customizable { background-color: #0b2a3c; } .submitButton-customizable { background-color: #0b6b5b; }"': "Classic Hosted UI customization is missing",
        'type                              = "CUSTOMER_MANAGED_KMS_KEY"': "Standard execution history is not CMK encrypted",
        'variable = "kms:EncryptionContext:pilot"': "Task-token KMS policy is not bound to pilot context",
        'variable = "kms:EncryptionContext:tenant"': "Task-token KMS policy does not require tenant context",
        'variable = "kms:EncryptionContext:application"': "Task-token KMS policy does not require application context",
    }
    for literal, message in integration_literals.items():
        if literal not in joined:
            raise SalesPlanGuardError(message)
    if not re.search(
        r'actions\s*=\s*\["states:DescribeExecution",\s*"states:RedriveExecution"\]',
        joined,
    ):
        raise SalesPlanGuardError("API retry repair permissions are incomplete")

    provisioning_script = terraform_dir / "provision_users.py"
    try:
        provisioning_text = provisioning_script.read_text(encoding="utf-8")
    except OSError as exc:
        raise SalesPlanGuardError("Secure post-deploy user provisioner is missing") from exc
    for literal in (
        "getpass",
        'MessageAction="SUPPRESS"',
        "APPROVALS-TerraformDeploymentRole",
        "list_users",
        "admin_list_groups_for_user",
        "dynamodb.scan",
        'f"IDENTITY#{subject_hash}"',
        'ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)"',
    ):
        if literal not in provisioning_text:
            raise SalesPlanGuardError("Post-deploy user provisioner lost a fail-closed control")

    publisher_script = terraform_dir / "publish_frontend.py"
    try:
        publisher_text = publisher_script.read_text(encoding="utf-8")
    except OSError as exc:
        raise SalesPlanGuardError("Fail-closed frontend publisher is missing") from exc
    for literal in (
        "APPROVALS-TerraformDeploymentRole",
        "runtime-config.json must be supplied post-apply",
        '"no-store"',
        "list_object_versions",
        "delete_objects",
        "abort_multipart_upload",
        "Versioned bucket is non-empty without a trusted manifest",
        '"status": "PREPARING"',
        "validate_prepared_runtime_config",
        "verify_source_revision",
        "does not match the exact live Terraform targets",
        "S3 cache-control verification failed",
    ):
        if literal not in publisher_text:
            raise SalesPlanGuardError("Frontend publisher lost a publication/cleanup control")

    readiness_script = terraform_dir / "readiness_probe.py"
    try:
        readiness_text = readiness_script.read_text(encoding="utf-8")
    except OSError as exc:
        raise SalesPlanGuardError("Public PREPARED readiness probe is missing") from exc
    for literal in (
        "PREPARED_READY",
        "validate_prepared_runtime_config",
        'fetcher(api_base + "/me").status != 401',
        '"t0_started": False',
        '"business_effects": 0',
    ):
        if literal not in readiness_text:
            raise SalesPlanGuardError("PREPARED readiness probe lost a fail-closed control")

    preflight_script = terraform_dir / "preflight.py"
    try:
        preflight_text = preflight_script.read_text(encoding="utf-8")
    except OSError as exc:
        raise SalesPlanGuardError("Live sales preflight is missing") from exc
    for literal in (
        'SCHEMA = "sales-live-preflight/1.0"',
        'value.add_argument("--source-profile", required=True)',
        'value.add_argument("--profile", required=True)',
        '"plan_type": "FREE"',
        '"status": "WORKLOAD_ZERO"',
        '"kinesis_streams": 0',
        '"telemetry_topic_rules": 0',
        '"exact_sales_policy_set": True',
        "assert_sanitized",
        "write_private",
    ):
        if literal not in preflight_text:
            raise SalesPlanGuardError("Live sales preflight lost a fail-closed control")

    activation_script = terraform_dir / "activate_pilot.py"
    try:
        activation_text = activation_script.read_text(encoding="utf-8")
    except OSError as exc:
        raise SalesPlanGuardError("Post-readiness pilot activator is missing") from exc
    for literal in (
        "PREPARED -> ACTIVE",
        '"ConditionExpression": "#status = :prepared AND expires_at_epoch = :zero"',
        "active_window_hours",
        "runtime_config_no_store",
        "rollback_schedule",
        "require_complete=True",
        "require_exact_t0",
        "verify_source_revision",
        "AmbiguousActivationError",
        "keep expiry armed",
    ):
        if literal not in activation_text:
            raise SalesPlanGuardError("Post-readiness activator lost a fail-closed control")

    if workflow.get("TimeoutSeconds") != 3600:
        raise SalesPlanGuardError("Canonical workflow must have a 3,600-second global timeout")
    callback_states = [
        state
        for state in workflow.get("States", {}).values()
        if state.get("Resource") == "arn:aws:states:::lambda:invoke.waitForTaskToken"
    ]
    if len(callback_states) != 1 or callback_states[0].get("TimeoutSeconds") != 3540:
        raise SalesPlanGuardError("Canonical callback must reserve 60 seconds for terminal handling")
    workflow_text = json.dumps(workflow, sort_keys=True)
    for required_action in (
        "CAPTURE_APPROVAL_TOKEN",
        "FINALIZE_APPROVED",
        "FINALIZE_REJECTED",
    ):
        if required_action not in workflow_text:
            raise SalesPlanGuardError(f"Canonical workflow is missing {required_action}")
    for forbidden_action in ("STORE_APPROVAL_TOKEN", "FINALIZE_AUTOMATIC_APPROVAL"):
        if forbidden_action in workflow_text:
            raise SalesPlanGuardError(f"Canonical workflow contains stale action {forbidden_action}")
    task_states = [
        state for state in workflow.get("States", {}).values() if state.get("Type") == "Task"
    ]
    if not task_states or not all(state.get("Retry") for state in task_states):
        raise SalesPlanGuardError("Every canonical Lambda task must have a bounded retry")

    return {
        "mode": "static",
        "terraform_files": len(files),
        "resource_counts": dict(sorted(resources.items())),
        "routes": sorted(REQUIRED_ROUTES),
        "workflow_sha256": __import__("hashlib").sha256(
            workflow_path.read_bytes()
        ).hexdigest(),
    }


def _flatten_resources(module: dict[str, Any]) -> Iterable[dict[str, Any]]:
    yield from module.get("resources", [])
    for child in module.get("child_modules", []):
        yield from _flatten_resources(child)


def _variable(plan: dict[str, Any], name: str) -> Any:
    try:
        return plan["variables"][name]["value"]
    except (KeyError, TypeError) as exc:
        raise SalesPlanGuardError(f"Plan is missing required variable {name}") from exc


def validate_plan(
    plan: dict[str, Any], *, expected_source_revision: str | None = None
) -> dict[str, Any]:
    """Validate a private ``terraform show -json`` document."""

    if _variable(plan, "deployment_profile") != "sales_demo":
        raise SalesPlanGuardError("Plan deployment_profile is not sales_demo")
    account_id = str(_variable(plan, "expected_aws_account_id"))
    if re.fullmatch(r"[0-9]{12}", account_id) is None:
        raise SalesPlanGuardError("Plan expected_aws_account_id is invalid")
    region = str(_variable(plan, "aws_region"))
    if re.fullmatch(r"[a-z]{2}(?:-[a-z0-9]+)+-[0-9]+", region) is None:
        raise SalesPlanGuardError("Plan aws_region is invalid")
    if _variable(plan, "guardrail_budget_limit_usd") != 20:
        raise SalesPlanGuardError("Plan is not bound to the approved USD 20 Budget alert")
    deployment_role_arn = str(_variable(plan, "deployment_role_arn"))
    expected_deployment_role_arn = (
        f"arn:aws:iam::{account_id}:role/APPROVALS-TerraformDeploymentRole"
    )
    if deployment_role_arn != expected_deployment_role_arn:
        raise SalesPlanGuardError("Plan does not assume the exact deployment role")
    source_revision = str(_variable(plan, "source_revision"))
    if re.fullmatch(r"[0-9a-f]{40}", source_revision) is None:
        raise SalesPlanGuardError("Plan source_revision is not a full Git SHA")
    if expected_source_revision is not None and source_revision != expected_source_revision:
        raise SalesPlanGuardError("Plan source_revision does not match the clean Git HEAD")
    frontend_release = str(_variable(plan, "frontend_release_sha256"))
    if re.fullmatch(r"[0-9a-f]{64}", frontend_release) is None:
        raise SalesPlanGuardError("Plan frontend_release_sha256 is invalid")
    boundary_arn = str(_variable(plan, "application_role_permissions_boundary_arn"))
    expected_boundary_arn = (
        f"arn:aws:iam::{account_id}:policy/approvals-sales-demo-application-boundary"
    )
    if boundary_arn != expected_boundary_arn:
        raise SalesPlanGuardError("Plan does not use the exact sales_demo guardrail boundary")
    boundary_sha256 = str(
        _variable(plan, "application_role_permissions_boundary_sha256")
    )
    if re.fullmatch(r"[0-9a-f]{64}", boundary_sha256) is None:
        raise SalesPlanGuardError("Plan permissions-boundary hash is invalid")
    role_contract_sha256 = str(_variable(plan, "deployment_role_contract_sha256"))
    if re.fullmatch(r"[0-9a-f]{64}", role_contract_sha256) is None:
        raise SalesPlanGuardError("Plan deployment-role contract hash is invalid")
    budget_name = str(_variable(plan, "guardrail_budget_name"))
    if not budget_name.strip():
        raise SalesPlanGuardError("Plan guardrail Budget name is missing")

    try:
        resources = [
            resource
            for resource in _flatten_resources(plan["planned_values"]["root_module"])
            if resource.get("mode", "managed") == "managed"
        ]
    except (KeyError, TypeError) as exc:
        raise SalesPlanGuardError("Plan has no planned root module") from exc

    counts = Counter(resource.get("type") for resource in resources)
    unexpected_types = sorted(set(counts) - set(REQUIRED_PLAN_COUNTS))
    if unexpected_types:
        raise SalesPlanGuardError(
            "Plan contains unexpected managed resource types: "
            + ", ".join(unexpected_types)
        )
    for resource_type, expected in REQUIRED_PLAN_COUNTS.items():
        actual = counts[resource_type]
        if actual != expected:
            raise SalesPlanGuardError(
                f"Plan expected {expected} {resource_type}; found {actual}"
            )
    by_type: dict[str, list[dict[str, Any]]] = {}
    for resource in resources:
        by_type.setdefault(resource["type"], []).append(resource)

    for role in by_type["aws_iam_role"]:
        if role.get("values", {}).get("permissions_boundary") != boundary_arn:
            raise SalesPlanGuardError("A runtime role escaped the guardrails-owned boundary")

    for function in by_type["aws_lambda_function"]:
        values = function.get("values", {})
        if (
            values.get("runtime") != "python3.12"
            or values.get("memory_size") != 256
            or values.get("timeout") != 10
        ):
            raise SalesPlanGuardError("Both Lambdas must remain Python 3.12 / 256 MB / 10 s")
        reserved = values.get("reserved_concurrent_executions")
        if reserved not in (None, -1):
            raise SalesPlanGuardError("Reserved concurrency is forbidden")

    lambdas_by_address = {
        function["address"]: function.get("values", {})
        for function in by_type["aws_lambda_function"]
    }
    expected_lambda_contracts = {
        "aws_lambda_function.api": {
            "handler": "sales_demo.backend.lambda_api.lambda_handler",
            "environment": {
                "SALES_PILOT_ID",
                "SALES_TABLE_NAME",
                "SALES_STATE_MACHINE_ARN",
                "SALES_WORKER_FUNCTION_NAME",
                "SALES_DAILY_LIMIT",
            },
        },
        "aws_lambda_function.worker": {
            "handler": "sales_demo.backend.lambda_worker.lambda_handler",
            "environment": {
                "SALES_PILOT_ID",
                "SALES_TABLE_NAME",
                "SALES_KMS_KEY_ID",
                "SALES_USER_POOL_ID",
                "SALES_SYNTHETIC_USERNAMES",
            },
        },
    }
    for address, expected in expected_lambda_contracts.items():
        values = lambdas_by_address.get(address)
        if values is None or values.get("handler") != expected["handler"]:
            raise SalesPlanGuardError(f"{address} handler does not match the backend")
        environment = (values.get("environment") or [{}])[0].get("variables") or {}
        if not expected["environment"].issubset(environment):
            raise SalesPlanGuardError(f"{address} environment contract is incomplete")
        if any(
            marker in key.lower()
            for key in environment
            for marker in ("password", "secret", "task_token")
        ):
            raise SalesPlanGuardError(f"{address} environment contains a secret-like key")
    if set(
        lambdas_by_address["aws_lambda_function.worker"]["environment"][0]["variables"][
            "SALES_SYNTHETIC_USERNAMES"
        ].split(",")
    ) != {"customer-a", "manager-a", "customer-b", "manager-b"}:
        raise SalesPlanGuardError("Worker expiry user set is not exactly the four synthetic users")

    pilot_items = by_type.get("aws_dynamodb_table_item", [])
    if len(pilot_items) != 1:
        raise SalesPlanGuardError("Plan must contain exactly one PREPARED pilot seed")
    try:
        pilot_seed = json.loads(pilot_items[0]["values"]["item"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise SalesPlanGuardError("Planned pilot seed is unavailable") from exc
    if pilot_seed.get("status") != {"S": "PREPARED"} or pilot_seed.get(
        "expires_at_epoch"
    ) != {"N": "0"}:
        raise SalesPlanGuardError("Terraform must leave the pilot PREPARED with expiry zero")

    table = by_type["aws_dynamodb_table"][0].get("values", {})
    if (
        table.get("billing_mode") != "PROVISIONED"
        or table.get("read_capacity") != 5
        or table.get("write_capacity") != 5
    ):
        raise SalesPlanGuardError("DynamoDB plan is not fixed at provisioned 5/5")

    state_machine = by_type["aws_sfn_state_machine"][0].get("values", {})
    if state_machine.get("type") != "STANDARD":
        raise SalesPlanGuardError("Step Functions plan is not Standard")
    encryption = (state_machine.get("encryption_configuration") or [{}])[0]
    if encryption.get("type") != "CUSTOMER_MANAGED_KMS_KEY":
        raise SalesPlanGuardError("Step Functions execution history is not CMK encrypted")
    try:
        definition = json.loads(state_machine["definition"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise SalesPlanGuardError("Planned Standard workflow definition is unavailable") from exc
    if definition.get("TimeoutSeconds") != 3600:
        raise SalesPlanGuardError("Planned Standard workflow timeout is not 3,600 seconds")
    callback_states = [
        state
        for state in definition.get("States", {}).values()
        if state.get("Resource") == "arn:aws:states:::lambda:invoke.waitForTaskToken"
    ]
    if len(callback_states) != 1 or callback_states[0].get("TimeoutSeconds") != 3540:
        raise SalesPlanGuardError("Planned callback does not reserve terminal handling time")

    pool = by_type["aws_cognito_user_pool"][0].get("values", {})
    domain = by_type["aws_cognito_user_pool_domain"][0].get("values", {})
    ui = by_type["aws_cognito_user_pool_ui_customization"][0].get("values", {})
    if pool.get("user_pool_tier") != "LITE":
        raise SalesPlanGuardError("Cognito user pool is not pinned to Lite")
    if domain.get("managed_login_version") != 1:
        raise SalesPlanGuardError("Cognito domain is not pinned to classic Hosted UI v1")
    if not isinstance(ui.get("css"), str) or not ui.get("css", "").strip():
        raise SalesPlanGuardError("Cognito classic Hosted UI customization is absent")

    client = by_type["aws_cognito_user_pool_client"][0].get("values", {})
    if client.get("generate_secret") is not False or set(
        client.get("allowed_oauth_flows") or []
    ) != {"code"}:
        raise SalesPlanGuardError("Cognito plan is not a public authorization-code client")
    units = (client.get("token_validity_units") or [{}])[0]
    if (
        client.get("access_token_validity") != 15
        or units.get("access_token") != "minutes"
        or client.get("refresh_token_validity") != 8
        or units.get("refresh_token") != "days"
    ):
        raise SalesPlanGuardError("Cognito token lifetimes do not match 15m/8d")
    rotation = (client.get("refresh_token_rotation") or [{}])[0]
    if (
        rotation.get("feature") != "ENABLED"
        or rotation.get("retry_grace_period_seconds") != 10
        or set(client.get("explicit_auth_flows") or []) != {"ALLOW_USER_SRP_AUTH"}
    ):
        raise SalesPlanGuardError("Cognito refresh-token rotation contract has drifted")

    stage = by_type["aws_apigatewayv2_stage"][0].get("values", {})
    defaults = (stage.get("default_route_settings") or [{}])[0]
    if defaults.get("throttling_rate_limit") != 5 or defaults.get(
        "throttling_burst_limit"
    ) != 10:
        raise SalesPlanGuardError("HTTP API default throttling target is not 5/10")
    route_settings = stage.get("route_settings") or []
    write_routes = {item.get("route_key") for item in route_settings}
    if write_routes != {"POST /applications", "POST /approvals/{id}/decision"}:
        raise SalesPlanGuardError("HTTP API write-route throttling is incomplete")
    if not all(
        item.get("throttling_rate_limit") == 1
        and item.get("throttling_burst_limit") == 2
        for item in route_settings
    ):
        raise SalesPlanGuardError("HTTP API write-route targets are not 1/2")

    routes = by_type.get("aws_apigatewayv2_route", [])
    route_contract: dict[str, dict[str, Any]] = {}
    for route in routes:
        values = route.get("values", {})
        route_key = values.get("route_key")
        if not isinstance(route_key, str) or route_key in route_contract:
            raise SalesPlanGuardError("API route keys are missing or duplicated")
        route_contract[route_key] = values
    if set(route_contract) != REQUIRED_ROUTES:
        raise SalesPlanGuardError("Planned API route set does not match the exact contract")
    for route_key, values in route_contract.items():
        authorization_type = values.get("authorization_type")
        scopes = set(values.get("authorization_scopes") or [])
        if route_key == "GET /health":
            if authorization_type != "NONE" or scopes:
                raise SalesPlanGuardError("GET /health must be the only anonymous route")
        elif authorization_type != "JWT" or scopes != {"openid"}:
            raise SalesPlanGuardError(
                "Every business route must require the exact openid JWT scope"
            )

    schedule = by_type["aws_scheduler_schedule"][0].get("values", {})
    if schedule.get("state") != "DISABLED" or schedule.get(
        "schedule_expression"
    ) != "at(2099-01-01T00:00:00)":
        raise SalesPlanGuardError("Terraform must plan only a disabled expiry placeholder")

    policies = by_type.get("aws_iam_policy", [])
    if len(policies) != 4:
        raise SalesPlanGuardError(f"Expected four managed policies; found {len(policies)}")
    policy_lengths: dict[str, int] = {}
    for policy in policies:
        value = policy.get("values", {}).get("policy")
        if not isinstance(value, str):
            raise SalesPlanGuardError("A managed IAM policy is unknown in the plan")
        length = len(value)
        if length >= IAM_MANAGED_POLICY_CHARACTER_LIMIT:
            raise SalesPlanGuardError("A managed IAM policy reaches the 6,144-character limit")
        policy_lengths[policy.get("address", "unknown")] = length

    return {
        "mode": "plan",
        "account_id_hash": __import__("hashlib").sha256(account_id.encode()).hexdigest(),
        "aws_region": region,
        "deployment_profile": "sales_demo",
        "deployment_role": "APPROVALS-TerraformDeploymentRole",
        "deployment_role_contract_sha256": role_contract_sha256,
        "permissions_boundary_sha256": boundary_sha256,
        "budget_name_sha256": hashlib.sha256(budget_name.encode()).hexdigest(),
        "source_revision": source_revision,
        "frontend_release_sha256": frontend_release,
        "resource_counts": dict(sorted(counts.items())),
        "iam_policy_character_counts": policy_lengths,
    }


def _clean_git_head(repo_root: Path) -> str:
    try:
        head = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain=v1", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise SalesPlanGuardError("Cannot verify the Git source revision") from exc
    if re.fullmatch(r"[0-9a-f]{40}", head) is None:
        raise SalesPlanGuardError("Git HEAD is not a full commit SHA")
    if dirty:
        raise SalesPlanGuardError("Git tree must be clean before validating a private plan")
    return head


def _lambda_package_bytes(path_value: Any, label: str) -> bytes:
    path = Path(str(path_value))
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise SalesPlanGuardError(f"{label} Lambda package path is not a safe regular file")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise SalesPlanGuardError(f"{label} Lambda package is unreadable") from exc
    if not payload:
        raise SalesPlanGuardError(f"{label} Lambda package is empty")
    return payload


def _frontend_release_hash(build_dir: Path) -> str:
    if build_dir.is_symlink() or not build_dir.is_dir():
        raise SalesPlanGuardError("Frontend build directory is missing or unsafe")
    files: list[tuple[str, bytes]] = []
    total = 0
    for path in sorted(build_dir.rglob("*")):
        relative = path.relative_to(build_dir)
        if path.is_symlink():
            raise SalesPlanGuardError("Frontend build contains a symlink")
        if path.is_dir():
            continue
        key = relative.as_posix()
        if any(part.startswith(".") for part in relative.parts):
            raise SalesPlanGuardError("Frontend build contains a hidden path")
        if key == "runtime-config.json" or key.endswith(".map"):
            raise SalesPlanGuardError("Frontend build contains a forbidden generated file")
        payload = path.read_bytes()
        total += len(payload)
        if total > MAX_FRONTEND_BUILD_BYTES:
            raise SalesPlanGuardError("Frontend build exceeds 10 MiB")
        files.append((key, payload))
    if not any(key == "index.html" for key, _ in files):
        raise SalesPlanGuardError("Frontend build does not contain index.html")
    digest = hashlib.sha256()
    for key, payload in files:
        digest.update(key.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).digest())
        digest.update(b"\0")
        digest.update(str(len(payload)).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def validate_local_artifacts(
    plan: dict[str, Any], *, frontend_build_dir: Path
) -> dict[str, Any]:
    api = _lambda_package_bytes(_variable(plan, "api_lambda_package_path"), "API")
    worker = _lambda_package_bytes(
        _variable(plan, "worker_lambda_package_path"), "worker"
    )
    if api != worker:
        raise SalesPlanGuardError("Reviewed API and worker Lambda packages are not identical")
    lambda_hash = base64.b64encode(hashlib.sha256(api).digest()).decode("ascii")
    if lambda_hash != str(_variable(plan, "api_lambda_source_code_sha256")):
        raise SalesPlanGuardError("API Lambda package bytes do not match the private plan")
    if lambda_hash != str(_variable(plan, "worker_lambda_source_code_sha256")):
        raise SalesPlanGuardError("Worker Lambda package bytes do not match the private plan")
    if frontend_build_dir.is_symlink():
        raise SalesPlanGuardError("Frontend build directory cannot be a symlink")
    frontend_hash = _frontend_release_hash(frontend_build_dir.resolve())
    if frontend_hash != str(_variable(plan, "frontend_release_sha256")):
        raise SalesPlanGuardError("Frontend dist bytes do not match the private plan")
    return {
        "lambda_source_code_sha256": lambda_hash,
        "frontend_release_sha256": frontend_hash,
    }


def _load_private_plan(path: Path) -> dict[str, Any]:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SalesPlanGuardError("Plan JSON must be mode 0600")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SalesPlanGuardError("Plan JSON is unreadable or invalid") from exc
    if not isinstance(value, dict):
        raise SalesPlanGuardError("Plan JSON root must be an object")
    return value


def _load_private_preflight(path: Path) -> tuple[dict[str, Any], str]:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SalesPlanGuardError("Live preflight JSON must be mode 0600")
    try:
        payload = path.read_bytes()
        value = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SalesPlanGuardError("Live preflight JSON is unreadable or invalid") from exc
    if not isinstance(value, dict):
        raise SalesPlanGuardError("Live preflight JSON root must be an object")
    return value, hashlib.sha256(payload).hexdigest()


def _preflight_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise SalesPlanGuardError(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SalesPlanGuardError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise SalesPlanGuardError(f"{label} lacks a timezone")
    return parsed.astimezone(UTC)


def validate_live_preflight(
    evidence: dict[str, Any],
    plan: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Bind a fresh sanitized live preflight to the private Terraform plan."""

    current = (now or datetime.now(UTC)).astimezone(UTC)
    captured = _preflight_time(evidence.get("captured_at"), "Preflight captured_at")
    expires = _preflight_time(evidence.get("expires_at"), "Preflight expires_at")
    if captured > current + timedelta(minutes=1):
        raise SalesPlanGuardError("Live preflight was captured in the future")
    if expires <= current or expires > captured + timedelta(minutes=15):
        raise SalesPlanGuardError("Live preflight is expired or has an excessive lifetime")

    account_id = str(_variable(plan, "expected_aws_account_id"))
    source_revision = str(_variable(plan, "source_revision"))
    region = str(_variable(plan, "aws_region"))
    role_hash = str(_variable(plan, "deployment_role_contract_sha256"))
    boundary_hash = str(_variable(plan, "application_role_permissions_boundary_sha256"))
    budget_name = str(_variable(plan, "guardrail_budget_name"))
    serialized = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    if account_id in serialized or "arn:" in serialized.lower() or "@" in serialized:
        raise SalesPlanGuardError("Live preflight contains an unsanitized identifier")
    if (
        evidence.get("schema") != LIVE_PREFLIGHT_SCHEMA
        or evidence.get("deployment_profile") != "sales_demo"
        or evidence.get("aws_region") != region
        or evidence.get("source_revision") != source_revision
        or evidence.get("account_id_hash") != hashlib.sha256(account_id.encode()).hexdigest()
    ):
        raise SalesPlanGuardError("Live preflight identity does not match the plan")

    identity = evidence.get("identity") or {}
    source_identity = evidence.get("source_identity") or {}
    human_posture = evidence.get("human_credential_posture") or {}
    account = evidence.get("account") or {}
    budget = evidence.get("budget") or {}
    guardrails = evidence.get("guardrails") or {}
    lambda_contract = evidence.get("lambda") or {}
    prior = evidence.get("prior_emergency") or {}
    absence = evidence.get("live_absence") or {}
    workload = evidence.get("sales_workload") or {}
    commercial = evidence.get("commercial_hard_stops") or {}
    if not all(
        isinstance(item, dict)
        for item in (
            identity,
            source_identity,
            human_posture,
            account,
            budget,
            guardrails,
            lambda_contract,
            prior,
            absence,
            workload,
            commercial,
        )
    ):
        raise SalesPlanGuardError("Live preflight contract is malformed")
    if identity.get("assumed_role") != "APPROVALS-TerraformDeploymentRole":
        raise SalesPlanGuardError("Live preflight did not use the assumed deployment role")
    if (
        source_identity.get("user_name_hash")
        != hashlib.sha256(b"approvals-local-login").hexdigest()
        or human_posture.get("user_name_hash")
        != hashlib.sha256(b"approvals-local-login").hexdigest()
        or human_posture.get("permanent_access_key_count") != 0
        or not isinstance(human_posture.get("mfa_device_count"), int)
        or human_posture["mfa_device_count"] < 1
    ):
        raise SalesPlanGuardError("Live source identity lacks the approved MFA/key posture")
    if (
        account.get("state") != "ACTIVE"
        or account.get("plan_type") != "FREE"
        or account.get("plan_status") != "ACTIVE"
        or account.get("credits_are_billing_statement") is not False
    ):
        raise SalesPlanGuardError("Live preflight account is not FREE/ACTIVE")
    try:
        if float(account.get("remaining_credits_usd")) <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise SalesPlanGuardError("Live preflight has no positive API-reported credits")
    if (
        budget.get("name_hash") != hashlib.sha256(budget_name.encode()).hexdigest()
        or budget.get("limit_usd") != 20
        or budget.get("hard_limit") is not False
        or budget.get("account_wide") is not True
        or budget.get("notifications_exact") is not True
    ):
        raise SalesPlanGuardError("Live preflight Budget differs from the plan")
    if (
        guardrails.get("role") != "APPROVALS-TerraformDeploymentRole"
        or guardrails.get("attached_policy_count") != 7
        or guardrails.get("inline_policy_count") != 0
        or guardrails.get("exact_sales_policy_set") is not True
        or guardrails.get("contract_sha256") != role_hash
        or guardrails.get("boundary_sha256") != boundary_hash
    ):
        raise SalesPlanGuardError("Live sales IAM contract differs from the plan")
    policy_lengths = guardrails.get("managed_policy_character_counts")
    if (
        not isinstance(policy_lengths, dict)
        or len(policy_lengths) != 7
        or any(not isinstance(length, int) or length >= 6144 for length in policy_lengths.values())
        or not isinstance(guardrails.get("boundary_character_count"), int)
        or guardrails["boundary_character_count"] >= 6144
    ):
        raise SalesPlanGuardError("Live sales managed-policy sizes are not bounded")
    if (
        lambda_contract.get("concurrent_executions") != 10
        or not isinstance(lambda_contract.get("unreserved_concurrent_executions"), int)
        or lambda_contract["unreserved_concurrent_executions"] < 2
        or lambda_contract.get("reserved_concurrency_used_by_sales") != 0
    ):
        raise SalesPlanGuardError("Live Lambda quota differs from the sales contract")
    if prior != {
        "deployment_profile": "emergency_only",
        "telemetry_resources": 0,
        "kinesis_resources": 0,
        "teardown_complete": True,
        "residual_workload_resources": 0,
        "evidence_sha256": prior.get("evidence_sha256"),
    } or re.fullmatch(r"[0-9a-f]{64}", str(prior.get("evidence_sha256") or "")) is None:
        raise SalesPlanGuardError("Prior emergency_only evidence is not exact")
    if absence != {
        "tagged_prior_approvals_resources": 0,
        "kinesis_streams": 0,
        "firehose_delivery_streams": 0,
        "prior_approvals_topic_rules": 0,
        "telemetry_topic_rules": 0,
    }:
        raise SalesPlanGuardError("Live prior-workload/Kinesis inventory is not zero")
    if workload != {
        "status": "WORKLOAD_ZERO",
        "finding_count": 0,
        "collector_error_count": 0,
        "persistent_guardrail_count": 3,
    }:
        raise SalesPlanGuardError("Live sales workload inventory is not zero")
    if commercial != {
        "organizations": {"organization_in_use": False},
        "marketplace": {"active_purchase_agreement_count": 0},
        "support": {
            "business_enterprise_api_entitlement": False,
            "signal": "SubscriptionRequiredException",
            "developer_support_excluded_by_signal": False,
        },
    }:
        raise SalesPlanGuardError("Live Organizations/Marketplace/Support hard stops differ")
    return {
        "schema": LIVE_PREFLIGHT_SCHEMA,
        "captured_at": evidence["captured_at"],
        "expires_at": evidence["expires_at"],
        "account_plan": "FREE/ACTIVE",
        "budget_limit_usd": 20,
        "lambda_concurrency_quota": 10,
        "workload_status": "WORKLOAD_ZERO",
        "kinesis_streams": 0,
        "telemetry_topic_rules": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--terraform-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
    )
    parser.add_argument("--plan-json", type=Path)
    parser.add_argument("--frontend-build-dir", type=Path)
    parser.add_argument("--preflight-json", type=Path)
    parser.add_argument("--static-only", action="store_true")
    args = parser.parse_args(argv)

    try:
        static_result = validate_static(args.terraform_dir.resolve())
        if args.static_only:
            if (
                args.plan_json is not None
                or args.frontend_build_dir is not None
                or args.preflight_json is not None
            ):
                raise SalesPlanGuardError(
                    "Static mode does not accept plan or artifact inputs"
                )
            result = static_result
        else:
            if args.plan_json is None:
                raise SalesPlanGuardError(
                    "Fail closed: --plan-json is required unless --static-only is explicit"
                )
            if args.frontend_build_dir is None:
                raise SalesPlanGuardError(
                    "Fail closed: --frontend-build-dir is required with a private plan"
                )
            if args.preflight_json is None:
                raise SalesPlanGuardError(
                    "Fail closed: --preflight-json is required with a private plan"
                )
            private_plan = _load_private_plan(args.plan_json.resolve())
            live_preflight, preflight_sha256 = _load_private_preflight(
                args.preflight_json.resolve()
            )
            repo_root = args.terraform_dir.resolve().parents[1]
            result = validate_plan(
                private_plan,
                expected_source_revision=_clean_git_head(repo_root),
            )
            result["local_artifacts"] = validate_local_artifacts(
                private_plan,
                frontend_build_dir=args.frontend_build_dir,
            )
            result["live_preflight"] = validate_live_preflight(
                live_preflight,
                private_plan,
            )
            result["live_preflight"]["sha256"] = preflight_sha256
            result["static_contract"] = static_result
    except (OSError, SalesPlanGuardError) as exc:
        print(f"sales plan guard: FAIL: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
