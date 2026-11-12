"""Service configuration uses the publisher's shared reference contract."""
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import new_plane
import service_channels

ASSURANCE = {'mode': 'kyc', 'providers': {'kyc': {
    'url': 'https://payment.example.invalid', 'credential_secret': 'kyc-client'}}}


class ServiceChannelTests(unittest.TestCase):
    def test_shared_positive_and_negative_vectors(self):
        vectors = json.loads((ROOT / 'tests/service-reference-vectors.json').read_text())
        for image in vectors['accepted']:
            documents = new_plane.manifests('api', image, ASSURANCE, repository=vectors['repository'])
            service = documents['compose.yaml']['services']['api']
            container = documents['kubernetes.json']['items'][1]['spec']['template']['spec']['containers'][0]
            self.assertEqual(service['image'], image)
            self.assertEqual(container['image'], image)
            self.assertEqual(service['pull_policy'], 'always')
            self.assertEqual(container['imagePullPolicy'], 'Always')
        for image in vectors['rejected']:
            with self.subTest(image=image), self.assertRaises(ValueError):
                new_plane.manifests('api', image, ASSURANCE, repository=vectors['repository'])

    def test_peer_patch_versions_are_independent(self):
        for name, tag in [('api', '0.6.1'), ('worker', '0.6.9')]:
            repo = 'ghcr.io/hyperme-sh/' + name
            data = new_plane.manifests(name, repo+':'+tag, ASSURANCE, repository=repo)
            self.assertEqual(data['compose.yaml']['services'][name]['image'], repo+':'+tag)

    def test_vendored_contract_matches_recorded_hash(self):
        source = json.loads((ROOT / 'scripts/service-channels-source.json').read_text())
        for name, expected in source['files'].items():
            self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(), expected)
