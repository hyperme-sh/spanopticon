"""Protocol vectors only; no services, hosts, database or provider are contacted."""
import json
from pathlib import Path
import tempfile
import unittest
from startup.contract import fingerprint
from startup.nightly import qualify,CASES,require_schedule

ROOT=Path(__file__).resolve().parents[1]


class NightlyProtocolTests(unittest.TestCase):
    def test_full_protocol_and_cross_org_denials(self):
        config=json.loads((ROOT/'startup/fixtures/mapping.json').read_text())
        orgs=[{'slug':s,'id':o['organization_id'],'tenant_id':o['tenant_id'],'roles':o['roles']} for s,o in sorted(config['binding']['organizations'].items())]
        stamp=fingerprint(config);calls=[]
        def owners(step,c,prior):
            calls.append(step)
            return {'status':'bound' if step=='auth' else 'reconciled','person_id':'person','mapping_sha256':stamp}
        def request(url,token,body=None,headers=None):
            if url.endswith('/identity'):return 200,{'status':'resolved','person_id':'person','mapping_sha256':stamp,'binding_sha256':config['binding_sha256'],
                'sea_public_key':config['binding']['sea_public_key'],'organizations':orgs,'permissions':[]}
            if url.endswith('/scoped-session'):
                if body['organization_id'] not in {o['id'] for o in orgs}:return 403,{}
                return 200,{'access_token':body['organization_id']}
            if body['organization_id']!=token:return 403,{}
            return 200,{'allowed':True}
        with tempfile.TemporaryDirectory() as directory:
            got=list(qualify(config,{'STARTUP_AUTH_URL':'https://auth.example.invalid','STARTUP_USER_URL':'https://user.example.invalid'},
                directory,'broker','00000000-0000-4000-8000-000000009999',owners=owners,request=request))
        self.assertEqual(got,list(CASES));self.assertEqual(calls[:4],['user','auth','user','auth'])
    def test_execution_requires_scheduled_main(self):
        for env in ({},{'GITHUB_ACTIONS':'true','GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_REPOSITORY':'FyberLabs/infra','GITHUB_REF':'refs/heads/main'}):
            with self.assertRaises(ValueError):require_schedule(env)
