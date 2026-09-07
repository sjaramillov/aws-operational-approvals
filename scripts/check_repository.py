"""Check tracked publication contents, local documentation links and provenance hashes."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
errors = []
count = 0
for rel in filter(None, paths):
    path = ROOT / rel
    if path.is_symlink():
        errors.append(f'Symlink requires publication review: {rel}')
        continue
    if not path.is_file():
        errors.append(f'Missing tracked file: {rel}')
        continue
    size_limit = 3_000_000 if rel.startswith('docs/visuals/') and path.suffix == '.png' else 2_000_000
    if path.stat().st_size > size_limit:
        errors.append(f'File exceeds {size_limit} byte publication limit: {rel}')
    if any(x in path.relative_to(ROOT).parts for x in ('.venv', 'node_modules', '.sf', '.sfdx', '.terraform')):
        errors.append(f'Private or generated directory tracked: {rel}')
    if path.name.startswith('.env') and not path.name.endswith('.example'):
        errors.append(f'Environment file tracked: {rel}')
    if path.suffix in ('.pem', '.key', '.tfstate', '.tfplan'):
        errors.append(f'Private runtime file tracked: {rel}')
    if path.suffix == '.md':
        for target in re.findall(r'\[[^\]\n]+\]\(([^)]+)\)', path.read_text()):
            target = target.split(' "', 1)[0].strip('<>')
            if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', target) or target.startswith('#'):
                continue
            target = unquote(target.split('#', 1)[0])
            if target.startswith('/') or not (path.parent / target).exists():
                errors.append(f'Nonportable or broken Markdown link: {rel} -> {target}')
for manifest in (ROOT / 'docs').rglob('*manifest.json'):
    payload = json.loads(manifest.read_text())
    for item in payload.get('files', []):
        rel = item.get('destination_path', item.get('destination', item.get('path')))
        sha = item.get('destination_sha256', item.get('sha256'))
        if rel is None or sha is None:
            continue
        target = ROOT / rel
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != sha:
            errors.append(f'Provenance destination hash mismatch: {rel}')
        count += 1
for error in errors:
    print(error)
print(f'{len(list(filter(None, paths)))} tracked files; {count} provenance hashes; {len(errors)} errors')
raise SystemExit(bool(errors))
