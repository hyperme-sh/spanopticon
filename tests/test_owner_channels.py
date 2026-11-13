import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from owner_channels import compose


class OwnerChannelTests(unittest.TestCase):
    def setUp(self):
        self.runtimes = {'payment-service': {'env_file': ['/private/payment.env'], 'read_only': True},
                         'plane-service': {'env_file': ['/private/plane.env'], 'read_only': True}}
        self.repositories = {name: 'ghcr.io/hyperme-sh/' + name + '/runtime' for name in self.runtimes}
        self.channels = {name: repo + ':test' for name, repo in self.repositories.items()}

    def test_native_runtime_preserved_without_framework_assurance(self):
        model = compose(self.runtimes, self.channels, self.repositories)
        for name, runtime in self.runtimes.items():
            self.assertEqual(model['services'][name], {**runtime, 'image': self.channels[name], 'pull_policy': 'always'})
        model['services']['payment-service']['env_file'].append('/other')
        self.assertEqual(self.runtimes['payment-service']['env_file'], ['/private/payment.env'])

    def test_one_owner_can_move_without_peer_change(self):
        before = compose(self.runtimes, self.channels, self.repositories)
        self.channels['payment-service'] = self.repositories['payment-service'] + ':0.2'
        after = compose(self.runtimes, self.channels, self.repositories)
        self.assertEqual(before['services']['plane-service'], after['services']['plane-service'])
        self.assertNotEqual(before['services']['payment-service']['image'], after['services']['payment-service']['image'])

    def test_absent_foreign_or_digest_configuration_refused(self):
        for value in ('ghcr.io/foreign/payment:test', self.repositories['payment-service'] + '@sha256:' + 'a'*64, '', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                compose(self.runtimes, {**self.channels, 'payment-service': value}, self.repositories)
        for values in ({}, {'plane-service': self.channels['plane-service']}):
            with self.assertRaises(ValueError):
                compose(self.runtimes, values, self.repositories)
        for key in ('build', 'extends', 'provider'):
            with self.assertRaises(ValueError):
                compose({**self.runtimes, 'payment-service': {key: 'other'}}, self.channels, self.repositories)


if __name__ == '__main__':
    unittest.main()
