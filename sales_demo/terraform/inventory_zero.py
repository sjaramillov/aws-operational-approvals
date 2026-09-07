#!/usr/bin/env python3
"""Prove that no ``approvals-sales-demo`` workload inventory remains after teardown.

The probe is read-only, paginated and fail-closed. Real identifiers are used in
memory only; persisted findings contain counts and SHA-256 fingerprints.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


DEPLOYMENT_ROLE_NAME = "APPROVALS-TerraformDeploymentRole"
RESOURCE_PREFIX = "approvals-sales-demo"
DEFAULT_GUARDRAIL_PREFIX = "approvals-central-demo"
MAX_PAGES = 1_000
MAX_ITEMS = 10_000


class InventoryError(RuntimeError):
    """The teardown inventory cannot be proven safely."""


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def verify_caller(identity: Mapping[str, Any], account_id: str) -> str:
    if re.fullmatch(r"[0-9]{12}", account_id) is None:
        raise InventoryError("Expected account ID is invalid")
    arn = str(identity.get("Arn") or "")
    if (
        identity.get("Account") != account_id
        or f":assumed-role/{DEPLOYMENT_ROLE_NAME}/" not in arn
    ):
        raise InventoryError("STS identity is not the approved assumed deployment role")
    return _fingerprint(arn)


def _pages(client: Any, operation: str, **kwargs: Any) -> Iterable[Mapping[str, Any]]:
    paginator = client.get_paginator(operation)
    for index, page in enumerate(paginator.paginate(**kwargs), start=1):
        if index > MAX_PAGES:
            raise InventoryError(f"{operation} exceeded the page bound")
        if not isinstance(page, Mapping):
            raise InventoryError(f"{operation} returned a malformed page")
        yield page


def _nested_items(page: Mapping[str, Any], *keys: str) -> list[Any]:
    value: Any = page
    for key in keys:
        if not isinstance(value, Mapping):
            return []
        value = value.get(key)
    return list(value or []) if isinstance(value, list) else []


def _matching(values: Iterable[Any], name: Callable[[Any], str]) -> list[str]:
    matches = [name(value) for value in values if name(value).startswith(RESOURCE_PREFIX)]
    if len(matches) > MAX_ITEMS:
        raise InventoryError("Inventory exceeded the item bound")
    return matches


def _guardrail_policy_names(guardrail_prefix: str) -> set[str]:
    if re.fullmatch(r"[a-z][a-z0-9-]{2,28}", guardrail_prefix) is None:
        raise InventoryError("Guardrail prefix is invalid")
    return {
        "approvals-sales-demo-application-boundary",
        f"{guardrail_prefix}-terraform-sales-demo",
        f"{guardrail_prefix}-terraform-sales-demo-edge",
    }


def collect_inventory(
    clients: Mapping[str, Any], *, guardrail_prefix: str = DEFAULT_GUARDRAIL_PREFIX
) -> dict[str, Any]:
    findings: dict[str, list[str]] = {}
    errors: list[dict[str, str]] = []
    guardrail_names = _guardrail_policy_names(guardrail_prefix)
    expected_guardrails: dict[str, Any] = {}

    def record(kind: str, identifiers: Iterable[str]) -> None:
        values = sorted(set(str(value) for value in identifiers if value))
        if len(values) > MAX_ITEMS:
            raise InventoryError(f"{kind} exceeded the item bound")
        if values:
            findings[kind] = [_fingerprint(f"{kind}:{value}") for value in values]

    def checked(label: str, operation: Callable[[], None]) -> None:
        try:
            operation()
        except Exception as exc:  # AWS client exceptions are intentionally sanitized.
            errors.append({"collector": label, "error_type": type(exc).__name__})

    def api_gateway() -> None:
        items = [
            item
            for page in _pages(clients["apigatewayv2"], "get_apis")
            for item in page.get("Items", [])
        ]
        record("apigateway_http_api", _matching(items, lambda item: str(item.get("Name") or "")))

    def cognito() -> None:
        pools = [
            item
            for page in _pages(
                clients["cognito-idp"], "list_user_pools", PaginationConfig={"PageSize": 60}
            )
            for item in page.get("UserPools", [])
        ]
        matching_pools = [
            pool for pool in pools if str(pool.get("Name") or "").startswith(RESOURCE_PREFIX)
        ]
        record("cognito_user_pool", [str(pool.get("Id") or "") for pool in matching_pools])
        for pool in matching_pools:
            pool_id = str(pool.get("Id") or "")
            description = clients["cognito-idp"].describe_user_pool(
                UserPoolId=pool_id
            ).get("UserPool", {})
            record(
                "cognito_domain",
                [
                    f"{pool_id}:{description.get(key, '')}"
                    for key in ("Domain", "CustomDomain")
                    if description.get(key)
                ],
            )
            for operation, key, kind, name_key in (
                ("list_user_pool_clients", "UserPoolClients", "cognito_app_client", "ClientId"),
                ("list_groups", "Groups", "cognito_group", "GroupName"),
                ("list_users", "Users", "cognito_user", "Username"),
            ):
                values = [
                    item
                    for page in _pages(clients["cognito-idp"], operation, UserPoolId=pool_id)
                    for item in page.get(key, [])
                ]
                record(kind, [f"{pool_id}:{item.get(name_key, '')}" for item in values])

    def lambda_functions() -> None:
        values = [
            item
            for page in _pages(clients["lambda"], "list_functions")
            for item in page.get("Functions", [])
        ]
        record("lambda_function", _matching(values, lambda item: str(item.get("FunctionName") or "")))

    def dynamodb() -> None:
        values = [
            name
            for page in _pages(clients["dynamodb"], "list_tables")
            for name in page.get("TableNames", [])
        ]
        record("dynamodb_table", _matching(values, str))

    def step_functions() -> None:
        values = [
            item
            for page in _pages(clients["stepfunctions"], "list_state_machines")
            for item in page.get("stateMachines", [])
        ]
        machines = [
            item for item in values if str(item.get("name") or "").startswith(RESOURCE_PREFIX)
        ]
        record("stepfunctions_state_machine", [str(item.get("stateMachineArn") or "") for item in machines])
        for machine in machines:
            arn = str(machine.get("stateMachineArn") or "")
            executions = [
                item
                for page in _pages(
                    clients["stepfunctions"], "list_executions", stateMachineArn=arn
                )
                for item in page.get("executions", [])
            ]
            record("stepfunctions_execution", [str(item.get("executionArn") or "") for item in executions])

    def scheduler() -> None:
        values = [
            item
            for page in _pages(
                clients["scheduler"],
                "list_schedules",
                GroupName="default",
                NamePrefix=RESOURCE_PREFIX,
            )
            for item in page.get("Schedules", [])
        ]
        record("scheduler_schedule", [str(item.get("Name") or "") for item in values])

    def logs() -> None:
        values: list[Mapping[str, Any]] = []
        for prefix in (
            f"/aws/lambda/{RESOURCE_PREFIX}",
            f"/aws/vendedlogs/states/{RESOURCE_PREFIX}",
        ):
            values.extend(
                item
                for page in _pages(
                    clients["logs"], "describe_log_groups", logGroupNamePrefix=prefix
                )
                for item in page.get("logGroups", [])
            )
        record("cloudwatch_log_group", [str(item.get("logGroupName") or "") for item in values])

    def cloudfront() -> None:
        distributions = [
            item
            for page in _pages(clients["cloudfront"], "list_distributions")
            for item in _nested_items(page, "DistributionList", "Items")
        ]
        selected: list[str] = []
        for distribution in distributions:
            arn = str(distribution.get("ARN") or "")
            tags = clients["cloudfront"].list_tags_for_resource(Resource=arn).get("Tags", {})
            tag_map = {item.get("Key"): item.get("Value") for item in tags.get("Items", [])}
            if tag_map.get("DeploymentProfile") == "sales_demo" or str(
                distribution.get("Comment") or ""
            ).startswith("Aprobaciones operativas"):
                selected.append(arn)
        record("cloudfront_distribution", selected)

        for operation, root, wrapper, config_key, kind in (
            (
                "list_origin_access_controls",
                "OriginAccessControlList",
                None,
                "OriginAccessControlConfig",
                "cloudfront_oac",
            ),
            (
                "list_cache_policies",
                "CachePolicyList",
                "CachePolicy",
                "CachePolicyConfig",
                "cloudfront_cache_policy",
            ),
            (
                "list_response_headers_policies",
                "ResponseHeadersPolicyList",
                "ResponseHeadersPolicy",
                "ResponseHeadersPolicyConfig",
                "cloudfront_response_headers_policy",
            ),
        ):
            names: list[str] = []
            for page in _pages(clients["cloudfront"], operation):
                for raw in _nested_items(page, root, "Items"):
                    item = raw.get(wrapper, {}) if wrapper else raw
                    config = item.get(config_key, {})
                    names.append(str(config.get("Name") or item.get("Name") or ""))
            record(kind, _matching(names, str))

    def iam() -> None:
        roles = [
            item
            for page in _pages(clients["iam"], "list_roles")
            for item in page.get("Roles", [])
        ]
        matching_roles = [
            role for role in roles if str(role.get("RoleName") or "").startswith(RESOURCE_PREFIX)
        ]
        record("iam_role", [str(role.get("RoleName") or "") for role in matching_roles])
        for role in matching_roles:
            name = str(role.get("RoleName") or "")
            attached = [
                item
                for page in _pages(clients["iam"], "list_attached_role_policies", RoleName=name)
                for item in page.get("AttachedPolicies", [])
            ]
            record("iam_role_attachment", [f"{name}:{item.get('PolicyArn', '')}" for item in attached])

        policies = [
            item
            for page in _pages(clients["iam"], "list_policies", Scope="Local")
            for item in page.get("Policies", [])
        ]
        present_guardrails = {
            str(policy.get("PolicyName") or "")
            for policy in policies
            if str(policy.get("PolicyName") or "") in guardrail_names
        }
        expected_guardrails.update(
            {
                "expected_count": len(guardrail_names),
                "present_count": len(present_guardrails),
                "policy_name_hashes": sorted(_fingerprint(name) for name in present_guardrails),
            }
        )
        if present_guardrails != guardrail_names:
            errors.append(
                {
                    "collector": "expected_sales_guardrails",
                    "error_type": "PersistentGuardrailSetDrifted",
                }
            )
        matching_policies = [
            policy
            for policy in policies
            if str(policy.get("PolicyName") or "").startswith(RESOURCE_PREFIX)
            and str(policy.get("PolicyName") or "") not in guardrail_names
        ]
        record("iam_policy", [str(policy.get("Arn") or "") for policy in matching_policies])
        for policy in matching_policies:
            arn = str(policy.get("Arn") or "")
            entities = [
                page
                for page in _pages(
                    clients["iam"], "list_entities_for_policy", PolicyArn=arn
                )
            ]
            attached = [
                f"{arn}:{kind}:{item.get(name_key, '')}"
                for page in entities
                for kind, key, name_key in (
                    ("role", "PolicyRoles", "RoleName"),
                    ("group", "PolicyGroups", "GroupName"),
                    ("user", "PolicyUsers", "UserName"),
                )
                for item in page.get(key, [])
            ]
            record("iam_policy_attachment", attached)

    def s3() -> None:
        buckets: list[Mapping[str, Any]] = []
        token: str | None = None
        for page_number in range(1, MAX_PAGES + 1):
            request: dict[str, Any] = {
                "MaxBuckets": 1_000,
                "Prefix": RESOURCE_PREFIX,
            }
            if token:
                request["ContinuationToken"] = token
            page = clients["s3"].list_buckets(**request)
            buckets.extend(page.get("Buckets", []))
            token = page.get("ContinuationToken")
            if not token:
                break
        else:
            raise InventoryError("list_buckets exceeded the page bound")
        record("s3_bucket", _matching(buckets, lambda item: str(item.get("Name") or "")))

    for label, operation in (
        ("apigatewayv2", api_gateway),
        ("cognito-idp", cognito),
        ("lambda", lambda_functions),
        ("dynamodb", dynamodb),
        ("stepfunctions", step_functions),
        ("scheduler", scheduler),
        ("logs", logs),
        ("cloudfront", cloudfront),
        ("iam", iam),
        ("s3", s3),
    ):
        checked(label, operation)

    status = (
        "INCONCLUSIVE"
        if errors
        else ("WORKLOAD_NON_ZERO" if findings else "WORKLOAD_ZERO")
    )
    return {
        "schema": "sales-inventory/1.1",
        "phase": "workload",
        "status": status,
        "checked_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "resource_prefix": RESOURCE_PREFIX,
        "findings": {
            kind: {"count": len(values), "identifier_hashes": values}
            for kind, values in sorted(findings.items())
        },
        "errors": errors,
        "expected_persistent_guardrails": expected_guardrails,
    }


def collect_guardrail_inventory(
    iam: Any, *, guardrail_prefix: str = DEFAULT_GUARDRAIL_PREFIX
) -> dict[str, Any]:
    expected_names = _guardrail_policy_names(guardrail_prefix)
    findings: dict[str, list[str]] = {}
    errors: list[dict[str, str]] = []
    try:
        policies = [
            item
            for page in _pages(iam, "list_policies", Scope="Local")
            for item in page.get("Policies", [])
        ]
        remaining = [
            policy
            for policy in policies
            if str(policy.get("PolicyName") or "") in expected_names
        ]
        if remaining:
            findings["guardrail_iam_policy"] = [
                _fingerprint(str(policy.get("Arn") or policy.get("PolicyName") or ""))
                for policy in remaining
            ]
        attached = [
            item
            for page in _pages(
                iam,
                "list_attached_role_policies",
                RoleName=DEPLOYMENT_ROLE_NAME,
            )
            for item in page.get("AttachedPolicies", [])
            if str(item.get("PolicyName") or "") in expected_names
        ]
        if attached:
            findings["guardrail_role_attachment"] = [
                _fingerprint(str(item.get("PolicyArn") or item.get("PolicyName") or ""))
                for item in attached
            ]
    except Exception as exc:
        errors.append({"collector": "guardrail_iam", "error_type": type(exc).__name__})
    status = (
        "INCONCLUSIVE"
        if errors
        else ("GUARDRAILS_NON_ZERO" if findings else "GUARDRAILS_ZERO")
    )
    return {
        "schema": "sales-inventory/1.1",
        "phase": "guardrails",
        "status": status,
        "checked_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "resource_prefix": RESOURCE_PREFIX,
        "findings": {
            kind: {"count": len(values), "identifier_hashes": sorted(values)}
            for kind, values in sorted(findings.items())
        },
        "errors": errors,
    }


def _write_private(path: Path, report: Mapping[str, Any]) -> None:
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise InventoryError("Inventory output path is unsafe")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, (json.dumps(report, sort_keys=True, indent=2) + "\n").encode())
    finally:
        os.close(descriptor)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--profile", required=True)
    value.add_argument("--expected-account-id", required=True)
    value.add_argument("--region", required=True)
    value.add_argument("--phase", choices=["workload", "guardrails"], required=True)
    value.add_argument("--guardrail-prefix", required=True)
    value.add_argument("--output", type=Path)
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        import boto3

        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        caller_hash = verify_caller(
            session.client("sts").get_caller_identity(), args.expected_account_id
        )
        if args.phase == "workload":
            clients = {
                name: session.client(name)
                for name in (
                    "apigatewayv2",
                    "cloudfront",
                    "cognito-idp",
                    "dynamodb",
                    "iam",
                    "lambda",
                    "logs",
                    "s3",
                    "scheduler",
                    "stepfunctions",
                )
            }
            report = collect_inventory(
                clients, guardrail_prefix=args.guardrail_prefix
            )
        else:
            report = collect_guardrail_inventory(
                session.client("iam"), guardrail_prefix=args.guardrail_prefix
            )
        report["assumed_role_hash"] = caller_hash
        report["region"] = args.region
        if args.output:
            _write_private(args.output, report)
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return {
            "WORKLOAD_ZERO": 0,
            "GUARDRAILS_ZERO": 0,
            "WORKLOAD_NON_ZERO": 1,
            "GUARDRAILS_NON_ZERO": 1,
            "INCONCLUSIVE": 2,
        }[report["status"]]
    except (InventoryError, OSError, ValueError) as exc:
        print(f"sales inventory: FAIL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
