"""Consumer protocol unit vectors: no network, browser, database or services."""
import base64
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from startup import consumer_nightly as target
from startup.contract import fingerprint

ROOT=Path(__file__).resolve().parents[1]


def token(subject,oid):
    config=json.loads((ROOT/'startup/fixtures/mapping.json').read_text())
    value={'sub':subject,'entra_tenant_id':'tenant','entra_object_id':oid,'email':config['owner']['profile']['email'],'iss':'issuer','aud':'client'}
    return 'header.'+base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')+'.signature'


def context():
    c=target.Context.__new__(target.Context)
    c.config=json.loads((ROOT/'startup/fixtures/mapping.json').read_text())
    c.tenant=c.config['owner']['tenant_id'];orgs=list(c.config['binding']['organizations'].values())
    c.targets={'organization_id':orgs[0]['organization_id'],'foreign_tenant_id':'00000000-0000-4000-8000-000000009999',
        'key_id':'00000000-0000-4000-8000-000000009998','key_prefix':'fixture','listing_id':'00000000-0000-4000-8000-000000009997',
        'lease_id':'00000000-0000-4000-8000-000000009996','catalog_id':'declared-model','foreign_catalog_id':'other-model'}
    c.urls={name:'https://'+name+'.invalid' for name in ('keys','marketplace','plane','gateway')}
    c.auth='https://auth.invalid';c.acl='https://acl.invalid';c.env={'KEYCLOAK_INTERNAL_URL':'https://keycloak.invalid'}
    c.person={'person_id':'person','broker_subject':'broker','permissions':[]}
    c.fixture={'foreign_organization_id':'00000000-0000-4000-8000-000000009995'}
    c.broker=token('broker','oid');c.other=token('other','different');c.key='fixture.secret';c.token='selected'
    c.sessions={o['organization_id']:(o['tenant_id'],'selected' if i==0 else 'other-org') for i,o in enumerate(orgs)}
    c.owners=SimpleNamespace(tokens={'keycloak_admin':'admin-only'})
    def request(url,credential,body=None,headers=None):
        if headers and headers.get('X-Tenant-ID')==c.targets['foreign_tenant_id']:return 403,{}
        if url.endswith('/auth/me'):return 200,{'sub':'other'}
        if url.endswith('/auth/startup/identity'):
            return (403,{'detail':'Configured startup identity is not available for this login'}) if credential==c.other else (200,copy.deepcopy(c.person))
        if url.endswith('/acl/enforce'):
            allowed=False
            for platform,enabled in c.config['binding']['platform_admin'].items():
                if body['obj']=='/platform/'+platform and body['act']=='admin':allowed=enabled
            for org in orgs:
                if body['obj']=='/organizations/'+org['organization_id']:allowed=body['act'] in org['roles']
            return 200,{'allow':allowed}
        if 'clientId=realm-management' in url:return 200,[{'id':'management','clientId':'realm-management'}]
        if '/role-mappings/clients/' in url:
            assert credential=='admin-only'
            return 200,[{'name':'realm-admin'}] if c.config['binding']['keycloak_admin']['enabled'] else []
        if url.endswith('/api-keys'):
            return 200,[] if credential=='other-org' else [{'id':c.targets['key_id'],'tenant_id':c.tenant,'organization_id':c.targets['organization_id'],
                'key_prefix':'fixture','revoked_at':None,'purpose':'renter'}]
        if '/marketplace/listings/' in url:return 200,{'id':c.targets['listing_id'],'tenant_id':c.tenant,'seller_id':'person'}
        if url.endswith('/checkout-principal'):return 200,{'tenant_id':c.tenant,'renter_user_id':'person'}
        if url.endswith('/leases'):return 200,[{'id':c.targets['lease_id'],'tenant_id':c.tenant,'renter_user_id':'person','renter_organization_id':c.targets['organization_id'],'catalog_id':c.targets['catalog_id']}]
        if url.endswith('/key-scope-capabilities'):return 200,{'api_key_scope_version':1,'require_key_scope':True}
        if url.endswith('/v1/models'):
            assert credential==c.key
            return 200,{'object':'list','data':[{'id':'declared-model'}]}
        raise AssertionError('Unexpected protocol call')
    c.request=request
    return c


