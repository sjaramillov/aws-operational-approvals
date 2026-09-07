from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "publish_frontend.py"
SPEC = importlib.util.spec_from_file_location("publish_frontend", MODULE_PATH)
assert SPEC and SPEC.loader
publish_frontend = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = publish_frontend
SPEC.loader.exec_module(publish_frontend)


def runtime_config() -> dict:
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
        "expiresAt": "2026-09-09T00:00:00Z",
        "syntheticDataOnly": True,
        "deploymentBinding": {
            "sourceRevision": "a" * 40,
            "frontendReleaseSha256": "b" * 64,
        },
    }


def prepared_runtime_config() -> dict:
    value = runtime_config()
    value["expiresAt"] = None
    return value


class MissingObject(Exception):
    def __init__(self) -> None:
        self.response = {"Error": {"Code": "NoSuchKey"}}


class FakeCleanupS3:
    def __init__(self, *, unexpected: bool = False) -> None:
        allowed = [publish_frontend.MANIFEST_KEY, "index.html", "runtime-config.json"]
        self.manifest = {
            "schema": 1,
            "status": "PUBLISHED",
            "allowed_history_keys": allowed,
        }
        key = "unexpected.txt" if unexpected else "index.html"
        self.versions = [
            {"Key": key, "VersionId": "v1"},
            {"Key": publish_frontend.MANIFEST_KEY, "VersionId": "v2"},
        ]
        self.uploads = [{"Key": "runtime-config.json", "UploadId": "upload"}]

    def get_object(self, **_kwargs):
        return {"Body": io.BytesIO(json.dumps(self.manifest).encode())}

    def list_object_versions(self, **_kwargs):
        return {"Versions": list(self.versions), "DeleteMarkers": [], "IsTruncated": False}

    def list_multipart_uploads(self, **_kwargs):
        return {"Uploads": list(self.uploads), "IsTruncated": False}

    def abort_multipart_upload(self, **_kwargs):
        self.uploads.clear()

    def delete_objects(self, **kwargs):
        deleted = {(item["Key"], item["VersionId"]) for item in kwargs["Delete"]["Objects"]}
        self.versions = [
            item
            for item in self.versions
            if (item["Key"], item["VersionId"]) not in deleted
        ]
        return {"Deleted": list(kwargs["Delete"]["Objects"])}


class FakePublishS3:
    def __init__(self, fail_on_put: int | None = None) -> None:
        self.fail_on_put = fail_on_put
        self.put_count = 0
        self.objects: dict[str, dict] = {}
        self.versions: list[dict] = []

    def get_object(self, **kwargs):
        value = self.objects.get(kwargs["Key"])
        if value is None:
            raise MissingObject()
        return {"Body": io.BytesIO(value["Body"])}

    def list_object_versions(self, **_kwargs):
        return {"Versions": list(self.versions), "DeleteMarkers": [], "IsTruncated": False}

    def put_object(self, **kwargs):
        self.put_count += 1
        if self.fail_on_put == self.put_count:
            raise RuntimeError("injected upload failure")
        key = kwargs["Key"]
        body = bytes(kwargs["Body"])
        self.objects[key] = {
            "Body": body,
            "Metadata": dict(kwargs.get("Metadata") or {}),
            "CacheControl": kwargs.get("CacheControl"),
        }
        self.versions.append({"Key": key, "VersionId": f"v{self.put_count}"})
        return {}

    def head_object(self, **kwargs):
        value = self.objects[kwargs["Key"]]
        return {
            "Metadata": value["Metadata"],
            "CacheControl": value["CacheControl"],
        }


class FakeCloudFront:
    def __init__(self) -> None:
        self.invalidations = 0

    def create_invalidation(self, **_kwargs):
        self.invalidations += 1
        return {}


class FakeTargetS3:
    def get_bucket_location(self, **_kwargs):
        return {"LocationConstraint": None}

    def get_bucket_tagging(self, **_kwargs):
        return {
            "TagSet": [
                {"Key": "DeploymentProfile", "Value": "sales_demo"},
                {"Key": "DataClassification", "Value": "synthetic-only"},
            ]
        }


class FakeTargetCloudFront:
    def __init__(self, account_id: str) -> None:
        self.account_id = account_id

    def get_distribution(self, **_kwargs):
        return {
            "Distribution": {
                "ARN": f"arn:aws:cloudfront::{self.account_id}:distribution/DIST",
                "DomainName": "example.cloudfront.net",
                "DistributionConfig": {
                    "Origins": {
                        "Items": [
                            {
                                "DomainName": "approvals-sales-demo-web-abcdef123456.s3.us-east-1.amazonaws.com"
                            }
                        ]
                    }
                },
            }
        }


class FakeTargetApi:
    def get_api(self, **kwargs):
        return {
            "ProtocolType": "HTTP",
            "ApiEndpoint": f"https://{kwargs['ApiId']}.execute-api.us-east-1.amazonaws.com",
        }


