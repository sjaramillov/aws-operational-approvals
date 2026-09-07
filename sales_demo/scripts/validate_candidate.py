#!/usr/bin/env python3
"""Valida el contrato cruzado del candidato Aprobaciones operativas.

El gate es deliberadamente local y no abre sesiones AWS. No reemplaza Terraform,
pytest, Vitest ni Playwright: evita que esos componentes pasen por separado mientras
el paquete contradice sus límites, expone material sensible o pierde una ruta.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


EXPECTED_OPERATIONS = frozenset(
    {
        "GET /health",
        "GET /me",
        "POST /applications",
        "GET /applications",
        "GET /applications/{id}",
        "GET /approvals",
        "POST /approvals/{id}/decision",
        "GET /plan-plus",
    }
)
REQUIRED_PATHS = (
    "Makefile",
    "README.md",
    "THREAT_MODEL.md",
    "openapi.yaml",
    "workflow.asl.json",
    "backend/lambda_api.py",
    "backend/lambda_worker.py",
    "backend/service.py",
    "scripts/build_lambda_packages.py",
    "terraform/activate_pilot.py",
    "terraform/inventory_zero.py",
    "terraform/preflight.py",
    "terraform/plan_guard.py",
    "terraform/provision_users.py",
    "terraform/publish_frontend.py",
    "terraform/readiness_probe.py",
    "terraform/versions.tf",
    "terraform/variables.tf",
    "terraform/identity.tf",
    "terraform/frontend.tf",
    "terraform/storage.tf",
    "web/package.json",
    "web/public/sw.js",
    "web/scripts/write-runtime-config.mjs",
    "web/src/App.tsx",
    "web/src/auth/pkce.ts",
    "web/src/runtimeConfig.ts",
)
SKIP_PARTS = frozenset(
    {"node_modules", "dist", ".terraform", "__pycache__", ".pytest_cache", "test-results", "playwright-report"}
)
TEXT_SUFFIXES = frozenset(
    {".css", ".hcl", ".html", ".js", ".json", ".md", ".mjs", ".py", ".sh", ".tf", ".ts", ".tsx", ".yaml", ".yml"}
)
# El ARN mock de Terraform admite el sentinel no enrutable de doce ceros. Todo
# otro bloque de doce dígitos se trata como identificador de cuenta persistido.
ACCOUNT_ID = re.compile(r"(?<!\d)(?!0{12}(?!\d))\d{12}(?!\d)")
# Solo entradas completas del lock: SHA-256 hexadecimal o base64 canónico
# (32 bytes, un padding). Los comentarios de bloque se conservan completos;
# los grupos de checksum conservan comillas, coma y espacios.
TERRAFORM_LOCK_CHECKSUM = re.compile(
    r'(?s:/\*.*?(?:\*/|\Z))|'
    r'^([ \t]*")(?:h1:[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=|zh:[0-9a-fA-F]{64})'
    r'("[ \t]*,?[ \t]*\r?)$',
    re.MULTILINE,
)
ACCESS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
EMAIL_ADDRESS = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
PASSWORD_ASSIGNMENT = re.compile(
    r"(?im)^\s*(?:temporary_)?password\s*=\s*[\"'][^\"'${]+[\"']"
)


class CandidateError(RuntimeError):
    """El candidato no satisface un invariante de release."""


@dataclass(frozen=True)
class CandidateReport:
    root: str
    required_paths: int
    source_files_scanned: int
    api_operations: int
    frontend_bytes: int | None
    status: str = "PASS"


def _source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in SKIP_PARTS for part in path.parts):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in {"Dockerfile", "Makefile"}:
            files.append(path)
    return sorted(files)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise CandidateError(f"source must be UTF-8 text: {path}") from exc


def _assert_required(root: Path) -> None:
    missing = [relative for relative in REQUIRED_PATHS if not (root / relative).is_file()]
    if missing:
        raise CandidateError(f"required candidate paths missing: {', '.join(missing)}")


def _assert_no_sensitive_material(root: Path, files: list[Path]) -> None:
    forbidden_names = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in SKIP_PARTS for part in path.parts):
            continue
        name = path.name.lower()
        if (
            ".tfstate" in name
            or ".tfplan" in name
            or name.endswith((".pem", ".key"))
            or (name.startswith(".env.") and name != ".env.example")
        ):
            forbidden_names.append(path.relative_to(root).as_posix())
    if forbidden_names:
        raise CandidateError(f"forbidden candidate files: {', '.join(sorted(forbidden_names))}")

    findings: list[str] = []
    for path in files:
        text = _read(path)
        relative = path.relative_to(root).as_posix()
        account_text = (
            TERRAFORM_LOCK_CHECKSUM.sub(
                lambda match: match.group(1) + match.group(2) if match.group(1) is not None else match.group(0),
                text,
            )
            if relative == "terraform/.terraform.lock.hcl"
            else text
        )
        for label, pattern in (
            ("AWS account id", ACCOUNT_ID),
            ("AWS access key", ACCESS_KEY),
            ("private key", PRIVATE_KEY),
            ("email address / possible PII", EMAIL_ADDRESS),
            ("literal password", PASSWORD_ASSIGNMENT),
        ):
            if pattern.search(account_text if pattern is ACCOUNT_ID else text):
                findings.append(f"{relative}: {label}")
    if findings:
        raise CandidateError("sensitive or private material detected: " + "; ".join(findings))


def _openapi_operations(text: str) -> frozenset[str]:
    operations: set[str] = set()
    current: str | None = None
    for line in text.splitlines():
        path_match = re.match(r"^  (/[^:]+):\s*$", line)
        if path_match:
            current = path_match.group(1).replace("{applicationId}", "{id}")
            continue
        method_match = re.match(r"^    (get|post):\s*$", line)
        if current and method_match:
            operations.add(f"{method_match.group(1).upper()} {current}")
    return frozenset(operations)


def _openapi_operation_blocks(text: str) -> dict[str, str]:
    """Extract operation blocks without introducing a runtime YAML dependency."""

    lines = text.splitlines()
    current_path: str | None = None
    starts: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        path_match = re.match(r"^  (/[^:]+):\s*$", line)
        if path_match:
            current_path = path_match.group(1).replace("{applicationId}", "{id}")
            continue
        method_match = re.match(r"^    (get|post):\s*$", line)
        if current_path and method_match:
            starts.append((f"{method_match.group(1).upper()} {current_path}", index))

    blocks: dict[str, str] = {}
    for position, (operation, start) in enumerate(starts):
        end = starts[position + 1][1] if position + 1 < len(starts) else len(lines)
        # A new path or components section may occur before the next method.
        for index in range(start + 1, end):
            if re.match(r"^(?:  /|components:)", lines[index]):
                end = index
                break
        blocks[operation] = "\n".join(lines[start:end])
    return blocks


def _assert_openapi(root: Path) -> int:
    text = _read(root / "openapi.yaml")
    operations = _openapi_operations(text)
    if operations != EXPECTED_OPERATIONS:
        missing = sorted(EXPECTED_OPERATIONS - operations)
        extra = sorted(operations - EXPECTED_OPERATIONS)
        raise CandidateError(f"OpenAPI operation drift; missing={missing}, extra={extra}")
    forbidden_schema_terms = ("taskToken", "encryptedToken", "ciphertext", "tenantHeader")
    present = [
        term
        for term in forbidden_schema_terms
        if re.search(rf"(?m)^\s+{re.escape(term)}:\s*$", text, re.IGNORECASE)
    ]
    if present:
        raise CandidateError(f"OpenAPI exposes internal/security fields: {present}")

    blocks = _openapi_operation_blocks(text)
    if set(blocks) != EXPECTED_OPERATIONS:
        raise CandidateError("OpenAPI operation blocks could not be extracted exactly")
    missing_internal_error = [
        operation
        for operation, block in blocks.items()
        if re.search(r'(?m)^        "500":\s*$', block) is None
    ]
    if missing_internal_error:
        raise CandidateError(
            "OpenAPI operations missing sanitized 500 response: "
            + ", ".join(sorted(missing_internal_error))
        )
    approval = blocks["POST /approvals/{id}/decision"]
    if re.search(r'(?m)^        "202":\s*$', approval) is None:
        raise CandidateError("Manager decision must be documented as 202 Accepted")

    manager_schema = text.split("    ManagerDecision:", 1)[-1].split(
        "    DecisionAccepted:", 1
    )[0]
    required_manager_terms = (
        "oneOf:",
        "const: APPROVE",
        "enum: [CAPACITY_CONFIRMED, POLICY_EXCEPTION_APPROVED]",
        "const: REJECT",
        "enum: [CAPACITY_NOT_AVAILABLE, INCOMPLETE_COMMERCIAL_CASE]",
    )
    absent_manager_terms = [term for term in required_manager_terms if term not in manager_schema]
    if absent_manager_terms or any(
        internal in manager_schema for internal in ("AUTO_APPROVED", "MANAGER_APPROVAL_TIMEOUT")
    ):
        raise CandidateError("OpenAPI ManagerDecision allowlist is not exact")

    health_schema = text.split("    Health:", 1)[-1].split("    Me:", 1)[0]
    if "enum: [PREPARED, ACTIVE, EXPIRED]" not in health_schema or 'type: "null"' not in health_schema:
        raise CandidateError("OpenAPI Health must distinguish PREPARED and nullable pre-T0 expiry")
    return len(operations)


def _assert_workflow(root: Path) -> None:
    try:
        workflow = json.loads(_read(root / "workflow.asl.json"))
    except json.JSONDecodeError as exc:
        raise CandidateError(f"workflow.asl.json is invalid JSON: {exc}") from exc
    encoded = json.dumps(workflow, sort_keys=True)
    required = ("waitForTaskToken", "TimeoutSeconds", "vehicleCount")
    absent = [value for value in required if value not in encoded]
    if absent:
        raise CandidateError(f"workflow is missing callback/threshold contract: {absent}")
    if workflow.get("TimeoutSeconds") != 3600:
        raise CandidateError("workflow global timeout must remain exactly 3,600 seconds")
    task_states = [
        state for state in workflow.get("States", {}).values() if state.get("Type") == "Task"
    ]
    callback_states = [
        state
        for state in task_states
        if state.get("Resource") == "arn:aws:states:::lambda:invoke.waitForTaskToken"
    ]
    if len(callback_states) != 1 or callback_states[0].get("TimeoutSeconds") != 3540:
        raise CandidateError("workflow callback must reserve 60 seconds for terminal handling")
    for state in task_states:
        retries = state.get("Retry")
        if not isinstance(retries, list) or not retries:
            raise CandidateError("every workflow task must have a bounded retry")
        for retry in retries:
            if not 1 <= retry.get("MaxAttempts", 0) <= 3 or retry.get("JitterStrategy") != "FULL":
                raise CandidateError("workflow retries must remain bounded and jittered")
    callback_errors = {
        error
        for catcher in callback_states[0].get("Catch", [])
        for error in catcher.get("ErrorEquals", [])
    }
    if not {"States.Timeout", "States.ALL"}.issubset(callback_errors):
        raise CandidateError("workflow callback must separate timeout and technical failure")
    technical_failure = workflow.get("States", {}).get("TechnicalCallbackFailure", {})
    if technical_failure.get("Type") != "Fail":
        raise CandidateError("technical callback failures must never become business rejection")


def _terraform_text(root: Path) -> str:
    return "\n".join(_read(path) for path in sorted((root / "terraform").glob("*.tf")))


def _assert_terraform(root: Path) -> None:
    text = _terraform_text(root)
    must_contain = (
        '"sales_demo"',
        "aws_cognito_user_pool",
        "aws_cognito_user_pool_client",
        "aws_apigatewayv2_authorizer",
        "aws_sfn_state_machine",
        "aws_cloudfront_origin_access_control",
        "aws_dynamodb_table",
        "aws_scheduler_schedule",
        "authorization_scopes",
        "refresh_token_rotation",
        'status                   = { S = "PREPARED" }',
        "application_role_permissions_boundary_arn",
        'type                              = "CUSTOMER_MANAGED_KMS_KEY"',
        "APPROVALS-TerraformDeploymentRole",
        '"dynamodb:Scan"',
    )
    absent = [needle for needle in must_contain if needle not in text]
    if absent:
        raise CandidateError(f"Terraform contract missing: {absent}")
    required_assignments = {
        "billing_mode": '"PROVISIONED"',
        "read_capacity": "5",
        "write_capacity": "5",
        "memory_size": "256",
        "timeout": "10",
        "retention_in_days": "14",
    }
    missing_assignments = [
        f"{name}={value}"
        for name, value in required_assignments.items()
        if re.search(rf"(?m)^\s*{name}\s*=\s*{re.escape(value)}\s*$", text) is None
    ]
    if missing_assignments:
        raise CandidateError(f"Terraform assignments missing: {missing_assignments}")
    forbidden = (
        "reserved_concurrent_executions",
        'generate_secret = true',
        'allow_admin_create_user_only = false',
        'resource "aws_iam_policy" "runtime_boundary"',
    )
    present = [needle for needle in forbidden if needle in text]
    if present:
        raise CandidateError(f"Terraform contract contains forbidden settings: {present}")

    if text.count(
        "permissions_boundary = var.application_role_permissions_boundary_arn"
    ) != 4:
        raise CandidateError("all four runtime roles must use the external guardrails boundary")
    pitr = re.search(r"point_in_time_recovery\s*\{(?P<body>.*?)\n\s*\}", text, re.DOTALL)
    if pitr is None or re.search(r"(?m)^\s*enabled\s*=\s*false\s*$", pitr.group("body")) is None:
        raise CandidateError("ephemeral DynamoDB PITR must stay disabled for inventory-zero teardown")
    if not re.search(r"refresh_token_validity\s*=\s*8", text) or not re.search(
        r'refresh_token\s*=\s*"days"', text
    ):
        raise CandidateError("Cognito refresh validity must cover the eight-day pilot")
    if "connect-src 'self' ${aws_apigatewayv2_api.sales.api_endpoint}" not in text:
        raise CandidateError("CloudFront CSP must bind bearer calls to the exact API")
    if not re.search(r"allow_credentials\s*=\s*false", text):
        raise CandidateError("HTTP API CORS must never allow browser credentials")

    activation = _read(root / "terraform" / "activate_pilot.py")
    for marker in (
        "PREPARED -> ACTIVE",
        "ACTIVE_WINDOW_HOURS = 192",
        "verify_prepared",
        "rollback_schedule",
        "publish_frontend.publish",
        "commit_active",
    ):
        if marker not in activation:
            raise CandidateError("post-readiness activator lost a fail-closed phase")
    publisher = _read(root / "terraform" / "publish_frontend.py")
    for marker in (
        '"status": "PREPARING"',
        '"status": "PUBLISHED"',
        "expected_live",
        "list_object_versions",
        "abort_multipart_upload",
        '"no-store"',
    ):
        if marker not in publisher:
            raise CandidateError("frontend publisher lost binding/recovery/cleanup controls")

    varfiles = sorted(
        path.relative_to(root).as_posix()
        for path in (root / "terraform").glob("*.tfvars")
        if path.name != "example.tfvars"
    )
    if varfiles:
        raise CandidateError(f"real tfvars must not be versioned: {varfiles}")


def _assert_frontend(root: Path) -> int | None:
    source = "\n".join(_read(path) for path in sorted((root / "web" / "src").rglob("*")) if path.is_file())
    required = (
        "Demostración · datos sintéticos · sin PII real",
        "PREPARED",
        "PENDING_MANAGER",
        "CONTRACT_ACTIVE",
        "Authorization",
        "code_challenge",
        "deploymentBinding",
        "getValidAccessToken",
        "history.replaceState",
    )
    absent = [needle for needle in required if needle not in source]
    if absent:
        raise CandidateError(f"frontend contract missing: {absent}")
    if re.search(r"(?i)(taskToken|encryptedToken|ciphertext)", source):
        raise CandidateError("frontend source references an internal callback token")

    service_worker = _read(root / "web" / "public" / "sw.js")
    for marker in ("url.search", "runtime-config.json", "/auth/callback", "event.request.mode === 'navigate'"):
        if marker not in service_worker:
            raise CandidateError("service worker lost a cache-safety invariant")
    http_client = _read(root / "web" / "src" / "api" / "http.ts")
    if http_client.count("response.status === 401") != 2 or "accessToken(true)" not in http_client:
        raise CandidateError("HTTP client must perform one bounded refresh retry on 401")

    package = json.loads(_read(root / "web" / "package.json"))
    scripts = package.get("scripts", {})
    for name in ("build", "typecheck", "test", "test:e2e"):
        if not scripts.get(name):
            raise CandidateError(f"frontend package is missing script {name}")

    dist = root / "web" / "dist"
    if not dist.is_dir():
        return None
    total = sum(path.stat().st_size for path in dist.rglob("*") if path.is_file())
    if total >= 10 * 1024 * 1024:
        raise CandidateError(f"frontend build exceeds 10 MiB: {total} bytes")
    return total


def validate(root: Path) -> CandidateReport:
    root = root.resolve()
    if not root.is_dir():
        raise CandidateError(f"candidate root does not exist: {root}")
    _assert_required(root)
    files = _source_files(root)
    _assert_no_sensitive_material(root, files)
    operations = _assert_openapi(root)
    _assert_workflow(root)
    _assert_terraform(root)
    frontend_bytes = _assert_frontend(root)
    return CandidateReport(
        root=str(root),
        required_paths=len(REQUIRED_PATHS),
        source_files_scanned=len(files),
        api_operations=operations,
        frontend_bytes=frontend_bytes,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Directorio sales_demo (default: inferido desde el script).",
    )
    args = parser.parse_args(argv)
    try:
        report = validate(args.root)
    except CandidateError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
