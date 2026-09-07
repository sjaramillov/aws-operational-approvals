from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from sales_demo.scripts.build_lambda_packages import build, verify


SALES_ROOT = Path(__file__).resolve().parents[1]


class LambdaPackageTests(unittest.TestCase):
    def test_packages_are_reproducible_and_contain_both_entrypoints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "build"
            first = build(SALES_ROOT, output)
            first_packages = first["packages"]
            self.assertIsInstance(first_packages, dict)
            first_hash = first_packages["api"]["sha256"]
            second = build(SALES_ROOT, output)
            second_packages = second["packages"]
            self.assertEqual(first_hash, second_packages["api"]["sha256"])
            self.assertEqual(
                second_packages["api"]["sha256"],
                second_packages["worker"]["sha256"],
            )
            with zipfile.ZipFile(output / "sales-api.zip") as archive:
                names = set(archive.namelist())
            self.assertIn("sales_demo/backend/lambda_api.py", names)
            self.assertIn("sales_demo/backend/lambda_worker.py", names)
            self.assertNotIn("sales_demo/backend/memory.py", names)
            self.assertNotIn("sales_demo/backend/local_server.py", names)
            self.assertEqual(verify(SALES_ROOT, output)["status"], "PASS")

    def test_verifier_rejects_tampered_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "build"
            build(SALES_ROOT, output)
            with (output / "sales-worker.zip").open("ab") as stream:
                stream.write(b"tampered")
            with self.assertRaisesRegex(RuntimeError, "not reproducible"):
                verify(SALES_ROOT, output)


if __name__ == "__main__":
    unittest.main()
