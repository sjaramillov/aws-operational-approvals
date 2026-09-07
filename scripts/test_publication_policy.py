"""Adversarial path cases for the publication boundary; fixtures contain no secrets."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from publication_policy import private_path_reason
from check_publication_history import audit_history


class PublicationPolicyTest(unittest.TestCase):
    def test_history_rejects_private_file_after_index_and_commit_removal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def git(*args):
                return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True)
            git('init', '-q')
            git('config', 'user.name', 'Publication boundary test')
            git('config', 'user.email', 'test@example.invalid')
            git('config', 'commit.gpgsign', 'false')
            (root / 'README.md').write_text('Synthetic fixture\n', encoding='utf-8')
            git('add', 'README.md')
            git('commit', '-qm', 'Safe fixture')
            self.assertEqual(audit_history(root)['findings'], [])
            private_file = root / 'state.tfstate.backup'
            private_file.write_text('{}\n', encoding='utf-8')
            git('add', private_file.name)
            git('commit', '-qm', 'Synthetic private path')
            git('rm', '--cached', private_file.name)
            self.assertEqual(audit_history(root)['findings'][0]['path'], private_file.name)
            private_file.unlink()
            git('commit', '-qm', 'Remove private path from current tree')
            self.assertEqual(audit_history(root)['findings'][0]['path'], private_file.name)

    def test_rejects_private_paths_and_backup_variants(self):
        paths = (
            '.aws/credentials', 'nested/.ssh/id_ed25519', '.kube/config',
            '.config/gcloud/application_default_credentials.json',
            'nested/Wallet_Pilot/tnsnames.ora', '.sf/orgs/demo.json',
            'nested/.terraform/providers/provider', 'node_modules/pkg/index.js',
            '.env', '.env.production', 'config/prod.env', 'prod.env.backup',
            'config/terraform.tfvars', 'config/demo.tfvars.json',
            'state.tfstate.backup', 'plan.tfplan.json', 'STATE.TFSTATE',
            '.npmrc', 'nested/.netrc', 'credentials.json', 'backend.s3.hcl',
            'server.PEM', 'client.p12', 'secrets.kdbx', 'export.sqlite3',
            'tenant-export.zip', 'backup.tar.gz', '.DS_Store',
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertIsNotNone(private_path_reason(path))

    def test_preserves_explicit_examples_and_authored_sources(self):
        paths = (
            'web/.env.example', 'terraform/example.tfvars',
            'terraform/.terraform.lock.hcl', 'config.example.yaml',
            'dev-secure-properties.example.yaml', 'sql/synthetic_case.sql',
            'docs/credentials.md', 'src/keys.py', 'evidence/test-results.log',
            'docs/visuals/cover.png', 'LICENSE', 'NOTICE',
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertIsNone(private_path_reason(path))

    def test_repository_gate_rejects_force_added_ignored_file(self):
        scripts = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'scripts').mkdir()
            for name in ('check_repository.py', 'publication_policy.py'):
                (root / 'scripts' / name).write_bytes((scripts / name).read_bytes())
            (root / '.gitignore').write_text('*.tfstate*\n', encoding='utf-8')
            (root / 'state.tfstate.backup').write_text('{}\n', encoding='utf-8')
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            subprocess.run(['git', '-C', str(root), 'add', '-f', 'state.tfstate.backup'], check=True)
            result = subprocess.run(
                ['python3', str(root / 'scripts' / 'check_repository.py')],
                cwd=root, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Terraform state or plan tracked: state.tfstate.backup', result.stdout)
