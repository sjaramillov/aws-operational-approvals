#!/usr/bin/env python3
"""Activate the sales pilot only after infrastructure readiness is proven.

Terraform deliberately leaves the pilot ``PREPARED`` with no T0. This tool
binds the public runtime config to live AWS targets, publishes and verifies the
reviewed bundle, arms the redundant expiry schedule, and performs the sole
``PREPARED -> ACTIVE`` conditional write as the final commit. It never stores
credentials or emits account IDs/ARNs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

import provision_users
import publish_frontend


ACTIVE_WINDOW_HOURS = 192
MIN_ACTIVATION_DELAY_SECONDS = 5
MAX_ACTIVATION_DELAY_SECONDS = 60
ACCOUNT_ID_RE = re.compile(r"^[0-9]{12}$")


class ActivationError(RuntimeError):
    """Raised when ACTIVE cannot be committed without weakening a gate."""


class SafeRollbackActivationError(ActivationError):
    """The durable pilot is still PREPARED, so disabling the schedule is safe."""


class AmbiguousActivationError(ActivationError):
    """ACTIVE may be durable; the expiry schedule must remain enabled."""


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _iso_z(value: dt.datetime) -> str:
    normalized = value.astimezone(dt.timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def activation_times(
    now: dt.datetime, delay_seconds: int
) -> tuple[dt.datetime, dt.datetime]:
    if not MIN_ACTIVATION_DELAY_SECONDS <= delay_seconds <= MAX_ACTIVATION_DELAY_SECONDS:
        raise ActivationError("Activation delay must remain between 5 and 60 seconds")
    if now.tzinfo is None:
        raise ActivationError("Activation clock must be timezone-aware")
    t0 = now.astimezone(dt.timezone.utc).replace(microsecond=0) + dt.timedelta(
        seconds=delay_seconds
    )
    return t0, t0 + dt.timedelta(hours=ACTIVE_WINDOW_HOURS)


def require_exact_t0(t0: dt.datetime, observed: dt.datetime) -> dt.datetime:
    normalized = observed.astimezone(dt.timezone.utc).replace(microsecond=0)
    if normalized != t0:
        raise SafeRollbackActivationError(
            "Activation missed exact T0; no shortened eight-day window is accepted"
        )
    return normalized


def require_exact_expiry(t0: dt.datetime, expires_at: dt.datetime) -> dt.datetime:
    if t0.tzinfo is None or expires_at.tzinfo is None:
        raise SafeRollbackActivationError("Activation timestamps must be timezone-aware")
    normalized_t0 = t0.astimezone(dt.timezone.utc).replace(microsecond=0)
    normalized_expiry = expires_at.astimezone(dt.timezone.utc).replace(microsecond=0)
    if normalized_expiry != normalized_t0 + dt.timedelta(hours=ACTIVE_WINDOW_HOURS):
        raise SafeRollbackActivationError(
            "Expiry must remain exactly 192 hours after T0"
        )
    return normalized_expiry


def verify_prepared(dynamodb: Any, *, table_name: str, pilot_id: str) -> None:
    response = dynamodb.get_item(
        TableName=table_name,
        Key={"pk": {"S": f"PILOT#{pilot_id}"}, "sk": {"S": "META"}},
        ConsistentRead=True,
    )
    item = response.get("Item") or {}
    if item.get("status") != {"S": "PREPARED"} or item.get(
        "expires_at_epoch"
    ) != {"N": "0"}:
        raise ActivationError("Pilot seed is not exactly PREPARED with expiry zero")


def verify_schedule_target(
    schedule: dict[str, Any], *, worker_arn: str, scheduler_role_arn: str
) -> dict[str, Any]:
    target = schedule.get("Target") or {}
    if target.get("Arn") != worker_arn or target.get("RoleArn") != scheduler_role_arn:
        raise ActivationError("Expiry schedule target does not match the Terraform contract")
    retry = target.get("RetryPolicy") or {}
    if retry != {"MaximumEventAgeInSeconds": 3600, "MaximumRetryAttempts": 2}:
        raise ActivationError("Expiry schedule retry policy has drifted")
    return target


def _update_schedule(
    scheduler: Any,
    *,
    schedule_name: str,
    target: dict[str, Any],
    expression: str,
    payload: dict[str, Any],
    state: str,
) -> None:
    updated_target = {
        "Arn": target["Arn"],
        "RoleArn": target["RoleArn"],
        "RetryPolicy": target["RetryPolicy"],
        "Input": json.dumps(payload, sort_keys=True, separators=(",", ":")),
    }
    scheduler.update_schedule(
        Name=schedule_name,
        ScheduleExpression=expression,
        ScheduleExpressionTimezone="UTC",
        FlexibleTimeWindow={"Mode": "OFF"},
        Target=updated_target,
        State=state,
        ActionAfterCompletion="DELETE",
    )


def arm_schedule(
    scheduler: Any,
    *,
    schedule_name: str,
    worker_arn: str,
    scheduler_role_arn: str,
    expires_at: dt.datetime,
) -> dict[str, Any]:
    current = scheduler.get_schedule(Name=schedule_name)
    target = verify_schedule_target(
        current,
        worker_arn=worker_arn,
        scheduler_role_arn=scheduler_role_arn,
    )
    expires_iso = _iso_z(expires_at)
    payload = {
        "operation": "EXPIRE_PILOT",
        "scheduled_expires_at": expires_iso,
        "expected_user_count": 4,
    }
    _update_schedule(
        scheduler,
        schedule_name=schedule_name,
        target=target,
        expression=f"at({expires_iso[:-1]})",
        payload=payload,
        state="ENABLED",
    )
    return target


def rollback_schedule(
    scheduler: Any,
    *,
    schedule_name: str,
    target: dict[str, Any],
    expires_at: dt.datetime,
) -> None:
    expires_iso = _iso_z(expires_at)
    _update_schedule(
        scheduler,
        schedule_name=schedule_name,
        target=target,
        expression=f"at({expires_iso[:-1]})",
        payload={
            "operation": "EXPIRE_PILOT",
            "scheduled_expires_at": expires_iso,
            "expected_user_count": 4,
        },
        state="DISABLED",
    )


def commit_active(
    dynamodb: Any,
    *,
    table_name: str,
    pilot_id: str,
    t0: dt.datetime,
    expires_at: dt.datetime,
    activated_at: dt.datetime,
) -> None:
    # Keep the exact eight-day contract true even when this helper is called
    # directly.  The orchestrator performs the same check immediately before
    # invoking us, but the durable commit must defend its own invariant.
    activated_at = require_exact_t0(t0, activated_at)
    expires_at = require_exact_expiry(t0, expires_at)
    t0_iso = _iso_z(t0)
    expires_iso = _iso_z(expires_at)
    activated_iso = _iso_z(activated_at)
    expires_epoch = int(expires_at.timestamp())
    key = {"pk": {"S": f"PILOT#{pilot_id}"}, "sk": {"S": "META"}}
    request = {
        "TableName": table_name,
        "Key": key,
        "UpdateExpression": (
            "SET #status = :active, t0 = :t0, activated_at = :activated, "
            "expires_at = :expires_iso, expires_at_epoch = :expires_epoch"
        ),
        "ConditionExpression": "#status = :prepared AND expires_at_epoch = :zero",
        "ExpressionAttributeNames": {"#status": "status"},
        "ExpressionAttributeValues": {
            ":active": {"S": "ACTIVE"},
            ":prepared": {"S": "PREPARED"},
            ":zero": {"N": "0"},
            ":t0": {"S": t0_iso},
            ":activated": {"S": activated_iso},
            ":expires_iso": {"S": expires_iso},
            ":expires_epoch": {"N": str(expires_epoch)},
        },
        "ReturnValues": "NONE",
    }
    try:
        dynamodb.update_item(**request)
    except Exception:
        # The network result can be ambiguous. Classification comes solely
        # from the durable, strongly consistent item below.
        pass
    try:
        item = dynamodb.get_item(
            TableName=table_name,
            Key=key,
            ConsistentRead=True,
        ).get("Item") or {}
    except Exception as exc:
        raise AmbiguousActivationError(
            "ACTIVE write is ambiguous and cannot be read; keep expiry armed"
        ) from exc
    expected = {
        "status": {"S": "ACTIVE"},
        "t0": {"S": t0_iso},
        "activated_at": {"S": activated_iso},
        "expires_at": {"S": expires_iso},
        "expires_at_epoch": {"N": str(expires_epoch)},
    }
    if all(item.get(name) == value for name, value in expected.items()):
        return
    if item.get("status") == {"S": "PREPARED"} and item.get(
        "expires_at_epoch"
    ) == {"N": "0"}:
        raise SafeRollbackActivationError(
            "ACTIVE write did not occur; durable state remains PREPARED"
        )
    raise AmbiguousActivationError(
        "ACTIVE write has an unexpected durable result; keep expiry armed"
    )


def verify_runtime_object(s3: Any, *, bucket: str, expected: bytes) -> None:
    head = s3.head_object(Bucket=bucket, Key="runtime-config.json")
    if head.get("CacheControl") != "no-store" or (
        head.get("Metadata") or {}
    ).get("sha256") != hashlib.sha256(expected).hexdigest():
        raise ActivationError("runtime-config.json metadata is not exact/no-store")
    body = s3.get_object(Bucket=bucket, Key="runtime-config.json")["Body"].read()
    if body != expected:
        raise ActivationError("runtime-config.json bytes do not match activation")


def _parse_iso_z(value: Any) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ActivationError("Activation timestamp is unavailable")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ActivationError("Activation timestamp is malformed") from exc
    return parsed.astimezone(dt.timezone.utc).replace(microsecond=0)


def _read_runtime_for_reconciliation(
    s3: Any,
    *,
    bucket: str,
    region: str,
    source_revision: str,
    release_sha256: str,
) -> dict[str, Any]:
    try:
        head = s3.head_object(Bucket=bucket, Key="runtime-config.json")
        body = s3.get_object(Bucket=bucket, Key="runtime-config.json")["Body"].read(65_537)
    except Exception as exc:
        raise ActivationError("Runtime config cannot be read for reconciliation") from exc
    if len(body) > 65_536 or head.get("CacheControl") != "no-store":
        raise ActivationError("Runtime config is oversized or cacheable")
    if (head.get("Metadata") or {}).get("sha256") != hashlib.sha256(body).hexdigest():
        raise ActivationError("Runtime config metadata hash is inconsistent")
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActivationError("Runtime config is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ActivationError("Runtime config root is invalid")
    publish_frontend.validate_runtime_config(value, region, allow_prepared=True)
    if value.get("deploymentBinding") != {
        "sourceRevision": source_revision,
        "frontendReleaseSha256": release_sha256,
    }:
        raise ActivationError("Runtime config is not bound to the reviewed candidate")
    return value


def reconcile_activation(
    dynamodb: Any,
    scheduler: Any,
    s3: Any,
    *,
    table_name: str,
    pilot_id: str,
    schedule_name: str,
    worker_arn: str,
    scheduler_role_arn: str,
    bucket: str,
    region: str,
    source_revision: str,
    release_sha256: str,
) -> dict[str, Any]:
    """Classify an ambiguous activation without mutating any AWS resource."""

    try:
        item = dynamodb.get_item(
            TableName=table_name,
            Key={"pk": {"S": f"PILOT#{pilot_id}"}, "sk": {"S": "META"}},
            ConsistentRead=True,
        ).get("Item") or {}
        schedule = scheduler.get_schedule(Name=schedule_name)
    except Exception as exc:
        raise ActivationError("Durable activation state cannot be read") from exc
    target = verify_schedule_target(
        schedule,
        worker_arn=worker_arn,
        scheduler_role_arn=scheduler_role_arn,
    )
    runtime = _read_runtime_for_reconciliation(
        s3,
        bucket=bucket,
        region=region,
        source_revision=source_revision,
        release_sha256=release_sha256,
    )
    schedule_state = schedule.get("State")
    runtime_state = "PREPARED" if runtime.get("expiresAt") is None else "ACTIVE_CONFIG"

    if item.get("status") == {"S": "PREPARED"} and item.get(
        "expires_at_epoch"
    ) == {"N": "0"}:
        if any(name in item for name in ("t0", "activated_at", "expires_at")):
            return {
                "status": "UNEXPECTED",
                "reason": "prepared_item_contains_activation_fields",
                "schedule_state": schedule_state,
                "runtime_state": runtime_state,
                "mutations_performed": 0,
            }
        if runtime_state != "PREPARED":
            action = "REPUBLISH_PREPARED_RUNTIME_THEN_DISABLE_SCHEDULE_AND_RETRY"
        elif schedule_state == "ENABLED":
            action = "DISABLE_SCHEDULE_THEN_RETRY"
        elif schedule_state == "DISABLED":
            action = "RETRY_ACTIVATION"
        else:
            return {
                "status": "UNEXPECTED",
                "reason": "prepared_schedule_state_invalid",
                "schedule_state": schedule_state,
                "runtime_state": runtime_state,
                "mutations_performed": 0,
            }
        return {
            "status": "PREPARED",
            "schedule_state": schedule_state,
            "runtime_state": runtime_state,
            "operator_action": action,
            "safe_to_disable_schedule": runtime_state == "PREPARED",
            "mutations_performed": 0,
        }

    if item.get("status") != {"S": "ACTIVE"}:
        return {
            "status": "UNEXPECTED",
            "reason": "durable_status_is_not_prepared_or_active",
            "schedule_state": schedule_state,
            "runtime_state": runtime_state,
            "mutations_performed": 0,
        }
    try:
        t0 = _parse_iso_z((item.get("t0") or {}).get("S"))
        activated = _parse_iso_z((item.get("activated_at") or {}).get("S"))
        expires_at = _parse_iso_z((item.get("expires_at") or {}).get("S"))
        payload = json.loads(target.get("Input") or "{}")
    except (ActivationError, json.JSONDecodeError):
        return {
            "status": "UNEXPECTED",
            "reason": "active_contract_is_malformed",
            "schedule_state": schedule_state,
            "runtime_state": runtime_state,
            "mutations_performed": 0,
        }
    expires_iso = _iso_z(expires_at)
    expected_payload = {
        "operation": "EXPIRE_PILOT",
        "scheduled_expires_at": expires_iso,
        "expected_user_count": 4,
    }
    exact = (
        activated == t0
        and expires_at == t0 + dt.timedelta(hours=ACTIVE_WINDOW_HOURS)
        and item.get("expires_at_epoch") == {"N": str(int(expires_at.timestamp()))}
        and runtime.get("expiresAt") == expires_iso
        and schedule_state == "ENABLED"
        and schedule.get("ScheduleExpression") == f"at({expires_iso[:-1]})"
        and schedule.get("ScheduleExpressionTimezone") == "UTC"
        and schedule.get("FlexibleTimeWindow") == {"Mode": "OFF"}
        and schedule.get("ActionAfterCompletion") == "DELETE"
        and payload == expected_payload
    )
    if not exact:
        return {
            "status": "UNEXPECTED",
            "reason": "active_runtime_or_expiry_contract_drifted",
            "schedule_state": schedule_state,
            "runtime_state": runtime_state,
            "mutations_performed": 0,
        }
    return {
        "status": "ACTIVE",
        "t0": _iso_z(t0),
        "expires_at": expires_iso,
        "schedule_state": schedule_state,
        "runtime_state": runtime_state,
        "operator_action": "CONTINUE_POST_ACTIVATION_E2E",
        "mutations_performed": 0,
    }


def reconcile(args: argparse.Namespace) -> dict[str, Any]:
    if ACCOUNT_ID_RE.fullmatch(args.expected_account_id) is None:
        raise ActivationError("Expected account ID is invalid")
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z0-9]+)+-[0-9]+", args.region):
        raise ActivationError("AWS region is invalid")
    publish_frontend.verify_source_revision(args.source_revision)
    import boto3

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    identity = session.client("sts").get_caller_identity()
    caller_hash = publish_frontend.verify_caller(
        identity, args.expected_account_id, args.expected_role_name
    )
    result = reconcile_activation(
        session.client("dynamodb"),
        session.client("scheduler"),
        session.client("s3"),
        table_name=args.table_name,
        pilot_id=args.pilot_id,
        schedule_name=args.schedule_name,
        worker_arn=args.worker_arn,
        scheduler_role_arn=args.scheduler_role_arn,
        bucket=args.bucket,
        region=args.region,
        source_revision=args.source_revision,
        release_sha256=args.expected_release_sha256,
    )
    result["caller_hash"] = caller_hash
    return result


def activate(
    args: argparse.Namespace,
    *,
    now: Callable[[], dt.datetime] = _utc_now,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if ACCOUNT_ID_RE.fullmatch(args.expected_account_id) is None:
        raise ActivationError("Expected account ID is invalid")
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z0-9]+)+-[0-9]+", args.region):
        raise ActivationError("AWS region is invalid")

    # Fail before creating any AWS client when the operator is not executing
    # the exact, clean commit recorded in the deployment contract.
    publish_frontend.verify_source_revision(args.source_revision)

    import boto3  # imported only in the explicit AWS activation mode

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    identity = session.client("sts").get_caller_identity()
    caller_hash = publish_frontend.verify_caller(
        identity,
        args.expected_account_id,
        args.expected_role_name,
    )
    clients = {
        "s3": session.client("s3"),
        "cloudfront": session.client("cloudfront"),
        "apigateway": session.client("apigatewayv2"),
        "cognito": session.client("cognito-idp"),
        "dynamodb": session.client("dynamodb"),
        "scheduler": session.client("scheduler"),
    }
    provision_users.verify_targets(
        clients["cognito"],
        clients["dynamodb"],
        user_pool_id=args.user_pool_id,
        table_name=args.table_name,
        pilot_id=args.pilot_id,
        require_complete=True,
    )
    verify_prepared(
        clients["dynamodb"], table_name=args.table_name, pilot_id=args.pilot_id
    )
    artifacts = publish_frontend.collect_build(args.build_dir)
    release = publish_frontend.release_sha256(artifacts)
    if release != args.expected_release_sha256:
        raise ActivationError("Static build hash does not match the approved release")
    live_template = publish_frontend.verify_targets(
        clients["s3"],
        clients["cloudfront"],
        clients["apigateway"],
        clients["cognito"],
        bucket=args.bucket,
        distribution_id=args.distribution_id,
        region=args.region,
        account_id=args.expected_account_id,
        api_id=args.api_id,
        user_pool_id=args.user_pool_id,
        client_id=args.client_id,
        cognito_domain_prefix=args.cognito_domain_prefix,
        source_revision=args.source_revision,
        release_sha256=release,
    )

    t0, expires_at = activation_times(now(), args.activation_delay_seconds)
    runtime_value = dict(live_template)
    runtime_value["expiresAt"] = _iso_z(expires_at)
    runtime = publish_frontend.validate_runtime_config(
        runtime_value,
        args.region,
        expected_live=live_template,
        allow_prepared=False,
    )
    publish_frontend.publish(
        clients["s3"],
        clients["cloudfront"],
        bucket=args.bucket,
        distribution_id=args.distribution_id,
        artifacts=artifacts,
        runtime=runtime,
        release=release,
        source_revision=args.source_revision,
    )
    verify_runtime_object(clients["s3"], bucket=args.bucket, expected=runtime)

    schedule_target: dict[str, Any] | None = None
    try:
        schedule_target = arm_schedule(
            clients["scheduler"],
            schedule_name=args.schedule_name,
            worker_arn=args.worker_arn,
            scheduler_role_arn=args.scheduler_role_arn,
            expires_at=expires_at,
        )
        remaining = max(0.0, (t0 - now()).total_seconds())
        if remaining > MAX_ACTIVATION_DELAY_SECONDS:
            raise ActivationError("Activation wait exceeded the bounded delay")
        if remaining:
            sleeper(remaining)
        activated_at = require_exact_t0(t0, now())
    except Exception:
        if schedule_target is not None:
            rollback_schedule(
                clients["scheduler"],
                schedule_name=args.schedule_name,
                target=schedule_target,
                expires_at=expires_at,
            )
        raise

    try:
        commit_active(
            clients["dynamodb"],
            table_name=args.table_name,
            pilot_id=args.pilot_id,
            t0=t0,
            expires_at=expires_at,
            activated_at=activated_at,
        )
    except SafeRollbackActivationError:
        if schedule_target is not None:
            rollback_schedule(
                clients["scheduler"],
                schedule_name=args.schedule_name,
                target=schedule_target,
                expires_at=expires_at,
            )
        raise
    except AmbiguousActivationError:
        # Request-time expiry remains primary. Disabling the redundant schedule
        # while ACTIVE may already be durable would be the unsafe choice.
        raise

    return {
        "status": "ACTIVE",
        "caller_hash": caller_hash,
        "release_sha256": release,
        "source_revision": args.source_revision,
        "t0": _iso_z(t0),
        "activated_at": _iso_z(activated_at),
        "expires_at": _iso_z(expires_at),
        "active_window_hours": ACTIVE_WINDOW_HOURS,
        "runtime_config_no_store": True,
        "synthetic_user_count": 4,
        "credentials_persisted": False,
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    commands = value.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="Validate the activation state machine locally")
    apply_parser = commands.add_parser("apply", help="Publish, arm expiry and commit ACTIVE")
    apply_parser.add_argument("--profile", required=True)
    apply_parser.add_argument("--expected-account-id", required=True)
    apply_parser.add_argument(
        "--expected-role-name",
        default=publish_frontend.DEPLOYMENT_ROLE_NAME,
        choices=[publish_frontend.DEPLOYMENT_ROLE_NAME],
    )
    apply_parser.add_argument("--region", required=True)
    apply_parser.add_argument("--build-dir", type=Path, required=True)
    apply_parser.add_argument("--expected-release-sha256", required=True)
    apply_parser.add_argument("--source-revision", required=True)
    apply_parser.add_argument("--bucket", required=True)
    apply_parser.add_argument("--distribution-id", required=True)
    apply_parser.add_argument("--api-id", required=True)
    apply_parser.add_argument("--user-pool-id", required=True)
    apply_parser.add_argument("--client-id", required=True)
    apply_parser.add_argument("--cognito-domain-prefix", required=True)
    apply_parser.add_argument("--table-name", required=True)
    apply_parser.add_argument("--pilot-id", required=True)
    apply_parser.add_argument("--schedule-name", required=True)
    apply_parser.add_argument("--worker-arn", required=True)
    apply_parser.add_argument("--scheduler-role-arn", required=True)
    apply_parser.add_argument(
        "--activation-delay-seconds", type=int, default=15
    )
    reconcile_parser = commands.add_parser(
        "reconcile", help="Read and classify an ambiguous activation without mutations"
    )
    reconcile_parser.add_argument("--profile", required=True)
    reconcile_parser.add_argument("--expected-account-id", required=True)
    reconcile_parser.add_argument(
        "--expected-role-name",
        default=publish_frontend.DEPLOYMENT_ROLE_NAME,
        choices=[publish_frontend.DEPLOYMENT_ROLE_NAME],
    )
    reconcile_parser.add_argument("--region", required=True)
    reconcile_parser.add_argument("--expected-release-sha256", required=True)
    reconcile_parser.add_argument("--source-revision", required=True)
    reconcile_parser.add_argument("--bucket", required=True)
    reconcile_parser.add_argument("--table-name", required=True)
    reconcile_parser.add_argument("--pilot-id", required=True)
    reconcile_parser.add_argument("--schedule-name", required=True)
    reconcile_parser.add_argument("--worker-arn", required=True)
    reconcile_parser.add_argument("--scheduler-role-arn", required=True)
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "validate":
            now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
            t0, expires = activation_times(now, 15)
            result = {
                "status": "VALID",
                "initial_state": "PREPARED",
                "final_commit": "conditional_PREPARED_to_ACTIVE",
                "active_window_hours": int((expires - t0).total_seconds() / 3600),
                "max_wait_seconds": MAX_ACTIVATION_DELAY_SECONDS,
            }
        elif args.command == "apply":
            result = activate(args)
        else:
            result = reconcile(args)
    except (ActivationError, publish_frontend.FrontendPublishError, provision_users.ProvisioningError, KeyboardInterrupt, OSError) as exc:
        message = "interrupted" if isinstance(exc, KeyboardInterrupt) else str(exc)
        print(f"pilot activation: FAIL: {message}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 2 if result.get("status") == "UNEXPECTED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
