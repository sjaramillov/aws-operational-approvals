"""Reject common private/runtime paths even when they are force-added to Git."""
from pathlib import PurePosixPath

PRIVATE_DIRECTORIES = frozenset({
    '.venv', 'node_modules', '.sf', '.sfdx', '.terraform', '.aws', '.ssh',
    '.azure', '.gcloud', '.kube', 'wallet', 'wallets',
})
PRIVATE_FILENAMES = frozenset({
    '.netrc', '.npmrc', '.pypirc', '.git-credentials', 'credentials',
    'credentials.json', 'kubeconfig', 'id_rsa', 'id_dsa', 'id_ecdsa',
    'id_ed25519', 'cwallet.sso', 'ewallet.p12', '.ds_store', 'thumbs.db',
    'backend.s3.hcl',
})
PRIVATE_SUFFIXES = frozenset({
    '.pem', '.key', '.p12', '.pfx', '.jks', '.keystore', '.kdbx',
    '.sqlite', '.sqlite3', '.db', '.zip', '.tgz',
})


def private_path_reason(relative: str) -> str | None:
    path = PurePosixPath(relative)
    parts = tuple(part.casefold() for part in path.parts)
    name = path.name.casefold()
    if any(part in PRIVATE_DIRECTORIES or part.startswith('wallet_') for part in parts[:-1]):
        return 'Private or generated directory tracked'
    if any(parts[index:index + 2] == ('.config', 'gcloud') for index in range(len(parts) - 1)):
        return 'Cloud CLI configuration tracked'
    if name in PRIVATE_FILENAMES:
        return 'Credential or local configuration file tracked'
    if name != '.env.example' and (name.startswith('.env') or name.endswith('.env') or '.env.' in name):
        return 'Environment file tracked'
    if '.tfstate' in name or '.tfplan' in name:
        return 'Terraform state or plan tracked'
    if (name.endswith('.tfvars') or name.endswith('.tfvars.json')) and name != 'example.tfvars':
        return 'Terraform environment values tracked'
    if path.suffix.casefold() in PRIVATE_SUFFIXES or name.endswith('.tar.gz'):
        return 'Private runtime, key store or unreviewed archive tracked'
    return None
