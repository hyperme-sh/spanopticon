import copy
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import gateway_oci_successor as policy


class GatewayOciSuccessorTest(unittest.TestCase):
    def setUp(self):
        self.before = {'openapi': '3.1.0', 'info': {'title': 'Gateway', 'version': '0.1.0'},
                       'paths': {'/ready': {'get': {'security': [{'Bearer': []}]}}}}
        self.after = copy.deepcopy(self.before)
        self.after['info']['version'] = '0.2.0'

    def test_only_the_next_patch_or_minor_is_a_successor(self):
        policy.require_successor('0.2.0', '0.2.1')
        policy.require_successor('0.2.7', '0.2.8')
        policy.require_successor('0.1.7', '0.2.0')
        for candidate in ('0.1.9', '0.2.0', '0.2.2', '0.3.1', '0.4.0', '1.0.0'):
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(ValueError, 'next patch or minor'):
                    policy.require_successor('0.2.0', candidate)

    def test_same_network_contract_preserves_the_runtime_version_check(self):
        policy.compare_api(self.before, self.after, '0.1.0', '0.2.0')
        with self.assertRaisesRegex(ValueError, 'reported version'):
            policy.compare_api(self.before, self.after, '0.1.0', '0.2.1')
        self.after['paths']['/ready']['get']['security'] = []
        with self.assertRaisesRegex(ValueError, 'published OpenAPI'):
            policy.compare_api(self.before, self.after, '0.1.0', '0.2.0')

    def test_contract_export_pulls_only_the_exact_owner_image_and_runs_offline(self):
        image = policy.IMAGE_REPOSITORY + '@sha256:' + 'a' * 64
        with patch.object(policy.subprocess, 'run', return_value=SimpleNamespace(
                stdout=json.dumps(self.before).encode())) as command:
            self.assertEqual(policy.read_openapi(image), self.before)
        pull, run = (call.args[0] for call in command.call_args_list)
        self.assertEqual(pull[-1], image)
        self.assertIn(image, run)
        for flag in ('--network=none', '--cap-drop=ALL', '--pull=never', '--read-only'):
            self.assertIn(flag, run)
        with patch.object(policy.subprocess, 'run') as command:
            with self.assertRaisesRegex(ValueError, 'immutable Gateway'):
                policy.read_openapi(policy.IMAGE_REPOSITORY + ':test')
        command.assert_not_called()
