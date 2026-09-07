from __future__ import annotations

import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from sales_demo.scripts.build_lambda_packages import PackageError, build, verify


SALES_ROOT = Path(__file__).resolve().parents[1]


class LambdaPackageTests(unittest.TestCase):
    def _copy_package_sources(self, root: Path) -> Path:
        sales_root = root / "sales_demo"
        shutil.copytree(SALES_ROOT / "backend", sales_root / "backend", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(SALES_ROOT / "__init__.py", sales_root / "__init__.py")
        for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
            shutil.copyfile(SALES_ROOT.parent / name, root / name)
        return sales_root

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
                for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
                    self.assertEqual(archive.read(name), (SALES_ROOT.parent / name).read_bytes())
            self.assertIn("sales_demo/backend/lambda_api.py", names)
            self.assertIn("sales_demo/backend/lambda_worker.py", names)
            self.assertNotIn("sales_demo/backend/memory.py", names)
            self.assertNotIn("sales_demo/backend/local_server.py", names)
            self.assertEqual(len(names), 12)
            self.assertFalse(any(name.startswith(("boto3/", "botocore/")) for name in names))
            self.assertEqual(verify(SALES_ROOT, output)["status"], "PASS")

    def test_build_requires_each_project_notice(self) -> None:
        for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
            with self.subTest(notice=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                sales_root = self._copy_package_sources(root)
                (root / name).unlink()
                with self.assertRaisesRegex(PackageError, "required Lambda source is missing"):
                    build(sales_root, root / "build")

    def test_verifier_rejects_missing_or_modified_packaged_notice(self) -> None:
        for modification in ("remove", "replace"):
            with self.subTest(modification=modification), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "build"
                build(SALES_ROOT, output)
                path = output / "sales-worker.zip"
                with zipfile.ZipFile(path) as archive:
                    entries = [(info, archive.read(info.filename)) for info in archive.infolist()]
                with zipfile.ZipFile(path, "w") as archive:
                    for info, payload in entries:
                        if info.filename == "NOTICE":
                            if modification == "remove":
                                continue
                            payload = b"Modified notice\n"
                        archive.writestr(info, payload)
                with self.assertRaisesRegex(PackageError, "not reproducible"):
                    verify(SALES_ROOT, output)

    def test_notice_source_change_invalidates_previous_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sales_root = self._copy_package_sources(root)
            output = root / "build"
            build(sales_root, output)
            notice = root / "NOTICE"
            notice.write_text(notice.read_text(encoding="utf-8") + "Additional project attribution.\n", encoding="utf-8")
            with self.assertRaisesRegex(PackageError, "not reproducible"):
                verify(sales_root, output)

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