class FakeTargetCognito:
    def describe_user_pool(self, **kwargs):
        return {
            "UserPool": {
                "Id": kwargs["UserPoolId"],
                "UserPoolTier": "LITE",
                "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True},
            }
        }

    def describe_user_pool_client(self, **kwargs):
        return {
            "UserPoolClient": {
                "ClientId": kwargs["ClientId"],
                "GenerateSecret": False,
                "AllowedOAuthFlows": ["code"],
                "AllowedOAuthScopes": ["openid"],
                "ExplicitAuthFlows": ["ALLOW_USER_SRP_AUTH"],
                "RefreshTokenRotation": {
                    "Feature": "ENABLED",
                    "RetryGracePeriodSeconds": 10,
                },
                "CallbackURLs": ["https://example.cloudfront.net/auth/callback"],
                "LogoutURLs": ["https://example.cloudfront.net/"],
            }
        }

    def describe_user_pool_domain(self, **_kwargs):
        return {
            "DomainDescription": {
                "UserPoolId": "us-east-1_synthetic",
                "ManagedLoginVersion": 1,
                "Status": "ACTIVE",
            }
        }

    def get_ui_customization(self, **kwargs):
        return {
            "UICustomization": {
                "UserPoolId": kwargs["UserPoolId"],
                "ClientId": kwargs["ClientId"],
                "CSS": publish_frontend.CLASSIC_HOSTED_UI_CSS,
                "CSSVersion": "synthetic-version",
            }
        }


