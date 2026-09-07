from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MODULE_PATH = ROOT / "activate_pilot.py"
SPEC = importlib.util.spec_from_file_location("activate_pilot", MODULE_PATH)
assert SPEC and SPEC.loader
activate_pilot = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = activate_pilot
SPEC.loader.exec_module(activate_pilot)


class FakeDynamo:
    def __init__(self, *, update_raises=False, read_raises_after_update=False) -> None:
        self.item = {
            "status": {"S": "PREPARED"},
            "expires_at_epoch": {"N": "0"},
        }
        self.update_request = None
        self.update_raises = update_raises
        self.read_raises_after_update = read_raises_after_update

    def get_item(self, **_kwargs):
        if self.update_request is not None and self.read_raises_after_update:
            raise RuntimeError("injected consistent-read failure")
        return {"Item": dict(self.item)}

    def update_item(self, **kwargs):
        self.update_request = kwargs
        if self.update_raises:
            raise RuntimeError("injected update failure")
        values = kwargs["ExpressionAttributeValues"]
        self.item.update(
            {
                "status": values[":active"],
                "t0": values[":t0"],
                "activated_at": values[":activated"],
                "expires_at": values[":expires_iso"],
                "expires_at_epoch": values[":expires_epoch"],
            }
        )


class FakeScheduler:
    def __init__(self) -> None:
        self.updates: list[dict] = []
        self.schedule = {
            "State": "DISABLED",
            "Target": {
                "Arn": "arn:synthetic:worker",
                "RoleArn": "arn:synthetic:scheduler-role",
                "RetryPolicy": {
                    "MaximumEventAgeInSeconds": 3600,
                    "MaximumRetryAttempts": 2,
                },
            },
        }

    def get_schedule(self, **_kwargs):
        return self.schedule

    def update_schedule(self, **kwargs):
        self.updates.append(kwargs)


def runtime_config(expires_at=None):
    return {
        "apiBaseUrl": "https://abc123.execute-api.us-east-1.amazonaws.com",
        "awsRegion": "us-east-1",
        "cognitoUserPool": "us-east-1_synthetic",
        "cognitoClientId": "syntheticclient",
        "cognitoDomain": "https://approvals-sales-demo.auth.us-east-1.amazoncognito.com",
        "redirectUri": "https://example.cloudfront.net/auth/callback",
        "logoutUri": "https://example.cloudfront.net/",
        "oauthFlow": "authorization_code_pkce",
        "scopes": ["openid"],
        "expiresAt": expires_at,
        "syntheticDataOnly": True,
        "deploymentBinding": {
            "sourceRevision": "a" * 40,
            "frontendReleaseSha256": "b" * 64,
        },
    }


class FakeS3:
    def __init__(self, expires_at=None) -> None:
        self.body = json.dumps(
            runtime_config(expires_at), sort_keys=True, separators=(",", ":")
        ).encode()

    def head_object(self, **_kwargs):
        return {
            "CacheControl": "no-store",
            "Metadata": {"sha256": hashlib.sha256(self.body).hexdigest()},
        }

    def get_object(self, **_kwargs):
        return {"Body": io.BytesIO(self.body)}


