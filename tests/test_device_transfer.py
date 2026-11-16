import copy
import json
import tempfile
from pathlib import Path
import unittest
from deployment.device_transfer import compile_config, write


def configuration():
    return {'version':1, 'edgeOrigin':'https://edge.example', 'crossApplicationPairs':[],
        'clients':[{'application':'hyperme.sh','origin':'https://portal.example','tenant':'00000000-0000-0000-0000-000000000001','audience':'test-portal','provider':None},
                   {'application':'s3r.ch','origin':'https://s3r.example','tenant':'00000000-0000-0000-0000-000000000002','audience':'test-s3r','provider':'existing'}],
        'ownerFiles':{'authEnvironment':'/private/auth.env','aclPrivateDirectory':'/private/acl','s3rchEnvironment':'/private/s3r.env'}}


class DeviceTransfer(unittest.TestCase):
    def test_owner_outputs_match_consumers_without_secrets_or_image_pins(self):
        generated=compile_config(configuration())
        services=generated['compose.override.json']['services']
        portal=json.loads(services['controlplane-portal']['environment']['PORTAL_DEVICE_TRANSFER_CONFIG'])
        s3rch=json.loads(services['s3rch']['environment']['S3RCH_DEVICE_TRANSFER_CONFIG'])
        self.assertEqual(portal['bindings'][0]['scope'],generated['scopes.json']['scopes'][0])
        self.assertEqual(s3rch['scope'],generated['scopes.json']['scopes'][1])
        self.assertEqual(portal['bindings'][0]['auth'],'https://edge.example/auth/sea-device-link/v1/')
        self.assertEqual(s3rch['acl'],'https://edge.example/api/v1/acl/sociacl/device-transfer/')
        self.assertFalse(generated['capabilities.json']['ready'])
        self.assertNotIn('/private',json.dumps(generated['capabilities.json']))
        self.assertNotIn('image',json.dumps(services))
        self.assertEqual(services['auth-service']['environment']['AUTH_SEA_PAIRING_SCOPES_FILE'],services['controlplane-edge']['environment']['CPEDGE_DEVICE_TRANSFER_SCOPES_FILE'])

    def test_missing_extra_and_unsafe_configuration(self):
        for key in configuration():
            value=configuration();del value[key]
            with self.assertRaises(ValueError):compile_config(value)
        for changes in ({'edgeOrigin':'http://edge.example'},{'edgeOrigin':'https://user:secret@edge.example'}, {'crossApplicationPairs':[{'source':'s3r.ch','destination':'hyperme.sh'}]}, {'clients':[]}, {'secret':'private'}):
            with self.assertRaises(ValueError):compile_config({**configuration(),**changes})
        for changes in ({'tenant':'other'},{'tenant':'00000000-0000-0000-0000-000000000000'},{'origin':'https://portal.example/path'}, {'audience':''},{'provider':'unexpected'}, {'application':'other'}):
            value=configuration();value['clients'][0].update(changes)
            with self.assertRaises(ValueError):compile_config(value)

    def test_scope_separation_and_duplicates(self):
        value=configuration();value['clients'].append(copy.deepcopy(value['clients'][0]))
        with self.assertRaises(ValueError):compile_config(value)
        value=configuration();value['ownerFiles']['aclPrivateDirectory']='${PRIVATE}'
        with self.assertRaises(ValueError):compile_config(value)

    def test_generation_is_exclusive_and_has_no_owner_side_effects(self):
        with tempfile.TemporaryDirectory() as root:
            target=Path(root)/'output';write(configuration(),target)
            self.assertEqual({p.name for p in target.iterdir()},{'compose.override.json','scopes.json','capabilities.json'})
            with self.assertRaises(FileExistsError):write(configuration(),target)