class PublishFrontendTests(unittest.TestCase):
    def test_prepared_runtime_is_valid_but_active_validation_requires_expiry(self) -> None:
        publish_frontend.validate_prepared_runtime_config(
            prepared_runtime_config(), "us-east-1"
        )
        with self.assertRaises(publish_frontend.FrontendPublishError):
            publish_frontend.validate_prepared_runtime_config(
                runtime_config(), "us-east-1"
            )
        with self.assertRaises(publish_frontend.FrontendPublishError):
            publish_frontend.validate_runtime_config(
                prepared_runtime_config(), "us-east-1", allow_prepared=False
            )
    def test_source_revision_requires_clean_matching_git_head(self) -> None:
        completed = lambda stdout: mock.Mock(stdout=stdout)
        with mock.patch.object(
            publish_frontend.subprocess,
            "run",
            side_effect=[completed("a" * 40 + "\n"), completed("")],
        ):
            self.assertEqual(
                publish_frontend.verify_source_revision("a" * 40), "a" * 40
            )

        with mock.patch.object(
            publish_frontend.subprocess,
            "run",
            side_effect=[completed("b" * 40 + "\n"), completed("")],
        ):
            with self.assertRaises(publish_frontend.FrontendPublishError):
                publish_frontend.verify_source_revision("a" * 40)

        with mock.patch.object(
            publish_frontend.subprocess,
            "run",
            side_effect=[completed("a" * 40 + "\n"), completed(" M file\n")],
        ):
            with self.assertRaises(publish_frontend.FrontendPublishError):
                publish_frontend.verify_source_revision("a" * 40)

    def test_local_validation_hashes_bundle_without_runtime_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / "dist"
            build.mkdir()
            (build / "index.html").write_text("<main>synthetic</main>", encoding="utf-8")
            assets = build / "assets"
            assets.mkdir()
            (assets / "app.abcdef123456.js").write_text("export default true", encoding="utf-8")
            config_path = root / "runtime-config.json"
            config_path.write_text(json.dumps(runtime_config()), encoding="utf-8")
            artifacts = publish_frontend.collect_build(build)
            expected = publish_frontend.release_sha256(artifacts)

            validated, runtime, release = publish_frontend.validate_local(
                build,
                config_path,
                "us-east-1",
                expected,
            )

            self.assertEqual(release, expected)
            self.assertEqual(len(validated), 2)
            self.assertIn(b"syntheticDataOnly", runtime)
            self.assertEqual(
                next(item for item in validated if item.key.endswith(".js")).cache_control,
                "public,max-age=31536000,immutable",
            )

    def test_build_rejects_runtime_config_and_source_maps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            (build / "index.html").write_text("ok", encoding="utf-8")
            (build / "runtime-config.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(publish_frontend.FrontendPublishError):
                publish_frontend.collect_build(build)
            (build / "runtime-config.json").unlink()
            (build / "app.js.map").write_text("{}", encoding="utf-8")
            with self.assertRaises(publish_frontend.FrontendPublishError):
                publish_frontend.collect_build(build)

    def test_runtime_config_rejects_cross_stack_target(self) -> None:
        expected = runtime_config()
        supplied = runtime_config()
        supplied["apiBaseUrl"] = "https://different.execute-api.us-east-1.amazonaws.com"
        with self.assertRaises(publish_frontend.FrontendPublishError):
            publish_frontend.validate_runtime_config(
                supplied,
                "us-east-1",
                expected_live=expected,
                allow_prepared=False,
            )

    def test_live_target_binding_uses_exact_api_cognito_and_cloudfront(self) -> None:
        account_id = "0" * 12
        expected = publish_frontend.verify_targets(
            FakeTargetS3(),
            FakeTargetCloudFront(account_id),
            FakeTargetApi(),
            FakeTargetCognito(),
            bucket="approvals-sales-demo-web-abcdef123456",
            distribution_id="DIST",
            region="us-east-1",
            account_id=account_id,
            api_id="abc123",
            user_pool_id="us-east-1_synthetic",
            client_id="syntheticclient",
            cognito_domain_prefix="approvals-sales-demo",
            source_revision="a" * 40,
            release_sha256="b" * 64,
        )
        supplied = runtime_config()
        publish_frontend.validate_runtime_config(
            supplied,
            "us-east-1",
            expected_live=expected,
            allow_prepared=False,
        )

    def test_live_target_binding_rejects_missing_classic_hosted_ui(self) -> None:
        cognito = FakeTargetCognito()
        cognito.get_ui_customization = lambda **_kwargs: {"UICustomization": {}}
        with self.assertRaises(publish_frontend.FrontendPublishError):
            publish_frontend.verify_targets(
                FakeTargetS3(),
                FakeTargetCloudFront("0" * 12),
                FakeTargetApi(),
                cognito,
                bucket="approvals-sales-demo-web-abcdef123456",
                distribution_id="DIST",
                region="us-east-1",
                account_id="0" * 12,
                api_id="abc123",
                user_pool_id="us-east-1_synthetic",
                client_id="syntheticclient",
                cognito_domain_prefix="approvals-sales-demo",
                source_revision="a" * 40,
                release_sha256="b" * 64,
            )
    def test_version_aware_cleanup_deletes_only_manifested_keys(self) -> None:
        client = FakeCleanupS3()
        result = publish_frontend.cleanup(client, bucket="approvals-sales-demo-web-abcdef123456")
        self.assertEqual(result["status"], "EMPTY")
        self.assertEqual(result["versions_deleted"], 2)
        self.assertFalse(client.versions)
        self.assertFalse(client.uploads)

    def test_version_aware_cleanup_rejects_unmanifested_key(self) -> None:
        with self.assertRaises(publish_frontend.FrontendPublishError):
            publish_frontend.cleanup(
                FakeCleanupS3(unexpected=True),
                bucket="approvals-sales-demo-web-abcdef123456",
            )

    def test_only_expected_assumed_role_is_accepted(self) -> None:
        account_id = "0" * 12
        result = publish_frontend.verify_caller(
            {
                "Account": account_id,
                "Arn": f"arn:aws:sts::{account_id}:assumed-role/APPROVALS-TerraformDeploymentRole/session",
            },
            account_id,
            "APPROVALS-TerraformDeploymentRole",
        )
        self.assertRegex(result, r"^[0-9a-f]{64}$")
        with self.assertRaises(publish_frontend.FrontendPublishError):
            publish_frontend.verify_caller(
                {
                    "Account": account_id,
                    "Arn": f"arn:aws:sts::{account_id}:assumed-role/Administrator/session",
                },
                account_id,
                "Administrator",
            )

    def test_preparing_manifest_makes_interrupted_publish_retryable(self) -> None:
        s3 = FakePublishS3(fail_on_put=2)
        cloudfront = FakeCloudFront()
        artifact = publish_frontend.Artifact(
            "index.html", b"synthetic", publish_frontend._sha256(b"synthetic"),
            "text/html", "no-store"
        )
        runtime = publish_frontend.validate_runtime_config(
            runtime_config(), "us-east-1", allow_prepared=False
        )
        with self.assertRaises(RuntimeError):
            publish_frontend.publish(
                s3,
                cloudfront,
                bucket="approvals-sales-demo-web-abcdef123456",
                distribution_id="DIST",
                artifacts=[artifact],
                runtime=runtime,
                release="b" * 64,
                source_revision="a" * 40,
            )
        preparing = json.loads(s3.objects[publish_frontend.MANIFEST_KEY]["Body"])
        self.assertEqual(preparing["status"], "PREPARING")

        s3.fail_on_put = None
        result = publish_frontend.publish(
            s3,
            cloudfront,
            bucket="approvals-sales-demo-web-abcdef123456",
            distribution_id="DIST",
            artifacts=[artifact],
            runtime=runtime,
            release="b" * 64,
            source_revision="a" * 40,
        )
        self.assertEqual(result["status"], "PUBLISHED")
        published = json.loads(s3.objects[publish_frontend.MANIFEST_KEY]["Body"])
        self.assertEqual(published["status"], "PUBLISHED")
        self.assertEqual(cloudfront.invalidations, 1)


if __name__ == "__main__":
    unittest.main()
