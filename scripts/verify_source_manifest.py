#!/usr/bin/env python3
"""Verify imported files without disclosing private source contents or locations."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'docs/source-manifest.json').read_text())
    failures = []
    for entry in manifest['files']:
        path = root / entry['destination_path']
        if not path.is_file() or digest(path) != entry['destination_sha256']:
            failures.append({'path': entry['destination_path'], 'kind': 'destination_mismatch'})
        if args.source_root:
            source = args.source_root / entry['source_path']
            if not source.is_file() or digest(source) != entry['source_sha256']:
                failures.append({'path': entry['destination_path'], 'kind': 'source_mismatch'})
    print(json.dumps({'status': 'FAIL' if failures else 'PASS', 'imported_files': len(manifest['files']), 'source_checked': bool(args.source_root), 'failures': failures}, indent=2))
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
