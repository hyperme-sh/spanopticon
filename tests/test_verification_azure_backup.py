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

    def test_recover_retired_subject_from_previous_version(self):
        from unittest.mock import patch
        from verification.qualify_azure import qualify
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            work.mkdir()
            az = root / "az"
            az.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
def arg(name):
    return args[args.index(name) + 1]
remote = Path(os.environ['FIXTURE_ROOT'])
assert arg('--auth-mode') == 'login'
path = Path(arg('--file'))
if args[2] == 'upload':
    if '--if-none-match' in args:
        assert arg('--if-none-match') == '*'
        version = 'one'
    else:
        assert arg('--if-match') == 'etag-one'
        version = 'two'
    (remote / version).write_bytes(path.read_bytes())
    print(json.dumps({'version_id': version, 'etag': 'etag-' + version}))
else:
    assert args[2] == 'download' and arg('--version-id') == 'one'
    path.write_bytes((remote / 'one').read_bytes())
""")
            az.chmod(0o700)
            with patch.dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"], FIXTURE_ROOT=tmp):
                receipt = qualify(work, "account", "container")
            self.assertEqual(receipt["restored_version"], "one")
            self.assertEqual(receipt["current_version"], "two")
            self.assertEqual(receipt["sha256"], hashlib.sha256((root / "one").read_bytes()).hexdigest())
