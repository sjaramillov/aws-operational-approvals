from __future__ import annotations

import tempfile
import unittest
import shutil
from pathlib import Path

from sales_demo.scripts.validate_candidate import (
    CandidateError,
    EXPECTED_OPERATIONS,
    _assert_openapi,
    _assert_terraform,
    _assert_workflow,
    _assert_no_sensitive_material,
    _openapi_operations,
    _source_files,
    validate,
)


class CandidateContractTest(unittest.TestCase):
    def test_complete_candidate_contract_is_green(self) -> None:
        root = Path(__file__).resolve().parents[1]
        report = validate(root)
        self.assertEqual(report.status, "PASS")
        self.assertEqual(report.api_operations, 8)

    def test_expected_openapi_operations_are_exact(self) -> None:
        root = Path(__file__).resolve().parents[1]
        operations = _openapi_operations((root / "openapi.yaml").read_text(encoding="utf-8"))
        self.assertEqual(operations, EXPECTED_OPERATIONS)

    def test_sensitive_scanner_rejects_account_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            account_id = "123456" + "789012"
            (root / "unsafe.md").write_text(f"account = {account_id}\n", encoding="utf-8")
            with self.assertRaisesRegex(CandidateError, "AWS account id"):
                _assert_no_sensitive_material(root, _source_files(root))

    def test_sensitive_scanner_rejects_ignored_env_variants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".env.demo").write_text("VITE_MODE=demo\n", encoding="utf-8")
            with self.assertRaisesRegex(CandidateError, "forbidden candidate files"):
                _assert_no_sensitive_material(root, _source_files(root))

    def test_sensitive_scanner_rejects_email_pii(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "unsafe.md").write_text(
                "contact = person" + "@" + "example.com\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CandidateError, "possible PII"):
                _assert_no_sensitive_material(root, _source_files(root))

    def test_openapi_gate_rejects_an_operation_without_sanitized_500(self) -> None:
        source = Path(__file__).resolve().parents[1] / "openapi.yaml"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            text = source.read_text(encoding="utf-8").replace(
                '        "500":\n          $ref: "#/components/responses/InternalError"',
                '        "599":\n          $ref: "#/components/responses/InternalError"',
                1,
            )
            (root / "openapi.yaml").write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(CandidateError, "missing sanitized 500"):
                _assert_openapi(root)

    def test_workflow_gate_rejects_unbounded_global_timeout(self) -> None:
        source = Path(__file__).resolve().parents[1] / "workflow.asl.json"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            text = source.read_text(encoding="utf-8").replace(
                '"TimeoutSeconds": 3600', '"TimeoutSeconds": 7200', 1
            )
            (root / "workflow.asl.json").write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(CandidateError, "3,600"):
                _assert_workflow(root)

    def test_terraform_gate_rejects_pitr_inventory_residue(self) -> None:
        source = Path(__file__).resolve().parents[1] / "terraform"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(source, root / "terraform", ignore=shutil.ignore_patterns(".terraform", "__pycache__"))
            storage = root / "terraform" / "storage.tf"
            storage.write_text(
                storage.read_text(encoding="utf-8").replace("enabled = false", "enabled = true", 1),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CandidateError, "PITR"):
                _assert_terraform(root)


if __name__ == "__main__":
    unittest.main()
