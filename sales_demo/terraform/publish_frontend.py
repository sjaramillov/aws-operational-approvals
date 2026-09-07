#!/usr/bin/env python3
"""Publish and remove the bounded sales PWA without persisting AWS secrets.

``validate`` is local-only. ``publish`` and ``cleanup`` require an explicit AWS
profile whose STS identity is the approved assumed deployment role. The
version-aware cleanup is deliberately separate from Terraform because the
versioned bucket uses ``force_destroy = false``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


MAX_BUILD_BYTES = 10 * 1024 * 1024
MAX_OBJECT_VERSIONS = 10_000
DEPLOYMENT_ROLE_NAME = "APPROVALS-TerraformDeploymentRole"
MANIFEST_KEY = ".approvals-sales-demo-manifest.json"
EXPECTED_CONFIG_KEYS = {
    "apiBaseUrl",
    "awsRegion",
    "cognitoUserPool",
    "cognitoClientId",
    "cognitoDomain",
    "redirectUri",
    "logoutUri",
    "oauthFlow",
    "scopes",
    "expiresAt",
    "syntheticDataOnly",
    "deploymentBinding",
}
EXPECTED_BINDING_KEYS = {"sourceRevision", "frontendReleaseSha256"}
ACCOUNT_ID_RE = re.compile(r"(?<![0-9])[0-9]{12}(?![0-9])")
REGION_RE = re.compile(r"^[a-z]{2}(?:-[a-z0-9]+)+-[0-9]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"(?i)aws_(?:secret_access_key|session_token)\s*[:=]"),
    re.compile(rb"(?i)client_secret\s*[:=]"),
)
CLASSIC_HOSTED_UI_CSS = (
    ".banner-customizable { background-color: #0b2a3c; } "
    ".submitButton-customizable { background-color: #0b6b5b; }"
)


class FrontendPublishError(RuntimeError):
    """Raised when publication or cleanup cannot remain fail closed."""


@dataclass(frozen=True)
class Artifact:
    key: str
    body: bytes
    sha256: str
    content_type: str
    cache_control: str


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise FrontendPublishError(f"Required JSON is not a regular file: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FrontendPublishError(f"Invalid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise FrontendPublishError(f"JSON root must be an object: {path.name}")
    return value


def validate_runtime_config(
    value: dict[str, Any],
    expected_region: str,
    *,
    expected_live: dict[str, Any] | None = None,
    allow_prepared: bool = True,
) -> bytes:
    if set(value) != EXPECTED_CONFIG_KEYS:
        raise FrontendPublishError("runtime-config.json has an unexpected schema")
    if value["awsRegion"] != expected_region or REGION_RE.fullmatch(expected_region) is None:
        raise FrontendPublishError("runtime-config.json region does not match")
    if value["oauthFlow"] != "authorization_code_pkce" or value["scopes"] != ["openid"]:
        raise FrontendPublishError("runtime-config.json OAuth contract has drifted")
    if value["syntheticDataOnly"] is not True:
        raise FrontendPublishError("runtime-config.json must declare synthetic data only")
    urls = {
        "apiBaseUrl": rf"^https://[a-z0-9]+\.execute-api\.{re.escape(expected_region)}\.amazonaws\.com/?$",
        "cognitoDomain": rf"^https://[a-z0-9-]+\.auth\.{re.escape(expected_region)}\.amazoncognito\.com$",
        "redirectUri": r"^https://[a-z0-9-]+\.cloudfront\.net/auth/callback$",
        "logoutUri": r"^https://[a-z0-9-]+\.cloudfront\.net/$",
    }
    for key, pattern in urls.items():
        if not isinstance(value[key], str) or re.fullmatch(pattern, value[key]) is None:
            raise FrontendPublishError(f"runtime-config.json contains an invalid {key}")
    if not isinstance(value["cognitoUserPool"], str) or not value["cognitoUserPool"].startswith(
        expected_region + "_"
    ):
        raise FrontendPublishError("runtime-config.json user pool is invalid")
    if not isinstance(value["cognitoClientId"], str) or not value["cognitoClientId"]:
        raise FrontendPublishError("runtime-config.json client ID is invalid")
    expires_at = value["expiresAt"]
    if expires_at is None and allow_prepared:
        pass
    elif not isinstance(expires_at, str) or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
        expires_at,
    ) is None:
        raise FrontendPublishError("runtime-config.json expiry is invalid")
    binding = value.get("deploymentBinding")
    if not isinstance(binding, dict) or set(binding) != EXPECTED_BINDING_KEYS:
        raise FrontendPublishError("runtime-config.json deployment binding is invalid")
    if GIT_SHA_RE.fullmatch(str(binding.get("sourceRevision") or "")) is None:
        raise FrontendPublishError("runtime-config.json source revision is invalid")
    if SHA256_RE.fullmatch(str(binding.get("frontendReleaseSha256") or "")) is None:
        raise FrontendPublishError("runtime-config.json release binding is invalid")
    if expected_live is not None:
        expected_without_expiry = dict(expected_live)
        expected_without_expiry["expiresAt"] = value["expiresAt"]
        if value != expected_without_expiry:
            raise FrontendPublishError(
                "runtime-config.json does not match the exact live Terraform targets"
            )
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    if ACCOUNT_ID_RE.search(encoded.decode()):
        raise FrontendPublishError("runtime-config.json contains an account identifier")
    return encoded


def validate_prepared_runtime_config(
    value: dict[str, Any],
    expected_region: str,
    *,
    expected_live: dict[str, Any] | None = None,
) -> bytes:
    if value.get("expiresAt") is not None:
        raise FrontendPublishError(
            "Standalone publication must leave runtime-config PREPARED with null expiry"
        )
    return validate_runtime_config(
        value,
        expected_region,
        expected_live=expected_live,
        allow_prepared=True,
    )


def runtime_config_for_targets(
    *,
    api_base_url: str,
    region: str,
    user_pool_id: str,
    client_id: str,
    cognito_domain: str,
    web_domain: str,
    expires_at: str | None,
    source_revision: str,
    release_sha256: str,
) -> dict[str, Any]:
    """Build the sole public configuration shape from verified live targets."""

    return {
        "apiBaseUrl": api_base_url.rstrip("/"),
        "awsRegion": region,
        "cognitoUserPool": user_pool_id,
        "cognitoClientId": client_id,
        "cognitoDomain": cognito_domain.rstrip("/"),
        "redirectUri": f"https://{web_domain}/auth/callback",
        "logoutUri": f"https://{web_domain}/",
        "oauthFlow": "authorization_code_pkce",
        "scopes": ["openid"],
        "expiresAt": expires_at,
        "syntheticDataOnly": True,
        "deploymentBinding": {
            "sourceRevision": source_revision,
            "frontendReleaseSha256": release_sha256,
        },
    }


def _cache_control(key: str) -> str:
    if key in {"index.html", "runtime-config.json"}:
        return "no-store"
    if re.search(r"[.-][0-9a-f]{8,}[.-]", PurePosixPath(key).name):
        return "public,max-age=31536000,immutable"
    return "public,max-age=300"


def collect_build(build_dir: Path) -> list[Artifact]:
    if not build_dir.is_dir() or build_dir.is_symlink():
        raise FrontendPublishError("Build directory is missing or unsafe")
    artifacts: list[Artifact] = []
    total = 0
    for path in sorted(build_dir.rglob("*")):
        relative = path.relative_to(build_dir)
        if path.is_symlink():
            raise FrontendPublishError(f"Symlinks are forbidden in the build: {relative}")
        if path.is_dir():
            continue
        key = PurePosixPath(*relative.parts).as_posix()
        if any(part.startswith(".") for part in relative.parts):
            raise FrontendPublishError(f"Hidden files are forbidden in the build: {key}")
        if key == "runtime-config.json":
            raise FrontendPublishError("runtime-config.json must be supplied post-apply")
        if key.endswith(".map"):
            raise FrontendPublishError("Source maps are forbidden in the public pilot")
        body = path.read_bytes()
        total += len(body)
        if total > MAX_BUILD_BYTES:
            raise FrontendPublishError("Uncompressed build exceeds 10 MiB")
        if ACCOUNT_ID_RE.search(body.decode("utf-8", errors="ignore")):
            raise FrontendPublishError(f"Build contains a 12-digit identifier: {key}")
        if any(pattern.search(body) for pattern in SECRET_PATTERNS):
            raise FrontendPublishError(f"Build contains secret-like material: {key}")
        content_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
        if key.endswith((".js", ".mjs")):
            content_type = "text/javascript"
        artifacts.append(
            Artifact(key, body, _sha256(body), content_type, _cache_control(key))
        )
    if not any(item.key == "index.html" for item in artifacts):
        raise FrontendPublishError("Build does not contain index.html")
    return artifacts


def release_sha256(artifacts: Iterable[Artifact]) -> str:
    digest = hashlib.sha256()
    for artifact in sorted(artifacts, key=lambda value: value.key):
        digest.update(artifact.key.encode())
        digest.update(b"\0")
        digest.update(bytes.fromhex(artifact.sha256))
        digest.update(b"\0")
        digest.update(str(len(artifact.body)).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def validate_local(
    build_dir: Path,
    runtime_config_path: Path,
    expected_region: str,
    expected_release_sha256: str,
) -> tuple[list[Artifact], bytes, str]:
    if SHA256_RE.fullmatch(expected_release_sha256) is None:
        raise FrontendPublishError("Expected release SHA-256 is invalid")
    artifacts = collect_build(build_dir)
    release = release_sha256(artifacts)
    if release != expected_release_sha256:
        raise FrontendPublishError("Static build SHA-256 does not match the approved release")
    runtime = validate_runtime_config(_safe_json(runtime_config_path), expected_region)
    return artifacts, runtime, release


def verify_caller(identity: dict[str, Any], account_id: str, role_name: str) -> str:
    if role_name != DEPLOYMENT_ROLE_NAME:
        raise FrontendPublishError("Deployment role name is outside the approved contract")
    if re.fullmatch(r"[0-9]{12}", account_id) is None:
        raise FrontendPublishError("Expected account ID is invalid")
    arn = str(identity.get("Arn") or "")
    if identity.get("Account") != account_id or f":assumed-role/{role_name}/" not in arn:
        raise FrontendPublishError("STS identity is not the approved assumed deployment role")
    return _sha256(arn.encode())


def verify_source_revision(
    source_revision: str, *, repo_root: Path | None = None
) -> str:
    """Bind publication to the clean Git commit that contains this tool."""

    if GIT_SHA_RE.fullmatch(source_revision) is None:
        raise FrontendPublishError("Source revision must be a full Git SHA")
    root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise FrontendPublishError("Cannot verify the local Git source revision") from exc
    if head != source_revision:
        raise FrontendPublishError("Source revision does not match the local Git HEAD")
    if dirty:
        raise FrontendPublishError("Source tree must be clean before publication")
    return head


def verify_storage_targets(
    s3: Any,
    cloudfront: Any,
    *,
    bucket: str,
    distribution_id: str,
    region: str,
    account_id: str,
) -> dict[str, Any]:
    """Bind publication/cleanup to the exact private origin and account."""

    if not re.fullmatch(r"approvals-sales-demo-web-[0-9a-f]{12}", bucket):
        raise FrontendPublishError("Bucket name is outside the exact sales_demo prefix")
    location = s3.get_bucket_location(Bucket=bucket).get("LocationConstraint") or "us-east-1"
    if location != region:
        raise FrontendPublishError("Bucket region does not match the approved region")
    tags = {
        item["Key"]: item["Value"]
        for item in s3.get_bucket_tagging(Bucket=bucket).get("TagSet") or []
    }
    if tags.get("DeploymentProfile") != "sales_demo" or tags.get(
        "DataClassification"
    ) != "synthetic-only":
        raise FrontendPublishError("Bucket tags do not prove the sales_demo contract")
    distribution = cloudfront.get_distribution(Id=distribution_id)["Distribution"]
    arn = str(distribution.get("ARN") or "")
    if f":cloudfront::{account_id}:distribution/" not in arn:
        raise FrontendPublishError("CloudFront distribution is outside the approved account")
    origins = distribution.get("DistributionConfig", {}).get("Origins", {}).get("Items") or []
    expected_domain = f"{bucket}.s3.{region}.amazonaws.com"
    if {origin.get("DomainName") for origin in origins} != {expected_domain}:
        raise FrontendPublishError("CloudFront origin does not match the exact private bucket")
    return distribution


def verify_targets(
    s3: Any,
    cloudfront: Any,
    apigateway: Any,
    cognito: Any,
    *,
    bucket: str,
    distribution_id: str,
    region: str,
    account_id: str,
    api_id: str,
    user_pool_id: str,
    client_id: str,
    cognito_domain_prefix: str,
    source_revision: str,
    release_sha256: str,
) -> dict[str, Any]:
    distribution = verify_storage_targets(
        s3,
        cloudfront,
        bucket=bucket,
        distribution_id=distribution_id,
        region=region,
        account_id=account_id,
    )
    web_domain = str(distribution.get("DomainName") or "")
    if not re.fullmatch(r"[a-z0-9-]+\.cloudfront\.net", web_domain):
        raise FrontendPublishError("CloudFront distribution domain is unavailable")

    api = apigateway.get_api(ApiId=api_id)
    api_endpoint = str(api.get("ApiEndpoint") or "").rstrip("/")
    if api.get("ProtocolType") != "HTTP" or not re.fullmatch(
        rf"https://{re.escape(api_id)}\.execute-api\.{re.escape(region)}\.amazonaws\.com",
        api_endpoint,
    ):
        raise FrontendPublishError("HTTP API target does not match its exact regional ID")
    pool = cognito.describe_user_pool(UserPoolId=user_pool_id).get("UserPool") or {}
    if (
        pool.get("Id") not in (None, user_pool_id)
        or pool.get("UserPoolTier") != "LITE"
        or not pool.get("AdminCreateUserConfig", {}).get("AllowAdminCreateUserOnly")
    ):
        raise FrontendPublishError("Cognito user pool is not the closed sales_demo pool")
    client = cognito.describe_user_pool_client(
        UserPoolId=user_pool_id,
        ClientId=client_id,
    ).get("UserPoolClient") or {}
    if (
        client.get("ClientId") not in (None, client_id)
        or client.get("GenerateSecret") is not False
        or set(client.get("AllowedOAuthFlows") or []) != {"code"}
        or set(client.get("AllowedOAuthScopes") or []) != {"openid"}
        or set(client.get("ExplicitAuthFlows") or []) != {"ALLOW_USER_SRP_AUTH"}
        or client.get("RefreshTokenRotation", {}).get("Feature") != "ENABLED"
        or client.get("RefreshTokenRotation", {}).get("RetryGracePeriodSeconds") != 10
    ):
        raise FrontendPublishError("Cognito app client has drifted from code+PKCE")
    domain = cognito.describe_user_pool_domain(Domain=cognito_domain_prefix).get(
        "DomainDescription"
    ) or {}
    if (
        domain.get("UserPoolId") != user_pool_id
        or domain.get("ManagedLoginVersion") != 1
        or domain.get("Status") != "ACTIVE"
    ):
        raise FrontendPublishError(
            "Cognito domain is not an active classic Hosted UI for the exact pool"
        )
    ui = cognito.get_ui_customization(
        UserPoolId=user_pool_id,
        ClientId=client_id,
    ).get("UICustomization") or {}
    if (
        ui.get("UserPoolId") != user_pool_id
        or ui.get("ClientId") != client_id
        or ui.get("CSS") != CLASSIC_HOSTED_UI_CSS
        or not ui.get("CSSVersion")
    ):
        raise FrontendPublishError("Cognito classic Hosted UI branding is unavailable or drifted")
    cognito_domain = (
        f"https://{cognito_domain_prefix}.auth.{region}.amazoncognito.com"
    )
    expected = runtime_config_for_targets(
        api_base_url=api_endpoint,
        region=region,
        user_pool_id=user_pool_id,
        client_id=client_id,
        cognito_domain=cognito_domain,
        web_domain=web_domain,
        expires_at=None,
        source_revision=source_revision,
        release_sha256=release_sha256,
    )
    if set(client.get("CallbackURLs") or []) != {expected["redirectUri"]} or set(
        client.get("LogoutURLs") or []
    ) != {expected["logoutUri"]}:
        raise FrontendPublishError("Cognito callback/logout URLs do not match CloudFront")
    return expected


def _client_error_code(exc: Exception) -> str:
    response = getattr(exc, "response", {})
    return str(response.get("Error", {}).get("Code") or "")


def _read_manifest(s3: Any, bucket: str) -> dict[str, Any] | None:
    try:
        response = s3.get_object(Bucket=bucket, Key=MANIFEST_KEY)
    except Exception as exc:
        if _client_error_code(exc) in {"NoSuchKey", "404", "NotFound"}:
            return None
        raise
    try:
        value = json.loads(response["Body"].read())
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise FrontendPublishError("Remote publication manifest is invalid") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema") != 1
        or value.get("status") not in {"PREPARING", "PUBLISHED"}
    ):
        raise FrontendPublishError("Remote publication manifest has an unknown schema")
    return value


def _put_manifest(s3: Any, bucket: str, manifest: dict[str, Any]) -> None:
    body = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    s3.put_object(
        Bucket=bucket,
        Key=MANIFEST_KEY,
        Body=body,
        ContentType="application/json",
        CacheControl="no-store",
        ServerSideEncryption="AES256",
        Metadata={"sha256": _sha256(body)},
    )


def _verify_remote_artifact(s3: Any, bucket: str, artifact: Artifact) -> None:
    head = s3.head_object(Bucket=bucket, Key=artifact.key)
    metadata = head.get("Metadata") or {}
    if metadata.get("sha256") != artifact.sha256:
        raise FrontendPublishError(f"S3 verification failed for {artifact.key}")
    if head.get("CacheControl") != artifact.cache_control:
        raise FrontendPublishError(f"S3 cache-control verification failed for {artifact.key}")


def _all_versions(s3: Any, bucket: str) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    key_marker: str | None = None
    version_marker: str | None = None
    while True:
        request: dict[str, Any] = {"Bucket": bucket, "MaxKeys": 1000}
        if key_marker:
            request["KeyMarker"] = key_marker
        if version_marker:
            request["VersionIdMarker"] = version_marker
        response = s3.list_object_versions(**request)
        for item in list(response.get("Versions") or []) + list(
            response.get("DeleteMarkers") or []
        ):
            values.append({"Key": item["Key"], "VersionId": item["VersionId"]})
        if len(values) > MAX_OBJECT_VERSIONS:
            raise FrontendPublishError("Bucket version inventory exceeds the cleanup bound")
        if not response.get("IsTruncated"):
            return values
        key_marker = response.get("NextKeyMarker")
        version_marker = response.get("NextVersionIdMarker")
        if not key_marker:
            raise FrontendPublishError("S3 returned a truncated version inventory without markers")


def _all_multipart_uploads(s3: Any, bucket: str) -> list[dict[str, str]]:
    uploads: list[dict[str, str]] = []
    key_marker: str | None = None
    upload_marker: str | None = None
    while True:
        request: dict[str, Any] = {"Bucket": bucket, "MaxUploads": 1000}
        if key_marker:
            request["KeyMarker"] = key_marker
        if upload_marker:
            request["UploadIdMarker"] = upload_marker
        response = s3.list_multipart_uploads(**request)
        uploads.extend(
            {"Key": item["Key"], "UploadId": item["UploadId"]}
            for item in response.get("Uploads") or []
        )
        if len(uploads) > MAX_OBJECT_VERSIONS:
            raise FrontendPublishError("Multipart inventory exceeds the cleanup bound")
        if not response.get("IsTruncated"):
            return uploads
        key_marker = response.get("NextKeyMarker")
        upload_marker = response.get("NextUploadIdMarker")
        if not key_marker:
            raise FrontendPublishError("S3 returned truncated multipart inventory without markers")


def publish(
    s3: Any,
    cloudfront: Any,
    *,
    bucket: str,
    distribution_id: str,
    artifacts: list[Artifact],
    runtime: bytes,
    release: str,
    source_revision: str,
) -> dict[str, Any]:
    if GIT_SHA_RE.fullmatch(source_revision) is None:
        raise FrontendPublishError("Source revision must be a full Git SHA")
    previous = _read_manifest(s3, bucket)
    existing_versions = _all_versions(s3, bucket)
    previous_allowed = set((previous or {}).get("allowed_history_keys") or [])
    existing_keys = {item["Key"] for item in existing_versions}
    if existing_keys and not previous:
        raise FrontendPublishError("Versioned bucket is non-empty without a trusted manifest")
    if not existing_keys.issubset(previous_allowed | {MANIFEST_KEY}):
        raise FrontendPublishError("Versioned bucket contains an unmanifested object key")

    runtime_artifact = Artifact(
        "runtime-config.json",
        runtime,
        _sha256(runtime),
        "application/json",
        "no-store",
    )
    upload = [*artifacts, runtime_artifact]
    current_keys = {artifact.key for artifact in upload}
    allowed_history = sorted(previous_allowed | existing_keys | current_keys | {MANIFEST_KEY})
    files = [
        {"key": item.key, "sha256": item.sha256, "size": len(item.body)}
        for item in sorted(upload, key=lambda value: value.key)
    ]
    if previous and previous.get("status") == "PREPARING" and (
        previous.get("release_sha256") != release
        or previous.get("source_revision") != source_revision
    ):
        raise FrontendPublishError(
            "A different PREPARING release must be cleaned before publication"
        )
    manifest = {
        "schema": 1,
        "status": "PREPARING",
        "release_sha256": release,
        "source_revision": source_revision,
        "synthetic_data_only": True,
        "files": files,
        "allowed_history_keys": allowed_history,
    }
    # The trusted allowlist is written first. Any interruption from this point
    # can be retried or cleaned without accepting an unknown object key.
    _put_manifest(s3, bucket, manifest)
    for artifact in upload:
        s3.put_object(
            Bucket=bucket,
            Key=artifact.key,
            Body=artifact.body,
            ContentType=artifact.content_type,
            CacheControl=artifact.cache_control,
            ServerSideEncryption="AES256",
            Metadata={"sha256": artifact.sha256},
        )
    for artifact in upload:
        _verify_remote_artifact(s3, bucket, artifact)
    manifest["status"] = "PUBLISHED"
    _put_manifest(s3, bucket, manifest)
    cloudfront.create_invalidation(
        DistributionId=distribution_id,
        InvalidationBatch={
            "CallerReference": f"{release}-{int(time.time())}",
            "Paths": {"Quantity": 1, "Items": ["/*"]},
        },
    )
    return {
        "status": "PUBLISHED",
        "release_sha256": release,
        "file_count": len(upload),
        "bytes": sum(len(item.body) for item in upload),
    }


def cleanup(s3: Any, *, bucket: str) -> dict[str, Any]:
    manifest = _read_manifest(s3, bucket)
    versions = _all_versions(s3, bucket)
    uploads = _all_multipart_uploads(s3, bucket)
    if not versions and not uploads:
        return {"status": "EMPTY", "versions_deleted": 0, "uploads_aborted": 0}
    if manifest is None:
        raise FrontendPublishError("Cannot clean a non-empty bucket without a trusted manifest")
    allowed = set(manifest.get("allowed_history_keys") or []) | {MANIFEST_KEY}
    observed = {item["Key"] for item in versions} | {item["Key"] for item in uploads}
    if not observed.issubset(allowed):
        raise FrontendPublishError("Cleanup refused an unmanifested object key")
    for item in uploads:
        s3.abort_multipart_upload(Bucket=bucket, Key=item["Key"], UploadId=item["UploadId"])
    for start in range(0, len(versions), 1000):
        batch = versions[start : start + 1000]
        response = s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": batch, "Quiet": True},
        )
        if response.get("Errors"):
            raise FrontendPublishError("S3 reported an object-version cleanup failure")
    if _all_versions(s3, bucket) or _all_multipart_uploads(s3, bucket):
        raise FrontendPublishError("Bucket is not empty after version-aware cleanup")
    return {
        "status": "EMPTY",
        "versions_deleted": len(versions),
        "uploads_aborted": len(uploads),
    }


def _aws_clients(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    import boto3  # imported only for explicit publish/cleanup modes

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    caller_hash = verify_caller(
        session.client("sts").get_caller_identity(),
        args.expected_account_id,
        args.expected_role_name,
    )
    return {
        "s3": session.client("s3"),
        "cloudfront": session.client("cloudfront"),
        "apigateway": session.client("apigatewayv2"),
        "cognito": session.client("cognito-idp"),
    }, caller_hash


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    commands = value.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="Validate artifacts without AWS")
    validate.add_argument("--build-dir", type=Path, required=True)
    validate.add_argument("--runtime-config", type=Path, required=True)
    validate.add_argument("--region", required=True)
    validate.add_argument("--expected-release-sha256", required=True)

    for name in ("publish", "cleanup"):
        command = commands.add_parser(name)
        command.add_argument("--profile", required=True)
        command.add_argument("--expected-account-id", required=True)
        command.add_argument(
            "--expected-role-name",
            default=DEPLOYMENT_ROLE_NAME,
            choices=[DEPLOYMENT_ROLE_NAME],
        )
        command.add_argument("--region", required=True)
        command.add_argument("--bucket", required=True)
        command.add_argument("--distribution-id", required=True)
        if name == "publish":
            command.add_argument("--build-dir", type=Path, required=True)
            command.add_argument("--runtime-config", type=Path, required=True)
            command.add_argument("--expected-release-sha256", required=True)
            command.add_argument("--source-revision", required=True)
            command.add_argument("--api-id", required=True)
            command.add_argument("--user-pool-id", required=True)
            command.add_argument("--client-id", required=True)
            command.add_argument("--cognito-domain-prefix", required=True)
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "validate":
            artifacts, runtime, release = validate_local(
                args.build_dir,
                args.runtime_config,
                args.region,
                args.expected_release_sha256,
            )
            result = {
                "status": "VALID",
                "release_sha256": release,
                "file_count": len(artifacts) + 1,
                "bytes": sum(len(item.body) for item in artifacts) + len(runtime),
            }
        else:
            if args.command == "publish":
                verify_source_revision(args.source_revision)
            clients, caller_hash = _aws_clients(args)
            expected_live = None
            if args.command == "publish":
                expected_live = verify_targets(
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
                    release_sha256=args.expected_release_sha256,
                )
            else:
                # Cleanup only needs to bind the versioned origin and account;
                # no public auth target survives the following destroy.
                verify_storage_targets(
                    clients["s3"],
                    clients["cloudfront"],
                    bucket=args.bucket,
                    distribution_id=args.distribution_id,
                    region=args.region,
                    account_id=args.expected_account_id,
                )
            if args.command == "publish":
                artifacts, runtime, release = validate_local(
                    args.build_dir,
                    args.runtime_config,
                    args.region,
                    args.expected_release_sha256,
                )
                runtime_value = json.loads(runtime)
                validate_prepared_runtime_config(
                    runtime_value,
                    args.region,
                    expected_live=expected_live,
                )
                result = publish(
                    clients["s3"],
                    clients["cloudfront"],
                    bucket=args.bucket,
                    distribution_id=args.distribution_id,
                    artifacts=artifacts,
                    runtime=runtime,
                    release=release,
                    source_revision=args.source_revision,
                )
            else:
                result = cleanup(clients["s3"], bucket=args.bucket)
            result["caller_hash"] = caller_hash
    except (FrontendPublishError, KeyboardInterrupt, OSError) as exc:
        message = "interrupted" if isinstance(exc, KeyboardInterrupt) else str(exc)
        print(f"frontend publication: FAIL: {message}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
