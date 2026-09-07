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

    def test_sensitive_scanner_accepts_account_like_digits_in_lock_checksums(self) -> None:
        account_id = "123456" + "789012"
        checksums = (
            "h1:" + account_id + "A" * 31 + "=",
            "zh:" + "a" * 8 + account_id + "b" * 44,
        )
        for checksum in checksums:
            with self.subTest(format=checksum[:3]), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                lock = root / "terraform" / ".terraform.lock.hcl"
                lock.parent.mkdir()
                lock.write_text(f'hashes = [\n  "{checksum}",\n]\n', encoding="utf-8")
                _assert_no_sensitive_material(root, _source_files(root))

    def test_sensitive_scanner_rejects_accounts_outside_valid_lock_checksums(self) -> None:
        account_id = "123456" + "789012"
        checksum = "zh:" + "a" * 8 + account_id + "b" * 44
        cases = {
            "assignment": f'checksum = "{checksum}"\n',
            "commented_checksum": f'# "{checksum}",\n',
            "block_comment": f'/*\n  "{checksum}",\n*/\n',
            "unterminated_comment": f'/*\n  "{checksum}",\n',
            "inline_comment": f'  "zh:{"a" * 64}", # account = {account_id}\n',
            "other_field": f'  "{checksum}",\naccount = "{account_id}"\n',
            "bare_account": f'  "{account_id}",\n',
            "short_hex": f'  "{checksum[:-1]}",\n',
            "long_hex": f'  "{checksum}a",\n',
            "invalid_hex": f'  "{checksum[:-1]}g",\n',
            "short_base64": f'  "h1:{account_id}{"A" * 30}=",\n',
            "missing_padding": f'  "h1:{account_id}{"A" * 31}",\n',
            "noncanonical_padding_bits": f'  "h1:{account_id}{"A" * 30}B=",\n',
        }
        for name, text in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                lock = root / "terraform" / ".terraform.lock.hcl"
                lock.parent.mkdir()
                lock.write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(CandidateError, "AWS account id"):
                    _assert_no_sensitive_material(root, _source_files(root))

    def test_sensitive_scanner_limits_checksum_exception_to_exact_lock_path(self) -> None:
        account_id = "123456" + "789012"
        checksum = "zh:" + "a" * 8 + account_id + "b" * 44
        for relative in (".terraform.lock.hcl", "nested/terraform/.terraform.lock.hcl", "terraform/other.hcl"):
            with self.subTest(path=relative), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f'  "{checksum}",\n', encoding="utf-8")
                with self.assertRaisesRegex(CandidateError, "AWS account id"):
                    _assert_no_sensitive_material(root, _source_files(root))

    def test_sensitive_scanner_keeps_other_detectors_on_complete_lock_text(self) -> None:
        account_id = "123456" + "789012"
        access_key = "AK" + "IA" + "B" * 16
        cases = {
            "AWS access key": '  "h1:' + access_key + "/" + account_id + "A" * 10 + '=",\n',
            "private key": "-----BEGIN " + "PRIVATE KEY-----\n",
            "possible PII": "person" + "@" + "example.com\n",
            "literal password": "password = " + '"example-only"\n',
        }
        for label, text in cases.items():
            with self.subTest(detector=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                lock = root / "terraform" / ".terraform.lock.hcl"
                lock.parent.mkdir()
                lock.write_text(f'  "zh:{"a" * 64}",\n' + text, encoding="utf-8")
                with self.assertRaisesRegex(CandidateError, label) as detected:
                    _assert_no_sensitive_material(root, _source_files(root))
                self.assertNotIn("AWS account id", str(detected.exception))

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
