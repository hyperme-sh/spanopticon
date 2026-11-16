"""Run against the declared Plane source fixture in contract CI."""
import importlib.util
import json
import os
from pathlib import Path
import unittest
from deployment.tenants import render

@unittest.skipUnless(os.environ.get('PLANE_POLICY_SOURCE'), 'Plane fixture is supplied by contract CI')
class PlanePolicyContract(unittest.TestCase):
    def test_rendered_plane_loads_current_patch_contract(self):
        spec=importlib.util.spec_from_file_location('peer_fixture',os.environ['PLANE_POLICY_SOURCE'])
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        catalog=json.loads(Path('examples/tenants/catalog.json').read_text())
        tenant=catalog['tenants'][0]
        tenant['network']='hyperme.sh'
        tenant['plane']=dict(environment='test',repository=module.REPOSITORY,version=module.VERSION,protocol_version=1)
        tenant['peers']=[dict(url='https://peer.invalid',public_key='AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=',plane=tenant['plane'])]
        value=render(catalog)[tenant['id']]['plane.json']
        policy=module.Policy(value)
        self.assertEqual(policy.verify(tenant['peers'][0]['public_key'],policy.hello()),['peer.handshake'])
