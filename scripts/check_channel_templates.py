"""Offline Helm rendering against the shared service reference vectors."""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    vectors = json.loads((ROOT / 'tests/service-reference-vectors.json').read_text())
    for accepted, values in [(True, vectors['accepted']), (False, vectors['rejected'])]:
        for image in values:
            result = subprocess.run(['helm', 'template', 'channel-contract',
                str(ROOT / 'profiles/minimum/chart'), '--set-string',
                'imageRepository=' + vectors['repository'], '--set-string', 'image=' + image,
                '--set', 'tenant=contract,existingSecret=contract'], capture_output=True, text=True)
            if (result.returncode == 0) != accepted:
                raise ValueError('Helm reference validation differs from the shared contract')
            if accepted and ('imagePullPolicy: Always' not in result.stdout
                             or json.dumps(image) not in result.stdout):
                raise ValueError('Helm changed the runtime reference or pull policy')


if __name__ == '__main__':
    main()