class StartupConsumerContracts(unittest.TestCase):
    def test_positive_protocol_vectors(self):
        c=context()
        for name in target.CASES[1:]:
            with self.subTest(name=name):getattr(c,name.replace('-','_'))()

    def test_each_consumer_rejects_empty_or_wrong_positive_results(self):
        for name in target.CASES[1:]:
            c=context();c.request=lambda *a,**kw:(200,{})
            with self.subTest(name=name),self.assertRaises((AssertionError,KeyError,TypeError)):
                getattr(c,name.replace('-','_'))()

    def test_wrong_tenant_success_is_not_a_denial(self):
        for name in ('api_key_organization_isolation','marketplace_person_scope','plane_person_scope','gateway_key_scope'):
            c=context();original=c.request
            def leak(url,credential,body=None,headers=None):
                if headers and headers.get('X-Tenant-ID')==c.targets['foreign_tenant_id']:headers={'X-Tenant-ID':c.tenant}
                return original(url,credential,body,headers)
            c.request=leak
            with self.subTest(name=name),self.assertRaises(AssertionError):getattr(c,name)()

    def test_other_organization_key_leak_fails(self):
        c=context();original=c.request
        c.request=lambda u,t,b=None,headers=None:original(u,'selected',b,headers)
        with self.assertRaises(AssertionError):c.api_key_organization_isolation()

    def test_same_email_requires_valid_other_identity_and_exact_refusal(self):
        for status,body in ((401,{}),(403,{'detail':'MFA required'}),(200,{})):
            c=context();original=c.request
            c.request=lambda u,t,b=None,headers=None:(status,body) if u.endswith('/startup/identity') and t==c.other else original(u,t,b,headers)
            with self.assertRaises(AssertionError):c.same_email_refusal()
        c=context();c.other=c.broker
        with self.assertRaises(AssertionError):c.same_email_refusal()

    def test_grants_follow_each_independent_boolean(self):
        for keycloak in (False,True):
            for platform in (False,True):
                c=context();c.config['binding']['keycloak_admin']['enabled']=keycloak
                c.config['binding']['platform_admin']={'spanopticon':platform,'hyperme.sh':not platform}
                c.independent_grant_boundaries()

    def test_unrelated_failure_does_not_hide_other_cases(self):
        class Fake:
            def ready(self):pass
            def __getattr__(self,name):
                def check():
                    if name=='same_email_refusal':raise AssertionError()
                return check
        rows=target.execute_cases(Fake())
        self.assertEqual([n for n,s in rows if s=='failed'],['same-email-refusal'])
        self.assertEqual(sum(s=='passed' for _,s in rows),6)
        class Blocked(Fake):
            def ready(self):raise ValueError()
        self.assertTrue(all(s=='blocked' for _,s in target.execute_cases(Blocked())))
        with tempfile.TemporaryDirectory() as d:
            target.report(Path(d),rows)
            from xml.etree import ElementTree as ET
            doc=ET.parse(Path(d)/'startup-consumers.xml')
            self.assertEqual(len(doc.findall('.//failure')),1)

    def test_concurrent_restart_uses_shared_checkpoint_and_actual_lock(self):
        c=context();stamp=fingerprint(c.config)
        c.owners=lambda step,config,prior:{'status':'bound' if step=='auth' else 'reconciled','person_id':'person','mapping_sha256':stamp}
        def execute(argv,**kwargs):
            self.assertEqual(argv[1:3],['-m','startup.command'])
            value=target.run(c.config,c.state,c.owners,validate=target.validate,fingerprint=fingerprint)
            return SimpleNamespace(stdout=json.dumps(value))
        c.execute=execute;c.env['STARTUP_IDENTITY_CONFIG_FILE']='unit-private-config'
        with tempfile.TemporaryDirectory() as d:
            c.state=Path(d);c.concurrent_restart()

    def test_targets_and_schedule_fail_closed(self):
        c=context();target.validate_targets(c.targets,c.config)
        uniform=copy.deepcopy(c.config);uniform['binding']['platform_admin']={'spanopticon':True,'hyperme.sh':True}
        with self.assertRaises(ValueError):target.validate_targets(c.targets,uniform)
        for changed in ({'foreign_tenant_id':c.tenant},{'organization_id':'00000000-0000-4000-8000-000000008888'},{'foreign_catalog_id':'declared-model'}):
            with self.assertRaises(ValueError):target.validate_targets({**c.targets,**changed},c.config)
        with self.assertRaises(ValueError):target.require_schedule({})
        env={'GITHUB_ACTIONS':'true','GITHUB_EVENT_NAME':'schedule','GITHUB_REPOSITORY':'hyperme-sh/spanopticon','GITHUB_REF':'refs/heads/main','NIGHTLY_SUITE':'startup-consumers','GITHUB_WORKFLOW_REF':'FyberLabs/infra/.github/workflows/nightly-integration.yml@refs/heads/main'}
        target.require_schedule(env)
        for key in env:
            with self.assertRaises(ValueError):target.require_schedule({**env,key:'wrong'})
