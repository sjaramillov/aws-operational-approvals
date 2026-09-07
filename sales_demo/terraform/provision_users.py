#!/usr/bin/env python3
"""Provision exactly four synthetic Cognito identities for the bounded pilot.

The ``validate`` command is local-only. ``apply`` requires an explicit AWS
profile and an STS session assumed from the approved deployment role. Passwords
are either entered twice with ``getpass`` or generated in-process with the
``secrets`` module. After the AWS postflight succeeds, the same four values are
written once to an operator-selected, repository-external 0600 JSON file so the
deployed PKCE E2E can authenticate without putting secrets in arguments,
environment variables, logs or Terraform state.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import secrets
import string
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


ACCOUNT_ID_RE = re.compile(r"^[0-9]{12}$")
REGION_RE = re.compile(r"^[a-z]{2}(?:-[a-z0-9]+)+-[0-9]+$")
DEPLOYMENT_ROLE_NAME = "APPROVALS-TerraformDeploymentRole"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
E2E_SLOT_NAMES = {
    "customer-a": "customerA",
    "manager-a": "managerA",
    "customer-b": "customerB",
    "manager-b": "managerB",
}
GENERATED_PASSWORD_SYMBOLS = "!#$%&*+-=?@"


class ProvisioningError(RuntimeError):
    """Raised when identity provisioning cannot remain fail closed."""


@dataclass(frozen=True)
class UserSlot:
    username: str
    tenant_id: str
    role: str
    group: str
    display_name: str


USER_SLOTS = (
    UserSlot("customer-a", "tenant-a", "CUSTOMER", "customer", "Customer A (sintético)"),
    UserSlot("manager-a", "tenant-a", "MANAGER", "manager", "Manager A (sintético)"),
    UserSlot("customer-b", "tenant-b", "CUSTOMER", "customer", "Customer B (sintético)"),
    UserSlot("manager-b", "tenant-b", "MANAGER", "manager", "Manager B (sintético)"),
)


def validate_contract() -> dict[str, Any]:
    if len(USER_SLOTS) != 4:
        raise ProvisioningError("The provisioning contract must contain exactly four users")
    if len({slot.username for slot in USER_SLOTS}) != 4:
        raise ProvisioningError("Synthetic usernames must be unique")
    if {slot.tenant_id for slot in USER_SLOTS} != {"tenant-a", "tenant-b"}:
        raise ProvisioningError("The contract must contain exactly two synthetic tenants")
    for tenant in ("tenant-a", "tenant-b"):
        roles = {slot.role for slot in USER_SLOTS if slot.tenant_id == tenant}
        if roles != {"CUSTOMER", "MANAGER"}:
            raise ProvisioningError(f"{tenant} must have one Customer and one Manager")
    return {
        "synthetic_only": True,
        "user_count": 4,
        "users": [
            {
                "username": slot.username,
                "tenant_id": slot.tenant_id,
                "role": slot.role,
                "group": slot.group,
            }
            for slot in USER_SLOTS
        ],
        "credentials": "getpass_or_in_process_generation_then_private_0600_contract",
        "persisted_subject": "sha256_only",
    }


def validate_password(password: str, confirmation: str, username: str) -> None:
    if password != confirmation:
        raise ProvisioningError("Password confirmation does not match")
    if not 14 <= len(password) <= 256:
        raise ProvisioningError("Password must contain between 14 and 256 characters")
    checks = (
        any(char.islower() for char in password),
        any(char.isupper() for char in password),
        any(char.isdigit() for char in password),
        any(not char.isalnum() for char in password),
    )
    if not all(checks):
        raise ProvisioningError(
            "Password must contain lower, upper, numeric and symbol characters"
        )
    if username.lower() in password.lower():
        raise ProvisioningError("Password must not contain the synthetic username")


def verify_caller(
    identity: dict[str, Any], expected_account_id: str, expected_role_name: str
) -> dict[str, str]:
    if expected_role_name != DEPLOYMENT_ROLE_NAME:
        raise ProvisioningError("Deployment role name is outside the approved contract")
    if ACCOUNT_ID_RE.fullmatch(expected_account_id) is None:
        raise ProvisioningError("Expected account ID is invalid")
    if identity.get("Account") != expected_account_id:
        raise ProvisioningError("STS account does not match the approved account")
    arn = str(identity.get("Arn") or "")
    marker = f":assumed-role/{expected_role_name}/"
    if not arn.startswith("arn:") or marker not in arn:
        raise ProvisioningError("STS identity is not the approved assumed deployment role")
    return {
        "account_id_hash": hashlib.sha256(expected_account_id.encode()).hexdigest(),
        "role": expected_role_name,
    }


def _attributes(values: Iterable[dict[str, str]]) -> dict[str, str]:
    return {
        value["Name"]: value["Value"]
        for value in values
        if isinstance(value, dict) and "Name" in value and "Value" in value
    }


def _list_all_users(cognito: Any, user_pool_id: str) -> list[dict[str, Any]]:
    """Return the complete pool inventory without accepting truncated evidence."""

    users: list[dict[str, Any]] = []
    pagination_token: str | None = None
    while True:
        request: dict[str, Any] = {"UserPoolId": user_pool_id, "Limit": 60}
        if pagination_token:
            request["PaginationToken"] = pagination_token
        response = cognito.list_users(**request)
        users.extend(response.get("Users") or [])
        pagination_token = response.get("PaginationToken")
        if not pagination_token:
            return users


def _list_user_groups(cognito: Any, user_pool_id: str, username: str) -> set[str]:
    groups: set[str] = set()
    next_token: str | None = None
    while True:
        request: dict[str, Any] = {
            "UserPoolId": user_pool_id,
            "Username": username,
            "Limit": 60,
        }
        if next_token:
            request["NextToken"] = next_token
        response = cognito.admin_list_groups_for_user(**request)
        for group in response.get("Groups") or []:
            name = group.get("GroupName")
            if not isinstance(name, str) or not name:
                raise ProvisioningError(f"Cognito returned an invalid group for {username}")
            groups.add(name)
        next_token = response.get("NextToken")
        if not next_token:
            return groups


def _is_username_exists(client: Any, exc: Exception) -> bool:
    expected = getattr(getattr(client, "exceptions", object()), "UsernameExistsException", ())
    return isinstance(exc, expected) if expected else exc.__class__.__name__ == "UsernameExistsException"


def _profile_item(subject: str, slot: UserSlot) -> dict[str, dict[str, Any]]:
    subject_hash = hashlib.sha256(subject.encode()).hexdigest()
    return {
        "pk": {"S": f"IDENTITY#{subject_hash}"},
        "sk": {"S": "PROFILE"},
        "tenant_id": {"S": slot.tenant_id},
        "role": {"S": slot.role},
        "display_name": {"S": slot.display_name},
        "enabled": {"BOOL": True},
    }


def _same_profile(existing: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(existing.get(key) == value for key, value in expected.items())


def _list_identity_profiles(dynamodb: Any, table_name: str) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    exclusive_start_key: dict[str, Any] | None = None
    while True:
        request: dict[str, Any] = {
            "TableName": table_name,
            "ConsistentRead": True,
            "FilterExpression": "begins_with(pk, :identity) AND sk = :profile",
            "ExpressionAttributeValues": {
                ":identity": {"S": "IDENTITY#"},
                ":profile": {"S": "PROFILE"},
            },
        }
        if exclusive_start_key:
            request["ExclusiveStartKey"] = exclusive_start_key
        response = dynamodb.scan(**request)
        profiles.extend(response.get("Items") or [])
        if len(profiles) > len(USER_SLOTS):
            raise ProvisioningError("DynamoDB contains extra identity profiles")
        exclusive_start_key = response.get("LastEvaluatedKey")
        if not exclusive_start_key:
            return profiles


def provision_one(
    cognito: Any,
    dynamodb: Any,
    *,
    user_pool_id: str,
    table_name: str,
    slot: UserSlot,
    password: str,
) -> str:
    requested_attributes = [
        {"Name": "custom:tenant_key", "Value": slot.tenant_id},
        {"Name": "custom:pilot_role", "Value": slot.role},
    ]
    try:
        cognito.admin_create_user(
            UserPoolId=user_pool_id,
            Username=slot.username,
            UserAttributes=requested_attributes,
            MessageAction="SUPPRESS",
        )
    except Exception as exc:
        if not _is_username_exists(cognito, exc):
            raise

    user = cognito.admin_get_user(UserPoolId=user_pool_id, Username=slot.username)
    attributes = _attributes(user.get("UserAttributes") or [])
    if (
        attributes.get("custom:tenant_key") != slot.tenant_id
        or attributes.get("custom:pilot_role") != slot.role
    ):
        raise ProvisioningError(f"Existing synthetic identity {slot.username} has drifted")
    subject = attributes.get("sub")
    if not subject:
        raise ProvisioningError(f"Cognito did not return a sub for {slot.username}")

    cognito.admin_set_user_password(
        UserPoolId=user_pool_id,
        Username=slot.username,
        Password=password,
        Permanent=True,
    )
    cognito.admin_enable_user(
        UserPoolId=user_pool_id,
        Username=slot.username,
    )
    cognito.admin_add_user_to_group(
        UserPoolId=user_pool_id,
        Username=slot.username,
        GroupName=slot.group,
    )

    expected_item = _profile_item(subject, slot)
    key = {"pk": expected_item["pk"], "sk": expected_item["sk"]}
    existing = dynamodb.get_item(
        TableName=table_name,
        Key=key,
        ConsistentRead=True,
    ).get("Item")
    if existing:
        if not _same_profile(existing, expected_item):
            raise ProvisioningError(f"Identity mapping for {slot.username} has drifted")
    else:
        dynamodb.put_item(
            TableName=table_name,
            Item=expected_item,
            ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
        )
    return hashlib.sha256(subject.encode()).hexdigest()


def verify_targets(
    cognito: Any,
    dynamodb: Any,
    *,
    user_pool_id: str,
    table_name: str,
    pilot_id: str,
    require_complete: bool = False,
) -> None:
    pool = cognito.describe_user_pool(UserPoolId=user_pool_id)["UserPool"]
    if not pool.get("AdminCreateUserConfig", {}).get("AllowAdminCreateUserOnly"):
        raise ProvisioningError("Cognito self-signup is not disabled")
    for group in ("customer", "manager"):
        cognito.get_group(GroupName=group, UserPoolId=user_pool_id)

    slots_by_username = {slot.username: slot for slot in USER_SLOTS}
    users = _list_all_users(cognito, user_pool_id)
    usernames = [str(user.get("Username") or "") for user in users]
    if any(not username for username in usernames):
        raise ProvisioningError("Cognito returned a user without a username")
    if len(set(usernames)) != len(usernames):
        raise ProvisioningError("Cognito returned duplicate usernames")
    unexpected = sorted(set(usernames) - set(slots_by_username))
    if unexpected:
        raise ProvisioningError(
            "Cognito contains identities outside the four-user synthetic allowlist"
        )
    if require_complete and set(usernames) != set(slots_by_username):
        raise ProvisioningError("Cognito does not contain all four synthetic identities")
    expected_profiles: dict[str, dict[str, Any]] = {}
    for user in users:
        username = str(user["Username"])
        slot = slots_by_username[username]
        if require_complete and (
            user.get("Enabled") is not True or user.get("UserStatus") != "CONFIRMED"
        ):
            raise ProvisioningError(
                f"Existing synthetic identity {username} is not enabled and CONFIRMED"
            )
        attributes = _attributes(user.get("Attributes") or [])
        if (
            attributes.get("custom:tenant_key") != slot.tenant_id
            or attributes.get("custom:pilot_role") != slot.role
        ):
            raise ProvisioningError(f"Existing synthetic identity {username} has drifted")
        if require_complete:
            subject = attributes.get("sub")
            if not subject:
                raise ProvisioningError(
                    f"Existing synthetic identity {username} has no stable subject"
                )
            expected = _profile_item(subject, slot)
            expected_profiles[expected["pk"]["S"]] = expected
        groups = _list_user_groups(cognito, user_pool_id, username)
        # An empty set is recoverable after an interrupted AdminCreateUser. Any
        # wrong or additional group would break tenant/role isolation and aborts.
        if require_complete and groups != {slot.group}:
            raise ProvisioningError(
                f"Existing synthetic identity {username} is missing its exact group"
            )
        if not groups.issubset({slot.group}):
            raise ProvisioningError(f"Existing synthetic identity {username} has group drift")

    table = dynamodb.describe_table(TableName=table_name)["Table"]
    key_schema = {entry["AttributeName"]: entry["KeyType"] for entry in table["KeySchema"]}
    if key_schema != {"pk": "HASH", "sk": "RANGE"}:
        raise ProvisioningError("DynamoDB key schema does not match the sales contract")
    if require_complete:
        profiles = _list_identity_profiles(dynamodb, table_name)
        observed: dict[str, dict[str, Any]] = {}
        for profile in profiles:
            key = profile.get("pk", {}).get("S")
            if not isinstance(key, str) or key in observed:
                raise ProvisioningError("DynamoDB returned an invalid identity profile")
            observed[key] = profile
        if set(observed) != set(expected_profiles):
            raise ProvisioningError(
                "DynamoDB identity profile inventory is not exactly four mappings"
            )
        if any(observed[key] != expected for key, expected in expected_profiles.items()):
            raise ProvisioningError("DynamoDB identity profile mapping has drifted")
    config = dynamodb.get_item(
        TableName=table_name,
        Key={"pk": {"S": f"PILOT#{pilot_id}"}, "sk": {"S": "META"}},
        ConsistentRead=True,
    ).get("Item")
    if not config:
        raise ProvisioningError("Pilot configuration is unavailable")
    status = config.get("status", {}).get("S")
    if status not in {"PREPARED", "ACTIVE"}:
        raise ProvisioningError("Pilot is neither PREPARED nor ACTIVE")
    try:
        expires_at = int(config["expires_at_epoch"]["N"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProvisioningError("Pilot expiry is unavailable") from exc
    if status == "PREPARED" and expires_at != 0:
        raise ProvisioningError("PREPARED pilot must not have an activation expiry")
    if status == "ACTIVE" and expires_at <= int(time.time()):
        raise ProvisioningError("Pilot has already expired")


def _collect_passwords(
    prompt: Callable[[str], str] = getpass.getpass,
) -> dict[str, str]:
    passwords: dict[str, str] = {}
    for slot in USER_SLOTS:
        first = prompt(f"Clave privada para {slot.username}: ")
        second = prompt(f"Confirmar clave para {slot.username}: ")
        validate_password(first, second, slot.username)
        passwords[slot.username] = first
    return passwords


def _generate_password(username: str, length: int = 32) -> str:
    """Generate a Cognito-compatible password without logging or persistence."""

    if length < 14:
        raise ProvisioningError("Generated password length is below the policy minimum")
    alphabet = string.ascii_letters + string.digits + GENERATED_PASSWORD_SYMBOLS
    for _ in range(128):
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        try:
            validate_password(candidate, candidate, username)
        except ProvisioningError:
            continue
        return candidate
    raise ProvisioningError("Could not generate a password satisfying the policy")


def _generate_passwords() -> dict[str, str]:
    passwords = {slot.username: _generate_password(slot.username) for slot in USER_SLOTS}
    if len(set(passwords.values())) != len(USER_SLOTS):
        passwords.clear()
        raise ProvisioningError("Generated passwords must be unique")
    return passwords


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_private_e2e_destination(output: Path) -> Path:
    """Resolve a new private destination before any remote identity mutation."""

    if not output.is_absolute():
        raise ProvisioningError("E2E credentials output must be an absolute path")
    parent = output.parent
    try:
        parent_metadata = parent.stat()
        resolved_parent = parent.resolve(strict=True)
    except OSError as exc:
        raise ProvisioningError("E2E credentials parent is unavailable") from exc
    if parent.is_symlink() or not parent.is_dir():
        raise ProvisioningError("E2E credentials parent must be a real directory")
    if (parent_metadata.st_mode & 0o077) != 0:
        raise ProvisioningError("E2E credentials parent must not be group/world accessible")
    if hasattr(os, "getuid") and parent_metadata.st_uid != os.getuid():
        raise ProvisioningError("E2E credentials parent must belong to the current user")

    resolved_output = resolved_parent / output.name
    if _is_within(resolved_output, REPOSITORY_ROOT.resolve()):
        raise ProvisioningError("E2E credentials must live outside the repository")
    if output.exists() or output.is_symlink():
        raise ProvisioningError("E2E credentials output already exists")
    return resolved_output


def write_private_e2e_credentials(
    output: Path,
    passwords: dict[str, str],
) -> None:
    """Write the exact deployed-E2E credential contract without replacement."""

    resolved_output = validate_private_e2e_destination(output)
    if set(passwords) != set(E2E_SLOT_NAMES):
        raise ProvisioningError("E2E credentials do not cover the exact four users")

    payload = {
        "schemaVersion": 1,
        "syntheticOnly": True,
        "users": {
            E2E_SLOT_NAMES[slot.username]: {
                "username": slot.username,
                "password": passwords[slot.username],
            }
            for slot in USER_SLOTS
        },
    }
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(resolved_output, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise ProvisioningError("E2E credentials could not be written safely") from exc


def apply(args: argparse.Namespace) -> dict[str, Any]:
    validate_contract()
    if REGION_RE.fullmatch(args.region) is None:
        raise ProvisioningError("AWS region is invalid")
    # Validate twice: first before any AWS call/mutation, then immediately before
    # O_EXCL creation to fail closed against races or operator mistakes.
    validate_private_e2e_destination(args.credentials_output)

    import boto3  # imported only in the explicit AWS apply mode

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    caller = verify_caller(
        session.client("sts").get_caller_identity(),
        args.expected_account_id,
        args.expected_role_name,
    )
    cognito = session.client("cognito-idp")
    dynamodb = session.client("dynamodb")
    verify_targets(
        cognito,
        dynamodb,
        user_pool_id=args.user_pool_id,
        table_name=args.table_name,
        pilot_id=args.pilot_id,
    )

    passwords = (
        _generate_passwords()
        if args.generate_private_credentials
        else _collect_passwords()
    )
    hashes: dict[str, str] = {}
    try:
        for slot in USER_SLOTS:
            hashes[slot.username] = provision_one(
                cognito,
                dynamodb,
                user_pool_id=args.user_pool_id,
                table_name=args.table_name,
                slot=slot,
                password=passwords[slot.username],
            )
        verify_targets(
            cognito,
            dynamodb,
            user_pool_id=args.user_pool_id,
            table_name=args.table_name,
            pilot_id=args.pilot_id,
            require_complete=True,
        )
        write_private_e2e_credentials(args.credentials_output, passwords)
    finally:
        passwords.clear()

    return {
        "status": "PROVISIONED",
        "account_id_hash": caller["account_id_hash"],
        "assumed_role": caller["role"],
        "user_count": len(hashes),
        "subject_hashes": hashes,
        "credentials_private_file_created": True,
        "credentials_file_mode": "0600",
        "credentials_path_reported": False,
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    commands = value.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="Validate the four-user contract without AWS")
    apply_parser = commands.add_parser("apply", help="Provision users using an assumed role")
    apply_parser.add_argument("--profile", required=True)
    apply_parser.add_argument("--expected-account-id", required=True)
    apply_parser.add_argument(
        "--expected-role-name",
        default=DEPLOYMENT_ROLE_NAME,
        choices=[DEPLOYMENT_ROLE_NAME],
    )
    apply_parser.add_argument("--region", required=True)
    apply_parser.add_argument("--user-pool-id", required=True)
    apply_parser.add_argument("--table-name", required=True)
    apply_parser.add_argument("--pilot-id", required=True)
    apply_parser.add_argument(
        "--credentials-output",
        type=Path,
        required=True,
        help="Absolute, new path outside the repository in a private directory",
    )
    apply_parser.add_argument(
        "--generate-private-credentials",
        action="store_true",
        help=(
            "Generate four unique credentials in-process instead of prompting; "
            "values are written only to --credentials-output after postflight"
        ),
    )
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = validate_contract() if args.command == "validate" else apply(args)
    except (ProvisioningError, KeyboardInterrupt) as exc:
        message = "interrupted" if isinstance(exc, KeyboardInterrupt) else str(exc)
        print(f"user provisioning: FAIL: {message}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
