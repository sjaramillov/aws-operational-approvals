from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "provision_users.py"
SPEC = importlib.util.spec_from_file_location("provision_users", MODULE_PATH)
assert SPEC and SPEC.loader
provision_users = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = provision_users
SPEC.loader.exec_module(provision_users)


class FakeCognito:
    class exceptions:
        class UsernameExistsException(Exception):
            pass

    def __init__(self, users=None, groups_by_user=None) -> None:
        self.passwords: list[dict] = []
        self.groups: list[dict] = []
        self.users = list(users or [])
        self.groups_by_user = dict(groups_by_user or {})
        self.enabled: list[str] = []

    def describe_user_pool(self, **_kwargs):
        return {"UserPool": {"AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True}}}

    def get_group(self, **_kwargs):
        return {"Group": {}}

    def list_users(self, **_kwargs):
        return {"Users": self.users}

    def admin_list_groups_for_user(self, **kwargs):
        return {
            "Groups": [
                {"GroupName": name}
                for name in self.groups_by_user.get(kwargs["Username"], [])
            ]
        }

    def admin_create_user(self, **_kwargs):
        return {}

    def admin_get_user(self, **kwargs):
        return {
            "Username": kwargs["Username"],
            "UserAttributes": [
                {"Name": "sub", "Value": "synthetic-sub-a"},
                {"Name": "custom:tenant_key", "Value": "tenant-a"},
                {"Name": "custom:pilot_role", "Value": "CUSTOMER"},
            ],
        }

    def admin_set_user_password(self, **kwargs):
        self.passwords.append(kwargs)

    def admin_add_user_to_group(self, **kwargs):
        self.groups.append(kwargs)

    def admin_enable_user(self, **kwargs):
        self.enabled.append(kwargs["Username"])


class FakeDynamo:
    def __init__(self, *, with_config=False, pilot_status="ACTIVE", profiles=None) -> None:
        self.item = None
        self.with_config = with_config
        self.pilot_status = pilot_status
        self.profiles = list(profiles or [])

    def describe_table(self, **_kwargs):
        return {
            "Table": {
                "KeySchema": [
                    {"AttributeName": "pk", "KeyType": "HASH"},
                    {"AttributeName": "sk", "KeyType": "RANGE"},
                ]
            }
        }

    def get_item(self, **kwargs):
        if self.with_config and kwargs["Key"]["pk"]["S"].startswith("PILOT#"):
            return {
                "Item": {
                    "status": {"S": self.pilot_status},
                    "expires_at_epoch": {
                        "N": "0" if self.pilot_status == "PREPARED" else str(int(time.time()) + 3600)
                    },
                }
            }
        return {}

    def put_item(self, **kwargs):
        self.item = kwargs["Item"]

    def scan(self, **_kwargs):
        return {"Items": list(self.profiles)}


class ProvisionUsersTests(unittest.TestCase):
    @staticmethod
    def _valid_credential() -> str:
        return "".join(("Correct", "-Horse", "-9!", "Battery"))

    @staticmethod
    def _complete_inventory():
        users = []
        groups = {}
        profiles = []
        for index, slot in enumerate(provision_users.USER_SLOTS):
            subject = f"synthetic-sub-{index}"
            users.append(
                {
                    "Username": slot.username,
                    "Enabled": True,
                    "UserStatus": "CONFIRMED",
                    "Attributes": [
                        {"Name": "sub", "Value": subject},
                        {"Name": "custom:tenant_key", "Value": slot.tenant_id},
                        {"Name": "custom:pilot_role", "Value": slot.role},
                    ],
                }
            )
            groups[slot.username] = [slot.group]
            profiles.append(provision_users._profile_item(subject, slot))
        return users, groups, profiles

    def test_contract_has_four_synthetic_users(self) -> None:
        result = provision_users.validate_contract()
        self.assertEqual(result["user_count"], 4)
        self.assertTrue(result["synthetic_only"])

    def test_password_is_validated_without_persistence(self) -> None:
        value = self._valid_credential()
        provision_users.validate_password(value, value, "customer-a")
        with self.assertRaises(provision_users.ProvisioningError):
            provision_users.validate_password("short", "short", "customer-a")
        with self.assertRaises(provision_users.ProvisioningError):
            provision_users.validate_password(value, "different", "customer-a")

    def test_generated_credentials_are_unique_and_policy_compliant(self) -> None:
        passwords = provision_users._generate_passwords()
        self.assertEqual(set(passwords), {slot.username for slot in provision_users.USER_SLOTS})
        self.assertEqual(len(set(passwords.values())), 4)
        for username, password in passwords.items():
            self.assertEqual(len(password), 32)
            provision_users.validate_password(password, password, username)

    def test_generated_credentials_flag_is_explicit(self) -> None:
        arguments = provision_users.parser().parse_args(
            [
                "apply",
                "--profile",
                "approvals-mvp-deployment",
                "--expected-account-id",
                "0" * 12,
                "--region",
                "us-east-1",
                "--user-pool-id",
                "us-east-1_example",
                "--table-name",
                "example",
                "--pilot-id",
                "example",
                "--credentials-output",
                "/private/tmp/credentials.json",
                "--generate-private-credentials",
            ]
        )
        self.assertTrue(arguments.generate_private_credentials)

    def test_private_e2e_contract_is_exact_0600_and_never_overwritten(self) -> None:
        value = self._valid_credential()
        passwords = {slot.username: value for slot in provision_users.USER_SLOTS}
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o700)
            path = Path(directory) / "credentials.json"
            provision_users.write_private_e2e_credentials(path, passwords)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(payload), {"schemaVersion", "syntheticOnly", "users"})
            self.assertTrue(payload["syntheticOnly"])
            self.assertEqual(
                {entry["username"] for entry in payload["users"].values()},
                {slot.username for slot in provision_users.USER_SLOTS},
            )
            with self.assertRaises(provision_users.ProvisioningError):
                provision_users.write_private_e2e_credentials(path, passwords)

    def test_private_e2e_contract_rejects_relative_or_public_parent(self) -> None:
        value = self._valid_credential()
        passwords = {slot.username: value for slot in provision_users.USER_SLOTS}
        with self.assertRaises(provision_users.ProvisioningError):
            provision_users.write_private_e2e_credentials(Path("credentials.json"), passwords)
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o755)
            with self.assertRaises(provision_users.ProvisioningError):
                provision_users.write_private_e2e_credentials(
                    Path(directory) / "credentials.json", passwords
                )

    def test_generated_apply_never_reports_credentials(self) -> None:
        account_id = "0" * 12
        generated = {
            slot.username: f"Private-{index}-Credential!9Z"
            for index, slot in enumerate(provision_users.USER_SLOTS)
        }

        class FakeSts:
            @staticmethod
            def get_caller_identity():
                return {
                    "Account": account_id,
                    "Arn": (
                        f"arn:aws:sts::{account_id}:assumed-role/"
                        "APPROVALS-TerraformDeploymentRole/session"
                    ),
                }

        class FakeSession:
            @staticmethod
            def client(name):
                return FakeSts() if name == "sts" else object()

        fake_boto3 = types.SimpleNamespace(
            Session=lambda **_kwargs: FakeSession()
        )
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o700)
            path = Path(directory) / "credentials.json"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.dict(sys.modules, {"boto3": fake_boto3}),
                mock.patch.object(provision_users, "_generate_passwords", return_value=dict(generated)),
                mock.patch.object(provision_users, "verify_targets"),
                mock.patch.object(
                    provision_users,
                    "provision_one",
                    side_effect=[f"hash-{index}" for index in range(4)],
                ),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                status = provision_users.main(
                    [
                        "apply",
                        "--profile",
                        "approvals-mvp-deployment",
                        "--expected-account-id",
                        account_id,
                        "--region",
                        "us-east-1",
                        "--user-pool-id",
                        "us-east-1_example",
                        "--table-name",
                        "example",
                        "--pilot-id",
                        "example",
                        "--credentials-output",
                        str(path),
                        "--generate-private-credentials",
                    ]
                )
            self.assertEqual(status, 0)
            self.assertEqual(stderr.getvalue(), "")
            result = json.loads(stdout.getvalue())
            self.assertFalse(result["credentials_path_reported"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            observable = stdout.getvalue() + stderr.getvalue() + json.dumps(result)
            for secret in generated.values():
                self.assertNotIn(secret, observable)

    def test_only_expected_assumed_role_is_accepted(self) -> None:
        account_id = "0" * 12
        value = provision_users.verify_caller(
            {
                "Account": account_id,
                "Arn": f"arn:aws:sts::{account_id}:assumed-role/APPROVALS-TerraformDeploymentRole/session",
            },
            account_id,
            "APPROVALS-TerraformDeploymentRole",
        )
        self.assertEqual(value["role"], "APPROVALS-TerraformDeploymentRole")
        with self.assertRaises(provision_users.ProvisioningError):
            provision_users.verify_caller(
                {"Account": account_id, "Arn": f"arn:aws:iam::{account_id}:user/admin"},
                account_id,
                "APPROVALS-TerraformDeploymentRole",
            )
        with self.assertRaises(provision_users.ProvisioningError):
            provision_users.verify_caller(
                {
                    "Account": account_id,
                    "Arn": f"arn:aws:sts::{account_id}:assumed-role/Administrator/session",
                },
                account_id,
                "Administrator",
            )

    def test_profile_mapping_stores_only_subject_hash(self) -> None:
        cognito = FakeCognito()
        dynamodb = FakeDynamo()
        value = self._valid_credential()
        subject_hash = provision_users.provision_one(
            cognito,
            dynamodb,
            user_pool_id="us-east-1_synthetic",
            table_name="approvals-sales-demo-records",
            slot=provision_users.USER_SLOTS[0],
            password=value,
        )
        self.assertIn(subject_hash, dynamodb.item["pk"]["S"])
        self.assertNotIn("synthetic-sub-a", str(dynamodb.item))
        self.assertNotIn(value, str(dynamodb.item))
        self.assertEqual(cognito.groups[0]["GroupName"], "customer")
        self.assertEqual(cognito.enabled, ["customer-a"])

    def test_target_verification_accepts_zero_to_four_allowlisted_users(self) -> None:
        existing = [
            {
                "Username": "customer-a",
                "Attributes": [
                    {"Name": "custom:tenant_key", "Value": "tenant-a"},
                    {"Name": "custom:pilot_role", "Value": "CUSTOMER"},
                ],
            }
        ]
        provision_users.verify_targets(
            FakeCognito(existing, {"customer-a": ["customer"]}),
            FakeDynamo(with_config=True),
            user_pool_id="us-east-1_synthetic",
            table_name="approvals-sales-demo-records",
            pilot_id="demo-sales-plus",
        )

    def test_target_verification_accepts_prepared_before_activation(self) -> None:
        provision_users.verify_targets(
            FakeCognito(),
            FakeDynamo(with_config=True, pilot_status="PREPARED"),
            user_pool_id="us-east-1_synthetic",
            table_name="approvals-sales-demo-records",
            pilot_id="demo-sales-plus",
        )

    def test_target_verification_rejects_fifth_user_and_group_drift(self) -> None:
        unexpected = [{"Username": "unexpected", "Attributes": []}]
        with self.assertRaises(provision_users.ProvisioningError):
            provision_users.verify_targets(
                FakeCognito(unexpected),
                FakeDynamo(with_config=True),
                user_pool_id="us-east-1_synthetic",
                table_name="approvals-sales-demo-records",
                pilot_id="demo-sales-plus",
            )

    def test_complete_postflight_requires_all_four_users(self) -> None:
        with self.assertRaises(provision_users.ProvisioningError):
            provision_users.verify_targets(
                FakeCognito(),
                FakeDynamo(with_config=True),
                user_pool_id="us-east-1_synthetic",
                table_name="approvals-sales-demo-records",
                pilot_id="demo-sales-plus",
                require_complete=True,
            )

        complete, groups, profiles = self._complete_inventory()
        provision_users.verify_targets(
            FakeCognito(complete, groups),
            FakeDynamo(with_config=True, profiles=profiles),
            user_pool_id="us-east-1_synthetic",
            table_name="approvals-sales-demo-records",
            pilot_id="demo-sales-plus",
            require_complete=True,
        )

    def test_complete_postflight_requires_exact_dynamodb_mappings(self) -> None:
        complete, groups, profiles = self._complete_inventory()
        with self.assertRaises(provision_users.ProvisioningError):
            provision_users.verify_targets(
                FakeCognito(complete, groups),
                FakeDynamo(with_config=True, profiles=profiles[:-1]),
                user_pool_id="us-east-1_synthetic",
                table_name="approvals-sales-demo-records",
                pilot_id="demo-sales-plus",
                require_complete=True,
            )

        drifted = [dict(item) for item in profiles]
        drifted[0] = dict(drifted[0])
        drifted[0]["tenant_id"] = {"S": "tenant-b"}
        with self.assertRaises(provision_users.ProvisioningError):
            provision_users.verify_targets(
                FakeCognito(complete, groups),
                FakeDynamo(with_config=True, profiles=drifted),
                user_pool_id="us-east-1_synthetic",
                table_name="approvals-sales-demo-records",
                pilot_id="demo-sales-plus",
                require_complete=True,
            )

    def test_complete_postflight_rejects_disabled_or_unconfirmed_user(self) -> None:
        slot = provision_users.USER_SLOTS[0]
        user = {
            "Username": slot.username,
            "Enabled": False,
            "UserStatus": "CONFIRMED",
            "Attributes": [
                {"Name": "custom:tenant_key", "Value": slot.tenant_id},
                {"Name": "custom:pilot_role", "Value": slot.role},
            ],
        }
        with self.assertRaises(provision_users.ProvisioningError):
            provision_users.verify_targets(
                FakeCognito([user], {slot.username: [slot.group]}),
                FakeDynamo(with_config=True),
                user_pool_id="us-east-1_synthetic",
                table_name="approvals-sales-demo-records",
                pilot_id="demo-sales-plus",
                require_complete=True,
            )

    def test_target_verification_rejects_group_drift(self) -> None:

        existing = [
            {
                "Username": "customer-a",
                "Attributes": [
                    {"Name": "custom:tenant_key", "Value": "tenant-a"},
                    {"Name": "custom:pilot_role", "Value": "CUSTOMER"},
                ],
            }
        ]
        with self.assertRaises(provision_users.ProvisioningError):
            provision_users.verify_targets(
                FakeCognito(existing, {"customer-a": ["manager"]}),
                FakeDynamo(with_config=True),
                user_pool_id="us-east-1_synthetic",
                table_name="approvals-sales-demo-records",
                pilot_id="demo-sales-plus",
            )


if __name__ == "__main__":
    unittest.main()
