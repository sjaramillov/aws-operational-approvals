#!/usr/bin/env python3
"""Run the live, fail-closed preflight for the isolated sales pilot.

The command is intentionally separate from Terraform.  It requires an AWS
profile that already resolves to ``APPROVALS-TerraformDeploymentRole`` and writes a
sanitized, mode-0600 contract for ``plan_guard.py``.  Account identifiers,
ARNs, email addresses and raw AWS responses are used in memory only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

import inventory_zero
import publish_frontend


SCHEMA = "sales-live-preflight/1.0"
DEPLOYMENT_ROLE_NAME = "APPROVALS-TerraformDeploymentRole"
BOUNDARY_NAME = "approvals-sales-demo-application-boundary"
EVIDENCE_TTL_MINUTES = 15
MINIMUM_PLAN_COVERAGE_HOURS = 193
LAMBDA_CONCURRENCY_QUOTA = 10
IAM_POLICY_LIMIT = 6_144
MAX_PAGES = 1_000
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ACCOUNT_ID_RE = re.compile(r"^[0-9]{12}$")
REGION_RE = re.compile(r"^[a-z]{2}(?:-[a-z0-9]+)+-[0-9]+$")
PREFIX_RE = re.compile(r"^[a-z][a-z0-9-]{2,28}$")
EXPECTED_COST_TYPES = {
    "IncludeCredit": True,
    "IncludeDiscount": True,
    "IncludeOtherSubscription": True,
    "IncludeRecurring": True,
    "IncludeRefund": True,
    "IncludeSubscription": True,
    "IncludeSupport": True,
    "IncludeTax": True,
    "IncludeUpfront": True,
    "UseAmortized": False,
    "UseBlended": False,
}


class SalesPreflightError(RuntimeError):
    """The live account is outside the approved sales-demo contract."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256_text(_canonical_json(value))


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SalesPreflightError(f"{label} response is malformed")
    return value


def _pages(client: Any, operation: str, **kwargs: Any) -> Iterable[Mapping[str, Any]]:
    can_paginate = getattr(client, "can_paginate", None)
    if not callable(can_paginate) or can_paginate(operation):
        paginator = client.get_paginator(operation)
        for index, page in enumerate(paginator.paginate(**kwargs), start=1):
            if index > MAX_PAGES:
                raise SalesPreflightError(f"{operation} exceeded the bounded page count")
            yield _mapping(page, operation)
        return

    # Botocore intentionally has no paginator model for these two list APIs.
    # Keep their native continuation contracts bounded and fail closed.
    request = dict(kwargs)
    previous_token: str | None = None
    for _index in range(1, MAX_PAGES + 1):
        method = getattr(client, operation)
        page = _mapping(method(**request), operation)
        yield page
        if operation == "list_delivery_streams":
            if page.get("HasMoreDeliveryStreams") is not True:
                return
            names = page.get("DeliveryStreamNames")
            token = str(names[-1]) if isinstance(names, list) and names else ""
            request["ExclusiveStartDeliveryStreamName"] = token
        elif operation == "search_agreements":
            token = str(page.get("nextToken") or "")
            if not token:
                return
            request["nextToken"] = token
        else:
            raise SalesPreflightError(f"{operation} has no approved pagination contract")
        if not token or token == previous_token:
            raise SalesPreflightError(f"{operation} returned an invalid continuation token")
        previous_token = token
    raise SalesPreflightError(f"{operation} exceeded the bounded page count")


def _decimal_string(value: Any, label: str, *, positive: bool = False) -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SalesPreflightError(f"{label} is not numeric") from exc
    if not amount.is_finite() or amount < 0 or (positive and amount <= 0):
        raise SalesPreflightError(f"{label} is outside the approved range")
    return format(amount.normalize(), "f") if amount else "0"


