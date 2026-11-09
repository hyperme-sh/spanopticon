import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

AZURE = Path(__file__).resolve().parents[1] / "verification" / "azure"


class AzureBackup(unittest.TestCase):
    def test_rule_scope_and_age(self):
        rule = json.loads((AZURE / "retention-rule.json").read_text())["definition"]
        self.assertEqual(rule["filters"], {"blobTypes": ["blockBlob"],
                                          "prefixMatch": ["verification-backups/cold/verification-"]})
        for target, age in (("baseBlob", "Modification"), ("version", "Creation"), ("snapshot", "Creation")):
            self.assertEqual(rule["actions"][target]["delete"], {f"daysAfter{age}GreaterThan": 30})

    def test_fetch_exact_version_verify_before_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            az = root / "az"
            az.write_text("""#!/usr/bin/env python3
import sys
from pathlib import Path
args = sys.argv[1:]
assert args[:3] == ['storage', 'blob', 'download']
for key, value in [('account-name','account'), ('container-name','container'), ('name','blob'),
                   ('version-id','retained-version'), ('auth-mode','login')]:
    assert args[args.index('--' + key) + 1] == value
Path(args[args.index('--file') + 1]).write_bytes(b'backup')
""")
            az.chmod(0o700)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"])
            dest = root / "restored"
            cmd = ["bash", str(AZURE / "fetch-version.sh"), "account", "container", "blob", "retained-version"]
            digest = hashlib.sha256(b"backup").hexdigest()
            for expected, success in [("0" * 64, False), (digest, True), (digest, False)]:
                result = subprocess.run(cmd + [expected, str(dest)], env=env, capture_output=True)
                self.assertEqual(result.returncode == 0, success, result.stderr)
                self.assertEqual(dest.exists(), expected == digest)
            self.assertEqual(dest.read_bytes(), b"backup")
            self.assertFalse(list(root.glob(".verification-restore.*")))
