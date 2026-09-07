from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


TERRAFORM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TERRAFORM_DIR))
MODULE_PATH = TERRAFORM_DIR / "preflight.py"
SPEC = importlib.util.spec_from_file_location("sales_live_preflight", MODULE_PATH)
assert SPEC and SPEC.loader
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)
ACCOUNT_ID = "0" * 12


class FakePaginator:
    def __init__(self, pages: list[dict]):
        self.pages = pages

    def paginate(self, **_kwargs: object):
        yield from self.pages


class FakePagedClient:
    def __init__(self, pages: dict[str, list[dict]]):
        self.pages = pages

    def get_paginator(self, operation: str) -> FakePaginator:
        return FakePaginator(self.pages[operation])


class FakeNonPageableClient:
    def __init__(self, operation: str, pages: list[dict]):
        self.operation = operation
        self.pages = list(pages)
        self.calls: list[dict] = []

    def can_paginate(self, operation: str) -> bool:
        self.assert_operation(operation)
        return False

    def assert_operation(self, operation: str) -> None:
        if operation != self.operation:
            raise AssertionError(f"unexpected operation {operation}")

    def _next(self, kwargs: dict) -> dict:
        self.calls.append(kwargs)
        return self.pages.pop(0)

    def list_delivery_streams(self, **kwargs: object) -> dict:
        self.assert_operation("list_delivery_streams")
        return self._next(dict(kwargs))

    def search_agreements(self, **kwargs: object) -> dict:
        self.assert_operation("search_agreements")
        return self._next(dict(kwargs))


class FakeAwsError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeOrganizations:
    def describe_organization(self) -> dict:
        raise FakeAwsError("AWSOrganizationsNotInUseException")


class FakeSupport:
    def describe_services(self, **_kwargs: object) -> dict:
        raise FakeAwsError("SubscriptionRequiredException")


class FakeIam(FakePagedClient):
    def __init__(self, prefix: str):
        self.prefix = prefix
        self.role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{preflight.DEPLOYMENT_ROLE_NAME}"
        self.names = (
            f"{prefix}-terraform-compute-data",
            f"{prefix}-terraform-iam-management",
            f"{prefix}-terraform-integrations",
            f"{prefix}-terraform-kms-use",
            f"{prefix}-terraform-read-and-global",
            f"{prefix}-terraform-sales-demo",
            f"{prefix}-terraform-sales-demo-edge",
        )
        self.documents = {
            name: {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "x:y", "Resource": "*"}]}
            for name in (*self.names, preflight.BOUNDARY_NAME)
        }
        attachments = [
            {
                "PolicyName": name,
                "PolicyArn": f"arn:aws:iam::{ACCOUNT_ID}:policy/{name}",
            }
            for name in self.names
        ]
        super().__init__({
            "list_role_policies": [{"PolicyNames": []}],
            "list_attached_role_policies": [{"AttachedPolicies": attachments}],
        })

    def get_role(self, **_kwargs: object) -> dict:
        return {
            "Role": {
                "Arn": self.role_arn,
                "RoleName": preflight.DEPLOYMENT_ROLE_NAME,
                "MaxSessionDuration": 3600,
                "AssumeRolePolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [{"Effect": "Allow", "Action": "sts:AssumeRole", "Principal": {"AWS": "root"}}],
                },
            }
        }

    def get_policy(self, *, PolicyArn: str) -> dict:
        return {"Policy": {"Arn": PolicyArn, "DefaultVersionId": "v1"}}

    def get_policy_version(self, *, PolicyArn: str, VersionId: str) -> dict:
        name = PolicyArn.rsplit("/", 1)[-1]
        return {
            "PolicyVersion": {
                "VersionId": VersionId,
                "IsDefaultVersion": True,
                "Document": self.documents[name],
            }
        }

    def expected_contract_hash(self) -> str:
        role = self.get_role()["Role"]
        return preflight._canonical_sha256({
            "role": {
                "arn": role["Arn"],
                "name": preflight.DEPLOYMENT_ROLE_NAME,
                "max_session_duration": 3600,
                "assume_role_policy": role["AssumeRolePolicyDocument"],
                "permissions_boundary_arn": None,
            },
            "managed_policies": [
                {
                    "arn": f"arn:aws:iam::{ACCOUNT_ID}:policy/{name}",
                    "document": self.documents[name],
                }
                for name in self.names
            ],
        })