def _utc(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float, Decimal)):
        parsed = datetime.fromtimestamp(float(value), tz=UTC)
    elif isinstance(value, str):
        encoded = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(encoded)
        except ValueError as exc:
            raise SalesPreflightError(f"{label} is not valid ISO-8601") from exc
    else:
        raise SalesPreflightError(f"{label} is not a supported timestamp")
    if parsed.tzinfo is None:
        raise SalesPreflightError(f"{label} lacks a timezone")
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_prior_evidence(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise SalesPreflightError("Prior sanitized runtime evidence is unavailable")
    try:
        payload = path.read_bytes()
        value = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SalesPreflightError("Prior sanitized runtime evidence is invalid") from exc
    if not isinstance(value, dict):
        raise SalesPreflightError("Prior runtime evidence root is malformed")
    cost = _mapping(value.get("cost_guard"), "Prior cost guard")
    teardown = _mapping(value.get("teardown"), "Prior teardown")
    if (
        value.get("deployment_profile") != "emergency_only"
        or cost.get("kinesis_resources") != 0
        or cost.get("telemetry_resources") != 0
        or teardown.get("completed") is not True
        or teardown.get("terraform_workload_state_empty") is not True
        or teardown.get("residual_workload_resources") != 0
    ):
        raise SalesPreflightError(
            "Prior evidence does not prove emergency_only teardown with zero telemetry"
        )
    return {
        "deployment_profile": "emergency_only",
        "telemetry_resources": 0,
        "kinesis_resources": 0,
        "teardown_complete": True,
        "residual_workload_resources": 0,
        "evidence_sha256": hashlib.sha256(payload).hexdigest(),
    }


def verify_caller(identity: Mapping[str, Any], account_id: str) -> dict[str, Any]:
    arn = str(identity.get("Arn") or "")
    if (
        identity.get("Account") != account_id
        or f":assumed-role/{DEPLOYMENT_ROLE_NAME}/" not in arn
    ):
        raise SalesPreflightError("STS identity is not the approved assumed deployment role")
    return {
        "assumed_role": DEPLOYMENT_ROLE_NAME,
        "session_hash": _sha256_text(arn),
    }


def verify_source_caller(identity: Mapping[str, Any], account_id: str) -> dict[str, Any]:
    arn = str(identity.get("Arn") or "")
    if identity.get("Account") != account_id or not arn.endswith(":user/approvals-local-login"):
        raise SalesPreflightError("Source profile is not the exact approvals-local-login IAM user")
    return {"user_name_hash": _sha256_text("approvals-local-login")}


def validate_human_credential_posture(iam: Any) -> dict[str, Any]:
    access_keys = [
        item
        for page in _pages(iam, "list_access_keys", UserName="approvals-local-login")
        for item in page.get("AccessKeyMetadata", [])
    ]
    mfa_devices = [
        item
        for page in _pages(iam, "list_mfa_devices", UserName="approvals-local-login")
        for item in page.get("MFADevices", [])
    ]
    if access_keys or not mfa_devices:
        raise SalesPreflightError(
            "Human deployment identity must have MFA and zero permanent access keys"
        )
    return {
        "user_name_hash": _sha256_text("approvals-local-login"),
        "permanent_access_key_count": 0,
        "mfa_device_count": len(mfa_devices),
    }


def validate_account(
    information: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    account_id: str,
    now: datetime,
) -> dict[str, Any]:
    if information.get("AccountId") != account_id or information.get("AccountState") != "ACTIVE":
        raise SalesPreflightError("AWS account is not the approved ACTIVE account")
    if (
        plan.get("accountId") != account_id
        or plan.get("accountPlanType") != "FREE"
        or plan.get("accountPlanStatus") != "ACTIVE"
    ):
        raise SalesPreflightError("AWS account plan is not FREE/ACTIVE")
    credits = _mapping(plan.get("accountPlanRemainingCredits"), "Remaining credits")
    if credits.get("unit") != "USD":
        raise SalesPreflightError("Remaining credits are not expressed in USD")
    amount = _decimal_string(credits.get("amount"), "Remaining credits", positive=True)
    expiration = _utc(plan.get("accountPlanExpirationDate"), "Account-plan expiration")
    if expiration <= now + timedelta(hours=MINIMUM_PLAN_COVERAGE_HOURS):
        raise SalesPreflightError("FREE plan does not cover the full pilot and teardown window")
    return {
        "state": "ACTIVE",
        "plan_type": "FREE",
        "plan_status": "ACTIVE",
        "remaining_credits_usd": amount,
        "credits_are_billing_statement": False,
        "plan_expiration": _iso(expiration),
        "minimum_coverage_hours": MINIMUM_PLAN_COVERAGE_HOURS,
    }


def validate_budget(
    budget_response: Mapping[str, Any],
    notifications_response: Mapping[str, Any],
    subscribers: Mapping[str, Mapping[str, Any]],
    *,
    account_id: str,
    budget_name: str,
    email_sha256: str,
) -> dict[str, Any]:
    budget = _mapping(budget_response.get("Budget"), "Budget")
    limit = _mapping(budget.get("BudgetLimit"), "Budget limit")
    cost_types = budget.get("CostTypes")
    if cost_types is None:
        cost_types = EXPECTED_COST_TYPES
    if (
        budget_response.get("AccountId", account_id) != account_id
        or budget.get("BudgetName") != budget_name
        or budget.get("BudgetType") != "COST"
        or budget.get("TimeUnit") != "MONTHLY"
        or limit.get("Unit") != "USD"
        or _decimal_string(limit.get("Amount"), "Budget limit", positive=True) != "20"
        or budget.get("CostFilters") not in (None, {})
        or budget.get("FilterExpression") not in (None, {})
        or budget.get("Metrics") != ["UnblendedCost"]
        or cost_types != EXPECTED_COST_TYPES
        or budget.get("BillingViewArn") not in (None, "")
        or budget.get("AutoAdjustData") not in (None, {})
        or budget.get("PlannedBudgetLimits") not in (None, {})
    ):
        raise SalesPreflightError("AWS Budget differs from the approved USD 20 contract")
    notifications = notifications_response.get("Notifications")
    if not isinstance(notifications, list):
        raise SalesPreflightError("Budget notification inventory is malformed")
    expected = {("ACTUAL", 50), ("FORECASTED", 80)}
    observed = {
        (item.get("NotificationType"), item.get("Threshold"))
        for item in notifications
        if isinstance(item, Mapping)
        and item.get("ComparisonOperator") == "GREATER_THAN"
        and item.get("ThresholdType", "PERCENTAGE") == "PERCENTAGE"
    }
    if len(notifications) != 2 or observed != expected:
        raise SalesPreflightError("Budget notifications are not exactly ACTUAL 50 / FORECASTED 80")
    for notification_type, _ in expected:
        response = subscribers.get(notification_type)
        values = response.get("Subscribers") if isinstance(response, Mapping) else None
        if (
            not isinstance(values, list)
            or len(values) != 1
            or values[0].get("SubscriptionType") != "EMAIL"
            or not isinstance(values[0].get("Address"), str)
            or _sha256_text(values[0]["Address"].strip().lower()) != email_sha256
        ):
            raise SalesPreflightError("Budget subscriber differs from the approved email hash")
    return {
        "name_hash": _sha256_text(budget_name),
        "limit_usd": 20,
        "hard_limit": False,
        "account_wide": True,
        "notifications_exact": True,
        "subscriber_email_sha256": email_sha256,
    }


def _policy_document(iam: Any, policy_arn: str) -> tuple[dict[str, Any], int]:
    policy = _mapping(iam.get_policy(PolicyArn=policy_arn).get("Policy"), "IAM policy")
    version_id = policy.get("DefaultVersionId")
    if not isinstance(version_id, str) or re.fullmatch(r"v[1-9][0-9]*", version_id) is None:
        raise SalesPreflightError("IAM policy has no valid default version")
    version = _mapping(
        iam.get_policy_version(PolicyArn=policy_arn, VersionId=version_id).get("PolicyVersion"),
        "IAM policy version",
    )
    document = version.get("Document")
    if version.get("VersionId") != version_id or version.get("IsDefaultVersion") is not True:
        raise SalesPreflightError("IAM policy default version is not active")
    if not isinstance(document, dict):
        raise SalesPreflightError("IAM policy document is malformed")
    length = len(_canonical_json(document))
    if length >= IAM_POLICY_LIMIT:
        raise SalesPreflightError("IAM managed policy reaches the 6,144-character limit")
    return document, length


def validate_role_contract(
    iam: Any,
    *,
    account_id: str,
    guardrail_prefix: str,
    expected_contract_sha256: str,
    expected_boundary_sha256: str,
) -> dict[str, Any]:
    role = _mapping(iam.get_role(RoleName=DEPLOYMENT_ROLE_NAME).get("Role"), "Deployment role")
    if (
        role.get("RoleName") != DEPLOYMENT_ROLE_NAME
        or role.get("MaxSessionDuration") != 3_600
        or role.get("PermissionsBoundary") is not None
        or not str(role.get("Arn") or "").endswith(f":role/{DEPLOYMENT_ROLE_NAME}")
    ):
        raise SalesPreflightError("Deployment role metadata differs from the approved contract")
    inline = [
        name
        for page in _pages(iam, "list_role_policies", RoleName=DEPLOYMENT_ROLE_NAME)
        for name in page.get("PolicyNames", [])
    ]
    if inline:
        raise SalesPreflightError("Deployment role contains inline policies")
    attached = [
        item
        for page in _pages(iam, "list_attached_role_policies", RoleName=DEPLOYMENT_ROLE_NAME)
        for item in page.get("AttachedPolicies", [])
    ]
    names = (
        f"{guardrail_prefix}-terraform-compute-data",
        f"{guardrail_prefix}-terraform-iam-management",
        f"{guardrail_prefix}-terraform-integrations",
        f"{guardrail_prefix}-terraform-kms-use",
        f"{guardrail_prefix}-terraform-read-and-global",
        f"{guardrail_prefix}-terraform-sales-demo",
        f"{guardrail_prefix}-terraform-sales-demo-edge",
    )
    by_name = {str(item.get("PolicyName") or ""): str(item.get("PolicyArn") or "") for item in attached}
    if len(attached) != len(by_name) or set(by_name) != set(names):
        raise SalesPreflightError("Deployment role policy attachments are not the exact sales set")
    partition = str(role.get("Arn")).split(":", 2)[1]
    documents: list[dict[str, Any]] = []
    character_counts: dict[str, int] = {}
    for name in names:
        expected_arn = f"arn:{partition}:iam::{account_id}:policy/{name}"
        if by_name[name] != expected_arn:
            raise SalesPreflightError("Deployment role policy belongs to an unexpected account")
        document, length = _policy_document(iam, expected_arn)
        documents.append({"arn": expected_arn, "document": document})
        character_counts[_sha256_text(name)[:16]] = length
    contract = {
        "role": {
            "arn": role["Arn"],
            "name": DEPLOYMENT_ROLE_NAME,
            "max_session_duration": 3_600,
            "assume_role_policy": role.get("AssumeRolePolicyDocument"),
            "permissions_boundary_arn": None,
        },
        "managed_policies": documents,
    }
    contract_sha256 = _canonical_sha256(contract)
    if contract_sha256 != expected_contract_sha256:
        raise SalesPreflightError("Deployment role contract hash differs from guardrails")

    boundary_arn = f"arn:{partition}:iam::{account_id}:policy/{BOUNDARY_NAME}"
    boundary, boundary_length = _policy_document(iam, boundary_arn)
    if _canonical_sha256(boundary) != expected_boundary_sha256:
        raise SalesPreflightError("Sales permissions-boundary hash differs from guardrails")
    return {
        "role": DEPLOYMENT_ROLE_NAME,
        "inline_policy_count": 0,
        "attached_policy_count": len(names),
        "exact_sales_policy_set": True,
        "contract_sha256": contract_sha256,
        "boundary_sha256": expected_boundary_sha256,
        "boundary_character_count": boundary_length,
        "managed_policy_character_counts": character_counts,
    }


def validate_lambda(settings: Mapping[str, Any]) -> dict[str, Any]:
    limits = _mapping(settings.get("AccountLimit"), "Lambda account limits")
    concurrent = limits.get("ConcurrentExecutions")
    unreserved = limits.get("UnreservedConcurrentExecutions")
    if concurrent != LAMBDA_CONCURRENCY_QUOTA or not isinstance(unreserved, int) or unreserved < 2:
        raise SalesPreflightError("Lambda quota/headroom differs from the quota-10 pilot contract")
    return {
        "concurrent_executions": concurrent,
        "unreserved_concurrent_executions": unreserved,
        "reserved_concurrency_used_by_sales": 0,
    }


def validate_live_absence(clients: Mapping[str, Any]) -> dict[str, Any]:
    try:
        streams = [
            name
            for page in _pages(clients["kinesis"], "list_streams")
            for name in page.get("StreamNames", [])
        ]
        kinesis_signal = "LIST_API_AVAILABLE"
    except Exception as exc:
        if _aws_error_code(exc) != "SubscriptionRequiredException":
            raise
        streams = []
        kinesis_signal = "SUBSCRIPTION_REQUIRED"
    try:
        delivery = [
            name
            for page in _pages(clients["firehose"], "list_delivery_streams")
            for name in page.get("DeliveryStreamNames", [])
        ]
        firehose_signal = "LIST_API_AVAILABLE"
    except Exception as exc:
        if _aws_error_code(exc) != "SubscriptionRequiredException":
            raise
        # A service that rejects the account as unsubscribed cannot hold an
        # account-owned delivery stream. Persist the signal instead of silently
        # presenting the result as an ordinary empty list.
        delivery = []
        firehose_signal = "SUBSCRIPTION_REQUIRED"
    rules = [
        str(item.get("ruleName") or "")
        for page in _pages(clients["iot"], "list_topic_rules")
        for item in page.get("rules", [])
    ]
    tagged = [
        str(item.get("ResourceARN") or "")
        for page in _pages(
            clients["resourcegroupstaggingapi"],
            "get_resources",
            TagFilters=[{
                "Key": "DeploymentProfile",
                "Values": ["target", "mvp", "emergency_only"],
            }],
            ResourcesPerPage=100,
        )
        for item in page.get("ResourceTagMappingList", [])
    ]
    prior_rules = [name for name in rules if name.startswith("approvals_central_demo_")]
    telemetry_rules = [name for name in rules if "telemetry" in name.lower()]
    if streams or delivery or tagged or prior_rules or telemetry_rules:
        raise SalesPreflightError("Prior APPROVALS/Kinesis/telemetry live inventory is not zero")
    return {
        "tagged_prior_approvals_resources": 0,
        "kinesis_streams": 0,
        "kinesis_inventory_signal": kinesis_signal,
        "firehose_delivery_streams": 0,
        "firehose_inventory_signal": firehose_signal,
        "prior_approvals_topic_rules": 0,
        "telemetry_topic_rules": 0,
    }


def _aws_error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if not isinstance(response, Mapping):
        return ""
    error = response.get("Error")
    return str(error.get("Code") or "") if isinstance(error, Mapping) else ""


def validate_organization(client: Any) -> dict[str, Any]:
    try:
        response = client.describe_organization()
    except Exception as exc:
        if _aws_error_code(exc) == "AWSOrganizationsNotInUseException":
            return {"organization_in_use": False}
        raise SalesPreflightError("Organizations state could not be verified") from exc
    if response.get("Organization"):
        raise SalesPreflightError("AWS Organizations is in use")
    raise SalesPreflightError("Organizations response is inconclusive")


def validate_marketplace(client: Any) -> dict[str, Any]:
    summaries = [
        item
        for page in _pages(
            client,
            "search_agreements",
            catalog="AWSMarketplace",
            filters=[
                {"name": "PartyType", "values": ["Acceptor"]},
                {"name": "AgreementType", "values": ["PurchaseAgreement"]},
                {"name": "Status", "values": ["ACTIVE"]},
            ],
            maxResults=50,
        )
        for item in page.get("agreementViewSummaries", [])
    ]
    if summaries:
        raise SalesPreflightError("AWS Marketplace has active purchase agreements")
    return {"active_purchase_agreement_count": 0}


def validate_support_api(client: Any) -> dict[str, Any]:
    try:
        client.describe_services(language="en")
    except Exception as exc:
        if _aws_error_code(exc) == "SubscriptionRequiredException":
            return {
                "business_enterprise_api_entitlement": False,
                "signal": "SubscriptionRequiredException",
                "developer_support_excluded_by_signal": False,
            }
        raise SalesPreflightError("AWS Support API entitlement is inconclusive") from exc
    raise SalesPreflightError("AWS Support API indicates a paid Business/Enterprise entitlement")


def validate_commercial_hard_stops(clients: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "organizations": validate_organization(clients["organizations"]),
        "marketplace": validate_marketplace(clients["marketplace-agreement"]),
        "support": validate_support_api(clients["support"]),
    }


def _notification_key(item: Mapping[str, Any]) -> tuple[str, int]:
    return str(item.get("NotificationType")), int(item.get("Threshold"))


def collect_live(args: argparse.Namespace, *, now: datetime | None = None) -> dict[str, Any]:
    current = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    publish_frontend.verify_source_revision(args.source_revision)
    prior = validate_prior_evidence(args.prior_runtime_evidence)

    import boto3  # imported only for the explicit live mode

    source_session = boto3.Session(profile_name=args.source_profile, region_name=args.region)
    source_identity = source_session.client("sts").get_caller_identity()
    source_caller = verify_source_caller(source_identity, args.expected_account_id)
    source_clients = {
        name: source_session.client(name)
        for name in ("iam", "marketplace-agreement", "organizations", "support")
    }
    credential_posture = validate_human_credential_posture(source_clients["iam"])
    commercial_hard_stops = validate_commercial_hard_stops(source_clients)

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    sts = session.client("sts")
    identity = sts.get_caller_identity()
    caller = verify_caller(identity, args.expected_account_id)
    clients = {
        name: session.client(name)
        for name in (
            "account",
            "apigatewayv2",
            "budgets",
            "cloudfront",
            "cognito-idp",
            "dynamodb",
            "firehose",
            "freetier",
            "iam",
            "iot",
            "kinesis",
            "lambda",
            "logs",
            "resourcegroupstaggingapi",
            "s3",
            "scheduler",
            "stepfunctions",
        )
    }
    account = validate_account(
        clients["account"].get_account_information(),
        clients["freetier"].get_account_plan_state(),
        account_id=args.expected_account_id,
        now=current,
    )
    budget_response = clients["budgets"].describe_budget(
        AccountId=args.expected_account_id,
        BudgetName=args.budget_name,
        ShowFilterExpression=True,
    )
    notifications = clients["budgets"].describe_notifications_for_budget(
        AccountId=args.expected_account_id,
        BudgetName=args.budget_name,
    )
    notification_values = notifications.get("Notifications") or []
    subscriber_results: dict[str, Mapping[str, Any]] = {}
    for item in notification_values:
        if not isinstance(item, Mapping):
            raise SalesPreflightError("Budget notification inventory is malformed")
        notification_type, _ = _notification_key(item)
        subscriber_results[notification_type] = clients[
            "budgets"
        ].describe_subscribers_for_notification(
            AccountId=args.expected_account_id,
            BudgetName=args.budget_name,
            Notification=dict(item),
        )
    budget = validate_budget(
        budget_response,
        notifications,
        subscriber_results,
        account_id=args.expected_account_id,
        budget_name=args.budget_name,
        email_sha256=args.budget_email_sha256,
    )
    role = validate_role_contract(
        clients["iam"],
        account_id=args.expected_account_id,
        guardrail_prefix=args.guardrail_prefix,
        expected_contract_sha256=args.expected_role_contract_sha256,
        expected_boundary_sha256=args.expected_boundary_sha256,
    )
    lambdas = validate_lambda(clients["lambda"].get_account_settings())
    absence = validate_live_absence(clients)
    workload_clients = {
        name: clients[name]
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
    inventory = inventory_zero.collect_inventory(
        workload_clients, guardrail_prefix=args.guardrail_prefix
    )
    expected_guardrails = inventory.get("expected_persistent_guardrails") or {}
    if (
        inventory.get("status") != "WORKLOAD_ZERO"
        or expected_guardrails.get("expected_count") != 3
        or expected_guardrails.get("present_count") != 3
    ):
        raise SalesPreflightError("Sales workload inventory is not provably zero")

    return {
        "schema": SCHEMA,
        "captured_at": _iso(current),
        "expires_at": _iso(current + timedelta(minutes=EVIDENCE_TTL_MINUTES)),
        "source_revision": args.source_revision,
        "aws_region": args.region,
        "deployment_profile": "sales_demo",
        "account_id_hash": _sha256_text(args.expected_account_id),
        "identity": caller,
        "source_identity": source_caller,
        "human_credential_posture": credential_posture,
        "account": account,
        "budget": budget,
        "guardrails": role,
        "lambda": lambdas,
        "prior_emergency": prior,
        "live_absence": absence,
        "commercial_hard_stops": commercial_hard_stops,
        "sales_workload": {
            "status": "WORKLOAD_ZERO",
            "finding_count": 0,
            "collector_error_count": 0,
            "persistent_guardrail_count": 3,
        },
    }


def assert_sanitized(report: Mapping[str, Any], *, account_id: str) -> bytes:
    encoded = (_canonical_json(report) + "\n").encode("utf-8")
    lowered = encoded.lower()
    if account_id.encode() in encoded or b"arn:" in lowered or b"@" in encoded:
        raise SalesPreflightError("Sanitized preflight contains a forbidden identifier")
    return encoded


def write_private(path: Path, payload: bytes) -> None:
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise SalesPreflightError("Preflight output path is unsafe")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--source-profile", required=True)
    value.add_argument("--profile", required=True)
    value.add_argument("--expected-account-id", required=True)
    value.add_argument("--region", required=True)
    value.add_argument("--source-revision", required=True)
    value.add_argument("--expected-role-contract-sha256", required=True)
    value.add_argument("--expected-boundary-sha256", required=True)
    value.add_argument("--budget-name", required=True)
    value.add_argument("--budget-email-sha256", required=True)
    value.add_argument("--guardrail-prefix", required=True)
    value.add_argument("--prior-runtime-evidence", type=Path, required=True)
    value.add_argument("--output", type=Path, required=True)
    return value


def _validate_args(args: argparse.Namespace) -> None:
    if ACCOUNT_ID_RE.fullmatch(args.expected_account_id) is None:
        raise SalesPreflightError("Expected account ID is invalid")
    if REGION_RE.fullmatch(args.region) is None:
        raise SalesPreflightError("AWS region is invalid")
    if PREFIX_RE.fullmatch(args.guardrail_prefix) is None:
        raise SalesPreflightError("Guardrail prefix is invalid")
    if re.fullmatch(r"[0-9a-f]{40}", args.source_revision) is None:
        raise SalesPreflightError("Source revision is invalid")
    for value in (
        args.expected_role_contract_sha256,
        args.expected_boundary_sha256,
        args.budget_email_sha256,
    ):
        if SHA256_RE.fullmatch(value) is None:
            raise SalesPreflightError("A required SHA-256 input is invalid")
    if not args.budget_name.strip():
        raise SalesPreflightError("Budget name is required")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        _validate_args(args)
        report = collect_live(args)
        payload = assert_sanitized(report, account_id=args.expected_account_id)
        write_private(args.output.resolve(), payload)
    except (SalesPreflightError, OSError, KeyboardInterrupt) as exc:
        message = "interrupted" if isinstance(exc, KeyboardInterrupt) else str(exc)
        print(f"sales live preflight: FAIL: {message}", file=sys.stderr)
        return 1
    except Exception as exc:  # boto errors are intentionally reduced to their type.
        print(
            f"sales live preflight: FAIL: AWS operation {type(exc).__name__}",
            file=sys.stderr,
        )
        return 1
    print(
        _canonical_json({
            "status": "PASS",
            "schema": SCHEMA,
            "output_sha256": hashlib.sha256(payload).hexdigest(),
        })
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
