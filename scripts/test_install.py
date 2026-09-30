"""Static configuration and isolated installer verification (no Codex model calls)."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest

REPO = Path(__file__).resolve().parents[1]
PROFILE = {
    'scout': ('gpt-6-luna', 'medium', 'fast'),
    'worker_luna': ('gpt-6-luna', 'high', 'fast'),
    'worker_sol': ('gpt-6.1-sol', 'high', 'default'),
    'controller_sol': ('gpt-6.1-sol', 'high', 'default'),
    'expert': ('gpt-6.1-sol', 'xhigh', 'default'),
    'controller_astra': ('gpt-6-astra', 'high', 'default'),
}

def toml(path):
    return tomllib.loads(path.read_text(encoding='utf-8-sig'))

class ConfigurationTests(unittest.TestCase):
    def test_root_and_independent_role_tiers(self):
        config = toml(REPO / 'config.example.toml')
        self.assertEqual((config['model'], config['model_reasoning_effort'], config['service_tier']),
                         ('gpt-6-luna', 'medium', 'fast'))
        self.assertTrue(config['features']['fast_mode'])
        for role, expected in PROFILE.items():
            with self.subTest(role=role):
                agent = toml(REPO / config['agents'][role]['config_file'])
                self.assertEqual((agent['model'], agent['model_reasoning_effort'], agent['service_tier']), expected)
                self.assertTrue(agent['developer_instructions'])

class InstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pwsh = shutil.which('pwsh')
        if cls.pwsh is None:
            bundled = Path(sys.executable).parent.parent / 'native' / 'powershell' / 'pwsh.exe'
            if bundled.exists():
                cls.pwsh = str(bundled)
        if cls.pwsh is None:
            raise unittest.SkipTest('PowerShell 7 is required for installer integration tests')

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='codex-sol61-install-')
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.original = '''# old managed defaults
model = "gpt-6-sol"
model_reasoning_effort = "medium"
service_tier = "priority"
notify = ["local-notify", "turn-ended"]
custom_top_level = "preserved"
[features]
multi_agent = false
[agents]
default_subagent_model = "old-model"
[mcp_servers.example]
url = "https://example.invalid/mcp"
[projects."C:/test project"]
trust_level = "trusted"
'''
        (self.home / 'config.toml').write_text(self.original, encoding='utf-8')
        (self.home / 'AGENTS.md').write_text('existing personal instructions\n', encoding='utf-8')

    def install(self, *options):
        result = subprocess.run([self.pwsh, '-NoProfile', '-File', str(REPO / 'scripts' / 'install.ps1'),
                                 '-CodexHome', str(self.home), *options], capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_dry_run_does_not_write(self):
        before = {p.name: p.read_bytes() for p in self.home.iterdir()}
        self.install('-InstallRootInstructions', '-WhatIf')
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.home.iterdir()})

    def test_install_preserves_unmanaged_settings_and_explicit_tiers(self):
        self.install('-InstallRootInstructions')
        config = toml(self.home / 'config.toml')
        self.assertEqual(config['notify'], ['local-notify', 'turn-ended'])
        self.assertEqual(config['custom_top_level'], 'preserved')
        self.assertEqual(config['mcp_servers']['example']['url'], 'https://example.invalid/mcp')
        self.assertEqual(config['projects']['C:/test project']['trust_level'], 'trusted')
        self.assertEqual(config['service_tier'], 'fast')
        for role, expected in PROFILE.items():
            with self.subTest(role=role):
                agent = toml(self.home / config['agents'][role]['config_file'])
                self.assertEqual((agent['model'], agent['model_reasoning_effort'], agent['service_tier']), expected)
        self.assertEqual((self.home / 'AGENTS.md').read_bytes(), (REPO / 'AGENTS.md').read_bytes())
        backups = list(self.home.glob('config.toml.backup-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding='utf-8'), self.original)
        self.assertEqual(len(list(self.home.glob('AGENTS.md.backup-*'))), 1)
        for name in ('cost-weights.json', 'codex-credit-rates.json'):
            rates = json.loads((self.home / 'metrics' / name).read_text(encoding='utf-8-sig'))
            self.assertIn('gpt-6.1-sol', rates['models'])

    def test_default_install_keeps_root_instructions(self):
        self.install()
        self.assertEqual((self.home / 'AGENTS.md').read_text(encoding='utf-8'), 'existing personal instructions\n')
        self.assertEqual(list(self.home.glob('AGENTS.md.backup-*')), [])

if __name__ == '__main__':
    unittest.main()
