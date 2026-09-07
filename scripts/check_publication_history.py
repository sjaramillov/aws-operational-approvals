"""Review private paths in every committed tree reachable from local Git refs."""
from pathlib import Path
import json
import subprocess

from publication_policy import private_path_reason


def audit_history(root: Path) -> dict:
    commits = subprocess.check_output(['git', 'rev-list', '--all'], cwd=root, text=True).splitlines()
    findings = {}
    for commit in commits:
        tree = subprocess.check_output(['git', 'ls-tree', '--full-tree', '-r', '-z', commit], cwd=root)
        for record in filter(None, tree.split(b'\0')):
            metadata, raw_path = record.split(b'\t', 1)
            mode = metadata.split(b' ', 1)[0]
            path = raw_path.decode('utf-8', errors='surrogateescape')
            reason = private_path_reason(path)
            if mode == b'120000':
                reason = 'Symlink requires publication review'
            elif mode == b'160000':
                reason = 'Submodule requires publication review'
            if reason:
                findings.setdefault((path, reason), {'path': path, 'reason': reason, 'commit': commit})
    return {'commits_scanned': len(commits), 'findings': list(findings.values())}


if __name__ == '__main__':
    result = audit_history(Path(__file__).resolve().parents[1])
    print(json.dumps(result, ensure_ascii=True, indent=2))
    raise SystemExit(bool(result['findings']))
