from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "inventory_zero.py"
SPEC = importlib.util.spec_from_file_location("inventory_zero", MODULE_PATH)
assert SPEC and SPEC.loader
inventory_zero = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = inventory_zero
SPEC.loader.exec_module(inventory_zero)


class FakePaginator:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **_kwargs):
        return iter(self.pages)


class FakeClient:
    def __init__(self, pages=None, calls=None):
        self.pages = pages or {}
        self.calls = calls or {}

    def get_paginator(self, operation):
        value = self.pages[operation]
        if isinstance(value, Exception):
            raise value
        return FakePaginator(value)

    def __getattr__(self, name):
        value = self.calls[name]
        return value if callable(value) else lambda **_kwargs: value


def empty_clients():
    return {
        "apigatewayv2": FakeClient({"get_apis": [{"Items": []}]}),
        "cognito-idp": FakeClient({"list_user_pools": [{"UserPools": []}]}),
        "lambda": FakeClient({"list_functions": [{"Functions": []}]}),
        "dynamodb": FakeClient({"list_tables": [{"TableNames": []}]}),
        "stepfunctions": FakeClient({"list_state_machines": [{"stateMachines": []}]}),
        "scheduler": FakeClient({"list_schedules": [{"Schedules": []}]}),
        "logs": FakeClient({"describe_log_groups": [{"logGroups": []}]}),
        "cloudfront": FakeClient(
            {
                "list_distributions": [{"DistributionList": {"Items": []}}],
                "list_origin_access_controls": [{"OriginAccessControlList": {"Items": []}}],
                "list_cache_policies": [{"CachePolicyList": {"Items": []}}],
                "list_response_headers_policies": [
                    {"ResponseHeadersPolicyList": {"Items": []}}
                ],
            }
        ),
        "iam": FakeClient(
            {
                "list_roles": [{"Roles": []}],
                "list_policies": [
                    {
                        "Policies": [
                            {
                                "PolicyName": "approvals-sales-demo-application-boundary",
                                "Arn": "arn:synthetic:boundary",
                            },
                            {
                                "PolicyName": "approvals-central-demo-terraform-sales-demo",
                                "Arn": "arn:synthetic:deployment",
                            },
                            {
                                "PolicyName": "approvals-central-demo-terraform-sales-demo-edge",
                                "Arn": "arn:synthetic:edge",
                            },
                        ]
                    }
                ],
            }
        ),
        "s3": FakeClient(calls={"list_buckets": {"Buckets": []}}),
    }


class InventoryZeroTests(unittest.TestCase):
    def test_empty_paginated_inventory_is_zero(self) -> None:
        report = inventory_zero.collect_inventory(empty_clients())
        self.assertEqual(report["status"], "WORKLOAD_ZERO")
        self.assertEqual(report["findings"], {})
        self.assertEqual(report["errors"], [])

    def test_late_page_resource_is_non_zero_and_identifier_is_hashed(self) -> None:
        clients = empty_clients()
        clients["lambda"] = FakeClient(
            {
                "list_functions": [
                    {"Functions": []},
                    {"Functions": [{"FunctionName": "approvals-sales-demo-api"}]},
                ]
            }
        )
        report = inventory_zero.collect_inventory(clients)
        self.assertEqual(report["status"], "WORKLOAD_NON_ZERO")
        finding = report["findings"]["lambda_function"]
        self.assertEqual(finding["count"], 1)
        self.assertNotIn("approvals-sales-demo-api", str(finding))

    def test_any_collector_error_is_inconclusive_not_zero(self) -> None:
        clients = empty_clients()
        clients["dynamodb"] = FakeClient({"list_tables": RuntimeError("denied")})
        report = inventory_zero.collect_inventory(clients)
        self.assertEqual(report["status"], "INCONCLUSIVE")
        self.assertEqual(
            report["errors"],
            [{"collector": "dynamodb", "error_type": "RuntimeError"}],
        )

    def test_only_exact_assumed_deployment_role_is_accepted(self) -> None:
        account = "0" * 12
        identity = {
            "Account": account,
            "Arn": f"arn:aws:sts::{account}:assumed-role/APPROVALS-TerraformDeploymentRole/inventory",
        }
        self.assertEqual(len(inventory_zero.verify_caller(identity, account)), 16)
        identity["Arn"] = f"arn:aws:sts::{account}:assumed-role/Administrator/inventory"
        with self.assertRaises(inventory_zero.InventoryError):
            inventory_zero.verify_caller(identity, account)

    def test_guardrail_phase_proves_exact_removal_separately(self) -> None:
        iam = FakeClient(
            {
                "list_policies": [{"Policies": []}],
                "list_attached_role_policies": [{"AttachedPolicies": []}],
            }
        )
        report = inventory_zero.collect_guardrail_inventory(iam)
        self.assertEqual(report["status"], "GUARDRAILS_ZERO")

        iam = FakeClient(
            {
                "list_policies": [
                    {
                        "Policies": [
                            {
                                "PolicyName": "approvals-sales-demo-application-boundary",
                                "Arn": "arn:synthetic:boundary",
                            }
                        ]
                    }
                ],
                "list_attached_role_policies": [{"AttachedPolicies": []}],
            }
        )
        report = inventory_zero.collect_guardrail_inventory(iam)
        self.assertEqual(report["status"], "GUARDRAILS_NON_ZERO")

    def test_workload_zero_requires_expected_guardrails_to_be_present(self) -> None:
        clients = empty_clients()
        clients["iam"] = FakeClient(
            {"list_roles": [{"Roles": []}], "list_policies": [{"Policies": []}]}
        )
        report = inventory_zero.collect_inventory(clients)
        self.assertEqual(report["status"], "INCONCLUSIVE")
        self.assertEqual(
            report["errors"][0]["error_type"], "PersistentGuardrailSetDrifted"
        )


if __name__ == "__main__":
    unittest.main()
