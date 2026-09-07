from __future__ import annotations

import importlib.util
import base64
import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "plan_guard.py"
ACCOUNT_ID = "0" * 12
SPEC = importlib.util.spec_from_file_location("sales_plan_guard", MODULE_PATH)
assert SPEC and SPEC.loader
plan_guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plan_guard)

PUBLISH_PATH = MODULE_PATH.parent / "publish_frontend.py"
PUBLISH_SPEC = importlib.util.spec_from_file_location(
    "plan_guard_publish_frontend", PUBLISH_PATH
)
assert PUBLISH_SPEC and PUBLISH_SPEC.loader
publish_frontend = importlib.util.module_from_spec(PUBLISH_SPEC)
sys.modules[PUBLISH_SPEC.name] = publish_frontend
PUBLISH_SPEC.loader.exec_module(publish_frontend)


def _resource(address: str, resource_type: str, **values: object) -> dict:
    return {"address": address, "type": resource_type, "values": values}


def representative_plan() -> dict:
    boundary_arn = (
        f"arn:aws:iam::{ACCOUNT_ID}:policy/approvals-sales-demo-application-boundary"
    )
    resources = [
        _resource("aws_apigatewayv2_api.sales", "aws_apigatewayv2_api"),
        _resource("aws_apigatewayv2_authorizer.cognito", "aws_apigatewayv2_authorizer"),
        *[
            _resource(
                f"aws_apigatewayv2_route.routes[{index}]",
                "aws_apigatewayv2_route",
                route_key=route,
                authorization_type="NONE" if route == "GET /health" else "JWT",
                authorization_scopes=[] if route == "GET /health" else ["openid"],
            )
            for index, route in enumerate(sorted(plan_guard.REQUIRED_ROUTES))
        ],
        _resource(
            "aws_apigatewayv2_stage.default",
            "aws_apigatewayv2_stage",
            default_route_settings=[
                {"throttling_rate_limit": 5, "throttling_burst_limit": 10}
            ],
            route_settings=[
                {
                    "route_key": "POST /applications",
                    "throttling_rate_limit": 1,
                    "throttling_burst_limit": 2,
                },
                {
                    "route_key": "POST /approvals/{id}/decision",
                    "throttling_rate_limit": 1,
                    "throttling_burst_limit": 2,
                },
            ],
        ),
        _resource("aws_cloudfront_distribution.web", "aws_cloudfront_distribution"),
        _resource(
            "aws_cloudfront_origin_access_control.web",
            "aws_cloudfront_origin_access_control",
        ),
        _resource(
            "aws_cognito_user_pool.pilot",
            "aws_cognito_user_pool",
            user_pool_tier="LITE",
        ),
        _resource(
            "aws_cognito_user_pool_domain.pilot",
            "aws_cognito_user_pool_domain",
            managed_login_version=1,
        ),
        _resource(
            "aws_cognito_user_pool_ui_customization.web",
            "aws_cognito_user_pool_ui_customization",
            css=".banner-customizable { background-color: #0b2a3c; }",
        ),
        _resource(
            "aws_cognito_user_pool_client.web",
            "aws_cognito_user_pool_client",
            generate_secret=False,
            allowed_oauth_flows=["code"],
            access_token_validity=15,
            refresh_token_validity=8,
            token_validity_units=[
                {"access_token": "minutes", "refresh_token": "days"}
            ],
            refresh_token_rotation=[
                {"feature": "ENABLED", "retry_grace_period_seconds": 10}
            ],
            explicit_auth_flows=["ALLOW_USER_SRP_AUTH"],
        ),
        _resource(
            "aws_dynamodb_table.sales",
            "aws_dynamodb_table",
            billing_mode="PROVISIONED",
            read_capacity=5,
            write_capacity=5,
        ),
        _resource(
            "aws_dynamodb_table_item.pilot_config",
            "aws_dynamodb_table_item",
            item=json.dumps(
                {"status": {"S": "PREPARED"}, "expires_at_epoch": {"N": "0"}}
            ),
        ),
        _resource(
            "aws_lambda_function.api",
            "aws_lambda_function",
            runtime="python3.12",
            memory_size=256,
            timeout=10,
            reserved_concurrent_executions=-1,
            handler="sales_demo.backend.lambda_api.lambda_handler",
            environment=[{
                "variables": {
                    "SALES_PILOT_ID": "demo-sales-plus",
                    "SALES_TABLE_NAME": "table",
                    "SALES_STATE_MACHINE_ARN": "state-machine",
                    "SALES_WORKER_FUNCTION_NAME": "worker",
                    "SALES_DAILY_LIMIT": "10",
                }
            }],
        ),
        _resource(
            "aws_lambda_function.worker",
            "aws_lambda_function",
            runtime="python3.12",
            memory_size=256,
            timeout=10,
            reserved_concurrent_executions=-1,
            handler="sales_demo.backend.lambda_worker.lambda_handler",
            environment=[{
                "variables": {
                    "SALES_PILOT_ID": "demo-sales-plus",
                    "SALES_TABLE_NAME": "table",
                    "SALES_KMS_KEY_ID": "key",
                    "SALES_USER_POOL_ID": "pool",
                    "SALES_SYNTHETIC_USERNAMES": "customer-a,manager-a,customer-b,manager-b",
                }
            }],
        ),
        _resource("aws_s3_bucket.web", "aws_s3_bucket"),
        _resource(
            "aws_scheduler_schedule.expiry",
            "aws_scheduler_schedule",
            state="DISABLED",
            schedule_expression="at(2099-01-01T00:00:00)",
        ),
        _resource(
            "aws_sfn_state_machine.application",
            "aws_sfn_state_machine",
            type="STANDARD",
            encryption_configuration=[{"type": "CUSTOMER_MANAGED_KMS_KEY"}],
            definition=json.dumps({
                "TimeoutSeconds": 3600,
                "States": {
                    "Wait": {
                        "Type": "Task",
                        "Resource": "arn:aws:states:::lambda:invoke.waitForTaskToken",
                        "TimeoutSeconds": 3540,
                    }
                },
            }),
        ),
    ]
    resources.extend(
        _resource(
            f"aws_iam_role.role_{index}",
            "aws_iam_role",
            permissions_boundary=boundary_arn,
        )
        for index in range(4)
    )
    resources.extend(
        _resource(f"aws_iam_policy.policy_{index}", "aws_iam_policy", policy="{}")
        for index in range(4)
    )
    for resource_type, expected in plan_guard.REQUIRED_PLAN_COUNTS.items():
        actual = sum(1 for resource in resources if resource["type"] == resource_type)
        resources.extend(
            _resource(
                f"{resource_type}.placeholder_{index}",
                resource_type,
            )
            for index in range(actual, expected)
        )
    return {
        "variables": {
            "deployment_profile": {"value": "sales_demo"},
            "expected_aws_account_id": {"value": ACCOUNT_ID},
            "aws_region": {"value": "us-east-1"},
            "guardrail_budget_limit_usd": {"value": 20},
            "guardrail_budget_name": {"value": "approvals-central-demo-account-guardrail"},
            "deployment_role_arn": {
                "value": f"arn:aws:iam::{ACCOUNT_ID}:role/APPROVALS-TerraformDeploymentRole"
            },
            "deployment_role_contract_sha256": {"value": "d" * 64},
            "source_revision": {"value": "b" * 40},
            "frontend_release_sha256": {"value": "c" * 64},
            "application_role_permissions_boundary_arn": {"value": boundary_arn},
            "application_role_permissions_boundary_sha256": {"value": "a" * 64},
        },
        "planned_values": {"root_module": {"resources": resources}},
    }