class SalesLivePreflightTests(unittest.TestCase):
    def test_prior_evidence_requires_emergency_only_zero_teardown(self) -> None:
        value = {
            "deployment_profile": "emergency_only",
            "cost_guard": {"kinesis_resources": 0, "telemetry_resources": 0},
            "teardown": {
                "completed": True,
                "terraform_workload_state_empty": True,
                "residual_workload_resources": 0,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            result = preflight.validate_prior_evidence(path)
            self.assertEqual(result["deployment_profile"], "emergency_only")
            value["cost_guard"]["telemetry_resources"] = 1
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(preflight.SalesPreflightError):
                preflight.validate_prior_evidence(path)

    def test_account_must_be_free_active_positive_and_cover_193_hours(self) -> None:
        now = datetime(2026, 8, 26, 15, 0, tzinfo=UTC)
        result = preflight.validate_account(
            {"AccountId": ACCOUNT_ID, "AccountState": "ACTIVE"},
            {
                "accountId": ACCOUNT_ID,
                "accountPlanType": "FREE",
                "accountPlanStatus": "ACTIVE",
                "accountPlanRemainingCredits": {"amount": "140", "unit": "USD"},
                "accountPlanExpirationDate": (now + timedelta(days=30)).isoformat(),
            },
            account_id=ACCOUNT_ID,
            now=now,
        )
        self.assertEqual(result["plan_type"], "FREE")
        with self.assertRaises(preflight.SalesPreflightError):
            preflight.validate_account(
                {"AccountId": ACCOUNT_ID, "AccountState": "ACTIVE"},
                {
                    "accountId": ACCOUNT_ID,
                    "accountPlanType": "PAID",
                    "accountPlanStatus": "ACTIVE",
                    "accountPlanRemainingCredits": {"amount": "140", "unit": "USD"},
                    "accountPlanExpirationDate": (now + timedelta(days=30)).isoformat(),
                },
                account_id=ACCOUNT_ID,
                now=now,
            )

    def test_budget_contract_and_email_hash_are_exact(self) -> None:
        email = "synthetic" + chr(64) + "example.invalid"
        email_hash = preflight._sha256_text(email)
        budget = {
            "Budget": {
                "BudgetName": "approvals-central-demo-account-guardrail",
                "BudgetType": "COST",
                "TimeUnit": "MONTHLY",
                "BudgetLimit": {"Amount": "20", "Unit": "USD"},
                "CostTypes": dict(preflight.EXPECTED_COST_TYPES),
                "Metrics": ["UnblendedCost"],
            }
        }
        notifications = {
            "Notifications": [
                {"NotificationType": "ACTUAL", "ComparisonOperator": "GREATER_THAN", "ThresholdType": "PERCENTAGE", "Threshold": 50},
                {"NotificationType": "FORECASTED", "ComparisonOperator": "GREATER_THAN", "ThresholdType": "PERCENTAGE", "Threshold": 80},
            ]
        }
        subscribers = {
            name: {"Subscribers": [{"SubscriptionType": "EMAIL", "Address": email}]}
            for name in ("ACTUAL", "FORECASTED")
        }
        result = preflight.validate_budget(
            budget,
            notifications,
            subscribers,
            account_id=ACCOUNT_ID,
            budget_name="approvals-central-demo-account-guardrail",
            email_sha256=email_hash,
        )
        self.assertEqual(result["limit_usd"], 20)
        with self.assertRaises(preflight.SalesPreflightError):
            preflight.validate_budget(
                budget,
                notifications,
                subscribers,
                account_id=ACCOUNT_ID,
                budget_name="approvals-central-demo-account-guardrail",
                email_sha256="f" * 64,
            )

    def test_role_requires_exact_seven_policy_contract_and_boundary(self) -> None:
        iam = FakeIam("approvals-central-demo")
        boundary_hash = preflight._canonical_sha256(
            iam.documents[preflight.BOUNDARY_NAME]
        )
        result = preflight.validate_role_contract(
            iam,
            account_id=ACCOUNT_ID,
            guardrail_prefix="approvals-central-demo",
            expected_contract_sha256=iam.expected_contract_hash(),
            expected_boundary_sha256=boundary_hash,
        )
        self.assertEqual(result["attached_policy_count"], 7)
        iam.pages["list_attached_role_policies"][0]["AttachedPolicies"].pop()
        with self.assertRaises(preflight.SalesPreflightError):
            preflight.validate_role_contract(
                iam,
                account_id=ACCOUNT_ID,
                guardrail_prefix="approvals-central-demo",
                expected_contract_sha256=iam.expected_contract_hash(),
                expected_boundary_sha256=boundary_hash,
            )

    def test_live_absence_rejects_any_kinesis_or_telemetry(self) -> None:
        clients = {
            "kinesis": FakePagedClient({"list_streams": [{"StreamNames": []}]}),
            "firehose": FakePagedClient({"list_delivery_streams": [{"DeliveryStreamNames": []}]}),
            "iot": FakePagedClient({"list_topic_rules": [{"rules": []}]}),
            "resourcegroupstaggingapi": FakePagedClient({"get_resources": [{"ResourceTagMappingList": []}]}),
        }
        self.assertEqual(preflight.validate_live_absence(clients)["kinesis_streams"], 0)
        clients["kinesis"] = FakePagedClient({"list_streams": [{"StreamNames": ["unexpected"]}]})
        with self.assertRaises(preflight.SalesPreflightError):
            preflight.validate_live_absence(clients)

    def test_unsubscribed_firehose_is_explicit_zero_but_other_errors_fail(self) -> None:
        class UnsubscribedFirehose:
            @staticmethod
            def can_paginate(_operation: str) -> bool:
                return False

            @staticmethod
            def list_delivery_streams(**_kwargs: object) -> dict:
                raise FakeAwsError("SubscriptionRequiredException")

        clients = {
            "kinesis": FakePagedClient({"list_streams": [{"StreamNames": []}]}),
            "firehose": UnsubscribedFirehose(),
            "iot": FakePagedClient({"list_topic_rules": [{"rules": []}]}),
            "resourcegroupstaggingapi": FakePagedClient(
                {"get_resources": [{"ResourceTagMappingList": []}]}
            ),
        }
        result = preflight.validate_live_absence(clients)
        self.assertEqual(result["firehose_delivery_streams"], 0)
        self.assertEqual(
            result["firehose_inventory_signal"], "SUBSCRIPTION_REQUIRED"
        )

        class InconclusiveFirehose(UnsubscribedFirehose):
            @staticmethod
            def list_delivery_streams(**_kwargs: object) -> dict:
                raise FakeAwsError("AccessDeniedException")

        clients["firehose"] = InconclusiveFirehose()
        with self.assertRaises(FakeAwsError):
            preflight.validate_live_absence(clients)

    def test_unsubscribed_kinesis_is_explicit_zero_but_other_errors_fail(self) -> None:
        class UnsubscribedKinesis:
            @staticmethod
            def can_paginate(_operation: str) -> bool:
                return True

            @staticmethod
            def get_paginator(_operation: str) -> FakePaginator:
                class RaisingPaginator:
                    @staticmethod
                    def paginate(**_kwargs: object):
                        raise FakeAwsError("SubscriptionRequiredException")
                        yield

                return RaisingPaginator()

        clients = {
            "kinesis": UnsubscribedKinesis(),
            "firehose": FakePagedClient(
                {"list_delivery_streams": [{"DeliveryStreamNames": []}]}
            ),
            "iot": FakePagedClient({"list_topic_rules": [{"rules": []}]}),
            "resourcegroupstaggingapi": FakePagedClient(
                {"get_resources": [{"ResourceTagMappingList": []}]}
            ),
        }
        result = preflight.validate_live_absence(clients)
        self.assertEqual(result["kinesis_streams"], 0)
        self.assertEqual(
            result["kinesis_inventory_signal"], "SUBSCRIPTION_REQUIRED"
        )

        class InconclusiveKinesis(UnsubscribedKinesis):
            @staticmethod
            def get_paginator(_operation: str) -> FakePaginator:
                class RaisingPaginator:
                    @staticmethod
                    def paginate(**_kwargs: object):
                        raise FakeAwsError("AccessDeniedException")
                        yield

                return RaisingPaginator()

        clients["kinesis"] = InconclusiveKinesis()
        with self.assertRaises(FakeAwsError):
            preflight.validate_live_absence(clients)

    def test_non_pageable_aws_lists_use_bounded_native_tokens(self) -> None:
        firehose = FakeNonPageableClient(
            "list_delivery_streams",
            [
                {
                    "DeliveryStreamNames": ["first"],
                    "HasMoreDeliveryStreams": True,
                },
                {"DeliveryStreamNames": [], "HasMoreDeliveryStreams": False},
            ],
        )
        self.assertEqual(
            len(list(preflight._pages(firehose, "list_delivery_streams"))), 2
        )
        self.assertEqual(
            firehose.calls[1]["ExclusiveStartDeliveryStreamName"], "first"
        )

        marketplace = FakeNonPageableClient(
            "search_agreements",
            [
                {"agreementViewSummaries": [], "nextToken": "opaque-next"},
                {"agreementViewSummaries": []},
            ],
        )
        self.assertEqual(
            len(list(preflight._pages(marketplace, "search_agreements"))), 2
        )
        self.assertEqual(marketplace.calls[1]["nextToken"], "opaque-next")

    def test_sanitized_output_is_0600_and_contains_no_raw_identity(self) -> None:
        report = {"schema": preflight.SCHEMA, "account_id_hash": "a" * 64}
        payload = preflight.assert_sanitized(report, account_id=ACCOUNT_ID)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preflight.json"
            preflight.write_private(path, payload)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with self.assertRaises(preflight.SalesPreflightError):
            preflight.assert_sanitized(
                {"leak": f"arn:aws:iam::{ACCOUNT_ID}:role/example"},
                account_id=ACCOUNT_ID,
            )

    def test_source_identity_and_commercial_hard_stops_are_exact(self) -> None:
        source = preflight.verify_source_caller(
            {
                "Account": ACCOUNT_ID,
                "Arn": f"arn:aws:iam::{ACCOUNT_ID}:user/approvals-local-login",
            },
            ACCOUNT_ID,
        )
        self.assertEqual(source["user_name_hash"], preflight._sha256_text("approvals-local-login"))
        marketplace = FakePagedClient({
            "search_agreements": [{"agreementViewSummaries": []}]
        })
        result = preflight.validate_commercial_hard_stops({
            "organizations": FakeOrganizations(),
            "marketplace-agreement": marketplace,
            "support": FakeSupport(),
        })
        self.assertFalse(result["organizations"]["organization_in_use"])
        self.assertEqual(
            result["support"]["signal"], "SubscriptionRequiredException"
        )

        paid_marketplace = FakePagedClient({
            "search_agreements": [{"agreementViewSummaries": [{"agreementId": "opaque"}]}]
        })
        with self.assertRaises(preflight.SalesPreflightError):
            preflight.validate_marketplace(paid_marketplace)

    def test_human_posture_requires_mfa_and_zero_permanent_keys(self) -> None:
        iam = FakePagedClient({
            "list_access_keys": [{"AccessKeyMetadata": []}],
            "list_mfa_devices": [{"MFADevices": [{"SerialNumber": "opaque"}]}],
        })
        result = preflight.validate_human_credential_posture(iam)
        self.assertEqual(result["permanent_access_key_count"], 0)
        iam.pages["list_access_keys"][0]["AccessKeyMetadata"] = [{"AccessKeyId": "redacted"}]
        with self.assertRaises(preflight.SalesPreflightError):
            preflight.validate_human_credential_posture(iam)

    def test_lambda_quota_is_exactly_ten(self) -> None:
        self.assertEqual(
            preflight.validate_lambda({
                "AccountLimit": {
                    "ConcurrentExecutions": 10,
                    "UnreservedConcurrentExecutions": 10,
                }
            })["concurrent_executions"],
            10,
        )
        with self.assertRaises(preflight.SalesPreflightError):
            preflight.validate_lambda({
                "AccountLimit": {
                    "ConcurrentExecutions": 1000,
                    "UnreservedConcurrentExecutions": 1000,
                }
            })


if __name__ == "__main__":
    unittest.main()