class ActivatePilotTests(unittest.TestCase):
    def test_window_is_exactly_192_hours_and_delay_is_bounded(self) -> None:
        now = dt.datetime(2026, 8, 26, tzinfo=dt.timezone.utc)
        t0, expires = activate_pilot.activation_times(now, 15)
        self.assertEqual((expires - t0).total_seconds(), 192 * 3600)
        with self.assertRaises(activate_pilot.ActivationError):
            activate_pilot.activation_times(now, 61)
        self.assertEqual(activate_pilot.require_exact_t0(t0, t0), t0)
        with self.assertRaises(activate_pilot.SafeRollbackActivationError):
            activate_pilot.require_exact_t0(t0, t0 + dt.timedelta(seconds=1))

    def test_active_is_single_conditional_final_write(self) -> None:
        dynamodb = FakeDynamo()
        t0 = dt.datetime(2026, 8, 26, 18, 0, tzinfo=dt.timezone.utc)
        expires = t0 + dt.timedelta(hours=192)
        activated = t0
        activate_pilot.verify_prepared(
            dynamodb, table_name="approvals-sales-demo-records", pilot_id="demo-sales-plus"
        )
        activate_pilot.commit_active(
            dynamodb,
            table_name="approvals-sales-demo-records",
            pilot_id="demo-sales-plus",
            t0=t0,
            expires_at=expires,
            activated_at=activated,
        )
        self.assertEqual(
            dynamodb.update_request["ConditionExpression"],
            "#status = :prepared AND expires_at_epoch = :zero",
        )
        self.assertEqual(dynamodb.item["status"], {"S": "ACTIVE"})
        self.assertEqual(dynamodb.item["expires_at_epoch"], {"N": str(int(expires.timestamp()))})

    def test_direct_commit_rejects_a_shortened_window(self) -> None:
        dynamodb = FakeDynamo()
        t0 = dt.datetime(2026, 8, 26, 18, 0, tzinfo=dt.timezone.utc)
        with self.assertRaises(activate_pilot.SafeRollbackActivationError):
            activate_pilot.commit_active(
                dynamodb,
                table_name="approvals-sales-demo-records",
                pilot_id="demo-sales-plus",
                t0=t0,
                expires_at=t0 + dt.timedelta(hours=192),
                activated_at=t0 + dt.timedelta(seconds=1),
            )
        self.assertIsNone(dynamodb.update_request)

    def test_direct_commit_rejects_expiry_other_than_t0_plus_192_hours(self) -> None:
        dynamodb = FakeDynamo()
        t0 = dt.datetime(2026, 8, 26, 18, 0, tzinfo=dt.timezone.utc)
        with self.assertRaises(activate_pilot.SafeRollbackActivationError):
            activate_pilot.commit_active(
                dynamodb,
                table_name="approvals-sales-demo-records",
                pilot_id="demo-sales-plus",
                t0=t0,
                expires_at=t0 + dt.timedelta(hours=191, minutes=59),
                activated_at=t0,
            )
        self.assertIsNone(dynamodb.update_request)

    def test_failed_update_with_prepared_readback_is_safe_to_rollback(self) -> None:
        dynamodb = FakeDynamo(update_raises=True)
        t0 = dt.datetime(2026, 8, 26, 18, 0, tzinfo=dt.timezone.utc)
        with self.assertRaises(activate_pilot.SafeRollbackActivationError):
            activate_pilot.commit_active(
                dynamodb,
                table_name="approvals-sales-demo-records",
                pilot_id="demo-sales-plus",
                t0=t0,
                expires_at=t0 + dt.timedelta(hours=192),
                activated_at=t0,
            )

    def test_readback_failure_after_update_is_ambiguous_and_keeps_schedule(self) -> None:
        dynamodb = FakeDynamo(read_raises_after_update=True)
        t0 = dt.datetime(2026, 8, 26, 18, 0, tzinfo=dt.timezone.utc)
        with self.assertRaises(activate_pilot.AmbiguousActivationError):
            activate_pilot.commit_active(
                dynamodb,
                table_name="approvals-sales-demo-records",
                pilot_id="demo-sales-plus",
                t0=t0,
                expires_at=t0 + dt.timedelta(hours=192),
                activated_at=t0,
            )

    def test_schedule_payload_and_rollback_are_exact(self) -> None:
        scheduler = FakeScheduler()
        expires = dt.datetime(2026, 9, 3, 18, 0, tzinfo=dt.timezone.utc)
        target = activate_pilot.arm_schedule(
            scheduler,
            schedule_name="approvals-sales-demo-expire",
            worker_arn="arn:synthetic:worker",
            scheduler_role_arn="arn:synthetic:scheduler-role",
            expires_at=expires,
        )
        self.assertEqual(scheduler.updates[-1]["State"], "ENABLED")
        payload = json.loads(scheduler.updates[-1]["Target"]["Input"])
        self.assertEqual(
            payload,
            {
                "operation": "EXPIRE_PILOT",
                "scheduled_expires_at": "2026-09-03T18:00:00Z",
                "expected_user_count": 4,
            },
        )
        activate_pilot.rollback_schedule(
            scheduler,
            schedule_name="approvals-sales-demo-expire",
            target=target,
            expires_at=expires,
        )
        self.assertEqual(scheduler.updates[-1]["State"], "DISABLED")

    def test_reconcile_prepared_is_read_only_and_actionable(self) -> None:
        result = activate_pilot.reconcile_activation(
            FakeDynamo(),
            FakeScheduler(),
            FakeS3(),
            table_name="approvals-sales-demo-records",
            pilot_id="demo-sales-plus",
            schedule_name="approvals-sales-demo-expire",
            worker_arn="arn:synthetic:worker",
            scheduler_role_arn="arn:synthetic:scheduler-role",
            bucket="approvals-sales-demo-web-synthetic",
            region="us-east-1",
            source_revision="a" * 40,
            release_sha256="b" * 64,
        )
        self.assertEqual(result["status"], "PREPARED")
        self.assertEqual(result["operator_action"], "RETRY_ACTIVATION")
        self.assertEqual(result["mutations_performed"], 0)

    def test_reconcile_prepared_with_active_runtime_orders_safe_recovery(self) -> None:
        result = activate_pilot.reconcile_activation(
            FakeDynamo(),
            FakeScheduler(),
            FakeS3("2026-09-03T18:00:00Z"),
            table_name="approvals-sales-demo-records",
            pilot_id="demo-sales-plus",
            schedule_name="approvals-sales-demo-expire",
            worker_arn="arn:synthetic:worker",
            scheduler_role_arn="arn:synthetic:scheduler-role",
            bucket="approvals-sales-demo-web-synthetic",
            region="us-east-1",
            source_revision="a" * 40,
            release_sha256="b" * 64,
        )
        self.assertEqual(
            result["operator_action"],
            "REPUBLISH_PREPARED_RUNTIME_THEN_DISABLE_SCHEDULE_AND_RETRY",
        )
        self.assertFalse(result["safe_to_disable_schedule"])

    def test_reconcile_active_requires_exact_runtime_and_schedule(self) -> None:
        t0 = dt.datetime(2026, 8, 26, 18, 0, tzinfo=dt.timezone.utc)
        expires = t0 + dt.timedelta(hours=192)
        expires_iso = activate_pilot._iso_z(expires)
        dynamodb = FakeDynamo()
        dynamodb.item = {
            "status": {"S": "ACTIVE"},
            "t0": {"S": activate_pilot._iso_z(t0)},
            "activated_at": {"S": activate_pilot._iso_z(t0)},
            "expires_at": {"S": expires_iso},
            "expires_at_epoch": {"N": str(int(expires.timestamp()))},
        }
        scheduler = FakeScheduler()
        scheduler.schedule.update(
            {
                "State": "ENABLED",
                "ScheduleExpression": f"at({expires_iso[:-1]})",
                "ScheduleExpressionTimezone": "UTC",
                "FlexibleTimeWindow": {"Mode": "OFF"},
                "ActionAfterCompletion": "DELETE",
            }
        )
        scheduler.schedule["Target"]["Input"] = json.dumps(
            {
                "operation": "EXPIRE_PILOT",
                "scheduled_expires_at": expires_iso,
                "expected_user_count": 4,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        result = activate_pilot.reconcile_activation(
            dynamodb,
            scheduler,
            FakeS3(expires_iso),
            table_name="approvals-sales-demo-records",
            pilot_id="demo-sales-plus",
            schedule_name="approvals-sales-demo-expire",
            worker_arn="arn:synthetic:worker",
            scheduler_role_arn="arn:synthetic:scheduler-role",
            bucket="approvals-sales-demo-web-synthetic",
            region="us-east-1",
            source_revision="a" * 40,
            release_sha256="b" * 64,
        )
        self.assertEqual(result["status"], "ACTIVE")
        self.assertEqual(result["mutations_performed"], 0)

        scheduler.schedule["FlexibleTimeWindow"] = {
            "Mode": "FLEXIBLE",
            "MaximumWindowInMinutes": 15,
        }
        drifted = activate_pilot.reconcile_activation(
            dynamodb,
            scheduler,
            FakeS3(expires_iso),
            table_name="approvals-sales-demo-records",
            pilot_id="demo-sales-plus",
            schedule_name="approvals-sales-demo-expire",
            worker_arn="arn:synthetic:worker",
            scheduler_role_arn="arn:synthetic:scheduler-role",
            bucket="approvals-sales-demo-web-synthetic",
            region="us-east-1",
            source_revision="a" * 40,
            release_sha256="b" * 64,
        )
        self.assertEqual(drifted["status"], "UNEXPECTED")


if __name__ == "__main__":
    unittest.main()