def representative_preflight(now: datetime) -> dict:
    captured = now.replace(microsecond=0)
    return {
        "schema": "sales-live-preflight/1.0",
        "captured_at": captured.isoformat().replace("+00:00", "Z"),
        "expires_at": (captured + timedelta(minutes=15)).isoformat().replace("+00:00", "Z"),
        "source_revision": "b" * 40,
        "aws_region": "us-east-1",
        "deployment_profile": "sales_demo",
        "account_id_hash": hashlib.sha256(ACCOUNT_ID.encode()).hexdigest(),
        "identity": {
            "assumed_role": "APPROVALS-TerraformDeploymentRole",
            "session_hash": "e" * 64,
        },
        "source_identity": {
            "user_name_hash": hashlib.sha256(b"approvals-local-login").hexdigest(),
        },
        "human_credential_posture": {
            "user_name_hash": hashlib.sha256(b"approvals-local-login").hexdigest(),
            "permanent_access_key_count": 0,
            "mfa_device_count": 1,
        },
        "account": {
            "state": "ACTIVE",
            "plan_type": "FREE",
            "plan_status": "ACTIVE",
            "remaining_credits_usd": "140",
            "credits_are_billing_statement": False,
            "plan_expiration": "2027-08-26T00:00:00Z",
            "minimum_coverage_hours": 193,
        },
        "budget": {
            "name_hash": hashlib.sha256(
                b"approvals-central-demo-account-guardrail"
            ).hexdigest(),
            "limit_usd": 20,
            "hard_limit": False,
            "account_wide": True,
            "notifications_exact": True,
            "subscriber_email_sha256": "f" * 64,
        },
        "guardrails": {
            "role": "APPROVALS-TerraformDeploymentRole",
            "inline_policy_count": 0,
            "attached_policy_count": 7,
            "exact_sales_policy_set": True,
            "contract_sha256": "d" * 64,
            "boundary_sha256": "a" * 64,
            "boundary_character_count": 5000,
            "managed_policy_character_counts": {
                f"policy-{index}": 5000 for index in range(7)
            },
        },
        "lambda": {
            "concurrent_executions": 10,
            "unreserved_concurrent_executions": 10,
            "reserved_concurrency_used_by_sales": 0,
        },
        "prior_emergency": {
            "deployment_profile": "emergency_only",
            "telemetry_resources": 0,
            "kinesis_resources": 0,
            "teardown_complete": True,
            "residual_workload_resources": 0,
            "evidence_sha256": "1" * 64,
        },
        "live_absence": {
            "tagged_prior_approvals_resources": 0,
            "kinesis_streams": 0,
            "firehose_delivery_streams": 0,
            "prior_approvals_topic_rules": 0,
            "telemetry_topic_rules": 0,
        },
        "commercial_hard_stops": {
            "organizations": {"organization_in_use": False},
            "marketplace": {"active_purchase_agreement_count": 0},
            "support": {
                "business_enterprise_api_entitlement": False,
                "signal": "SubscriptionRequiredException",
                "developer_support_excluded_by_signal": False,
            },
        },
        "sales_workload": {
            "status": "WORKLOAD_ZERO",
            "finding_count": 0,
            "collector_error_count": 0,
            "persistent_guardrail_count": 3,
        },
    }


