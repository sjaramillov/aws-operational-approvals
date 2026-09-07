#!/usr/bin/env python3
"""Construye ZIPs Lambda reproducibles sin credenciales ni acceso de red.

Ambas funciones comparten el mismo paquete porque sus entrypoints importan un
dominio y adaptadores comunes. Incluyen los avisos legales del proyecto, pero no
empaquetan el SDK AWS ni dependencias de terceros. Los ZIP se escriben en ``build/``,
que permanece fuera de Git, y el JSON emitido contiene únicamente rutas y
hashes de artefactos locales.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path


FIXED_ZIP_TIMESTAMP = (2026, 8, 26, 0, 0, 0)
PACKAGE_NAMES = ("api", "worker")
PACKAGE_NOTICES = ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md")
RUNTIME_MODULES = (
    "__init__.py",
    "aws_adapters.py",
    "domain.py",
    "errors.py",
    "lambda_api.py",
    "lambda_worker.py",
    "ports.py",
    "service.py",
)


class PackageError(RuntimeError):
    """El paquete no puede ligarse de forma reproducible al código local."""


def _sources(sales_root: Path) -> list[tuple[Path, str]]:
    package_root = sales_root.parent
    candidates = [
        *(package_root / name for name in PACKAGE_NOTICES),
        sales_root / "__init__.py",
        *(sales_root / "backend" / name for name in RUNTIME_MODULES),
    ]
    files: list[tuple[Path, str]] = []
    for path in sorted(candidates):
        if not path.is_file():
            raise PackageError(f"required Lambda source is missing: {path}")
        files.append((path, path.relative_to(package_root).as_posix()))
    if not any(arcname.endswith("backend/lambda_api.py") for _, arcname in files):
        raise PackageError("API entrypoint is missing")
    if not any(arcname.endswith("backend/lambda_worker.py") for _, arcname in files):
        raise PackageError("worker entrypoint is missing")
    return files


def _write_zip(path: Path, sources: list[tuple[Path, str]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source, arcname in sources:
            info = zipfile.ZipInfo(arcname, FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _digest(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).digest()
    return {
        "path": str(path),
        "bytes": len(payload),
        "sha256": digest.hex(),
        "source_code_hash_base64": base64.b64encode(digest).decode("ascii"),
    }


def build(sales_root: Path, output_dir: Path) -> dict[str, object]:
    sales_root = sales_root.resolve()
    output_dir = output_dir.resolve()
    sources = _sources(sales_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    reports: dict[str, object] = {}
    for name in PACKAGE_NAMES:
        destination = output_dir / f"sales-{name}.zip"
        temporary = output_dir / f".{destination.name}.tmp"
        _write_zip(temporary, sources)
        temporary.replace(destination)
        reports[name] = _digest(destination)
    if (output_dir / "sales-api.zip").read_bytes() != (output_dir / "sales-worker.zip").read_bytes():
        raise PackageError("shared Lambda packages are not byte-identical")
    return {"status": "PASS", "source_files": len(sources), "packages": reports}


def verify(sales_root: Path, output_dir: Path) -> dict[str, object]:
    output_dir = output_dir.resolve()
    probe = output_dir.parent / f".{output_dir.name}-verification"
    if probe.exists():
        shutil.rmtree(probe)
    try:
        expected = build(sales_root, probe)
        reports: dict[str, object] = {}
        for name in PACKAGE_NAMES:
            actual_path = output_dir / f"sales-{name}.zip"
            if not actual_path.is_file():
                raise PackageError(f"package is missing: {actual_path}")
            actual = _digest(actual_path)
            expected_packages = expected["packages"]
            if not isinstance(expected_packages, dict):
                raise PackageError("internal package report is invalid")
            wanted = expected_packages[name]
            if not isinstance(wanted, dict) or actual["sha256"] != wanted.get("sha256"):
                raise PackageError(f"package is not reproducible: {actual_path}")
            reports[name] = actual
        return {"status": "PASS", "packages": reports}
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "verify"))
    parser.add_argument("--sales-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "build",
    )
    args = parser.parse_args(argv)
    try:
        report = (
            build(args.sales_root, args.output_dir)
            if args.command == "build"
            else verify(args.sales_root, args.output_dir)
        )
    except PackageError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