class SalesPlanGuardTests(unittest.TestCase):
    def test_static_contract_is_green(self) -> None:
        result = plan_guard.validate_static(MODULE_PATH.parent)
        self.assertEqual(result["resource_counts"]["aws_lambda_function"], 2)

    def test_representative_plan_is_green(self) -> None:
        result = plan_guard.validate_plan(representative_plan())
        self.assertEqual(result["deployment_profile"], "sales_demo")

    def test_refresh_rotation_rejects_the_implicit_or_incompatible_flow(self) -> None:
        for flows in ([], ["ALLOW_REFRESH_TOKEN_AUTH"]):
            plan = representative_plan()
            client = next(
                resource
                for resource in plan["planned_values"]["root_module"]["resources"]
                if resource["type"] == "aws_cognito_user_pool_client"
            )
            client["values"]["explicit_auth_flows"] = flows
            with self.assertRaises(plan_guard.SalesPlanGuardError):
                plan_guard.validate_plan(plan)

    def test_paid_or_unbranded_cognito_login_is_rejected(self) -> None:
        plan = representative_plan()
        pool = next(
            resource
            for resource in plan["planned_values"]["root_module"]["resources"]
            if resource["type"] == "aws_cognito_user_pool"
        )
        pool["values"]["user_pool_tier"] = "ESSENTIALS"
        with self.assertRaises(plan_guard.SalesPlanGuardError):
            plan_guard.validate_plan(plan)

        plan = representative_plan()
        ui = next(
            resource
            for resource in plan["planned_values"]["root_module"]["resources"]
            if resource["type"] == "aws_cognito_user_pool_ui_customization"
        )
        ui["values"]["css"] = ""
        with self.assertRaises(plan_guard.SalesPlanGuardError):
            plan_guard.validate_plan(plan)

    def test_oversized_managed_policy_is_rejected(self) -> None:
        plan = representative_plan()
        policies = [
            resource
            for resource in plan["planned_values"]["root_module"]["resources"]
            if resource["type"] == "aws_iam_policy"
        ]
        policies[0]["values"]["policy"] = "x" * 6144
        with self.assertRaises(plan_guard.SalesPlanGuardError):
            plan_guard.validate_plan(plan)

    def test_runtime_role_without_guardrail_boundary_is_rejected(self) -> None:
        plan = representative_plan()
        role = next(
            resource
            for resource in plan["planned_values"]["root_module"]["resources"]
            if resource["type"] == "aws_iam_role"
        )
        role["values"]["permissions_boundary"] = None
        with self.assertRaises(plan_guard.SalesPlanGuardError):
            plan_guard.validate_plan(plan)

    def test_unscoped_jwt_route_is_rejected(self) -> None:
        plan = representative_plan()
        route = next(
            resource
            for resource in plan["planned_values"]["root_module"]["resources"]
            if resource["type"] == "aws_apigatewayv2_route"
            and resource["values"]["authorization_type"] == "JWT"
        )
        route["values"]["authorization_scopes"] = []
        with self.assertRaises(plan_guard.SalesPlanGuardError):
            plan_guard.validate_plan(plan)

    def test_business_route_cannot_become_anonymous(self) -> None:
        plan = representative_plan()
        route = next(
            resource
            for resource in plan["planned_values"]["root_module"]["resources"]
            if resource["type"] == "aws_apigatewayv2_route"
            and resource["values"]["route_key"] == "GET /me"
        )
        route["values"]["authorization_type"] = "NONE"
        route["values"]["authorization_scopes"] = []
        with self.assertRaises(plan_guard.SalesPlanGuardError):
            plan_guard.validate_plan(plan)

    def test_route_key_drift_is_rejected(self) -> None:
        plan = representative_plan()
        route = next(
            resource
            for resource in plan["planned_values"]["root_module"]["resources"]
            if resource["type"] == "aws_apigatewayv2_route"
            and resource["values"]["route_key"] == "GET /me"
        )
        route["values"]["route_key"] = "GET /unexpected"
        with self.assertRaises(plan_guard.SalesPlanGuardError):
            plan_guard.validate_plan(plan)

    def test_plan_source_revision_must_match_clean_head(self) -> None:
        with self.assertRaises(plan_guard.SalesPlanGuardError):
            plan_guard.validate_plan(
                representative_plan(), expected_source_revision="d" * 40
            )

    def test_plan_rejects_a_different_same_account_role(self) -> None:
        plan = representative_plan()
        plan["variables"]["deployment_role_arn"]["value"] = (
            f"arn:aws:iam::{ACCOUNT_ID}:role/Administrator"
        )
        with self.assertRaises(plan_guard.SalesPlanGuardError):
            plan_guard.validate_plan(plan)

    def test_unexpected_managed_resource_type_is_rejected(self) -> None:
        plan = representative_plan()
        plan["planned_values"]["root_module"]["resources"].append(
            _resource("aws_instance.escape", "aws_instance", instance_type="t4g.nano")
        )
        with self.assertRaises(plan_guard.SalesPlanGuardError):
            plan_guard.validate_plan(plan)

    def test_data_resources_do_not_change_managed_inventory(self) -> None:
        plan = representative_plan()
        data = _resource("data.aws_partition.current", "aws_partition")
        data["mode"] = "data"
        plan["planned_values"]["root_module"]["resources"].append(data)
        plan_guard.validate_plan(plan)

    def test_plan_file_must_be_mode_0600(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            path.write_text(json.dumps(representative_plan()), encoding="utf-8")
            os.chmod(path, 0o644)
            with self.assertRaises(plan_guard.SalesPlanGuardError):
                plan_guard._load_private_plan(path)
            os.chmod(path, 0o600)
            self.assertEqual(
                plan_guard._load_private_plan(path)["variables"]["deployment_profile"]["value"],
                "sales_demo",
            )

    def test_live_preflight_is_fresh_and_bound_to_the_plan(self) -> None:
        now = datetime(2026, 8, 26, 15, 0, tzinfo=UTC)
        result = plan_guard.validate_live_preflight(
            representative_preflight(now), representative_plan(), now=now
        )
        self.assertEqual(result["account_plan"], "FREE/ACTIVE")
        self.assertEqual(result["workload_status"], "WORKLOAD_ZERO")

    def test_live_preflight_rejects_expiry_or_nonzero_kinesis(self) -> None:
        now = datetime(2026, 8, 26, 15, 0, tzinfo=UTC)
        expired = representative_preflight(now - timedelta(minutes=16))
        with self.assertRaises(plan_guard.SalesPlanGuardError):
            plan_guard.validate_live_preflight(expired, representative_plan(), now=now)

        nonzero = representative_preflight(now)
        nonzero["live_absence"]["kinesis_streams"] = 1
        with self.assertRaises(plan_guard.SalesPlanGuardError):
            plan_guard.validate_live_preflight(nonzero, representative_plan(), now=now)

    def test_live_preflight_rejects_unsanitized_account_or_arn(self) -> None:
        now = datetime(2026, 8, 26, 15, 0, tzinfo=UTC)
        value = representative_preflight(now)
        value["leak"] = f"arn:aws:iam::{ACCOUNT_ID}:role/example"
        with self.assertRaises(plan_guard.SalesPlanGuardError):
            plan_guard.validate_live_preflight(value, representative_plan(), now=now)

    def test_local_artifacts_are_bound_to_private_plan_hashes(self) -> None:
        plan = representative_plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = root / "sales-api.zip"
            worker = root / "sales-worker.zip"
            payload = b"synthetic-reviewed-lambda-package"
            api.write_bytes(payload)
            worker.write_bytes(payload)
            encoded = base64.b64encode(hashlib.sha256(payload).digest()).decode("ascii")
            plan["variables"]["api_lambda_package_path"] = {"value": str(api)}
            plan["variables"]["worker_lambda_package_path"] = {"value": str(worker)}
            plan["variables"]["api_lambda_source_code_sha256"] = {"value": encoded}
            plan["variables"]["worker_lambda_source_code_sha256"] = {"value": encoded}
            dist = root / "dist"
            dist.mkdir()
            (dist / "index.html").write_text("<main>synthetic</main>", encoding="utf-8")
            frontend_hash = plan_guard._frontend_release_hash(dist)
            self.assertEqual(
                frontend_hash,
                publish_frontend.release_sha256(
                    publish_frontend.collect_build(dist)
                ),
            )
            plan["variables"]["frontend_release_sha256"] = {"value": frontend_hash}

            result = plan_guard.validate_local_artifacts(
                plan, frontend_build_dir=dist
            )
            self.assertEqual(result["frontend_release_sha256"], frontend_hash)

            worker.write_bytes(payload + b"tampered")
            with self.assertRaises(plan_guard.SalesPlanGuardError):
                plan_guard.validate_local_artifacts(plan, frontend_build_dir=dist)


if __name__ == "__main__":
    unittest.main()
