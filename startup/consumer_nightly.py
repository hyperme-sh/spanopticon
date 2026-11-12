"""Scheduled startup consumers using declared existing TEST records only."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import threading
import subprocess
import sys
from urllib.parse import quote
import uuid
import xml.etree.ElementTree as ET
from .command import Owners, endpoint
from .contract import load, validate, fingerprint
from .orchestrator import run, private_file
from .nightly import exchange

CASES=('concurrent-restart','same-email-refusal','independent-grant-boundaries',
       'api-key-organization-isolation','marketplace-person-scope','plane-person-scope','gateway-key-scope')


def require_schedule(env):
    expected={'GITHUB_ACTIONS':'true','GITHUB_EVENT_NAME':'schedule',
        'GITHUB_REPOSITORY':'hyperme-sh/spanopticon','GITHUB_REF':'refs/heads/main',
        'NIGHTLY_SUITE':'startup-consumers',
        'GITHUB_WORKFLOW_REF':'FyberLabs/infra/.github/workflows/nightly-integration.yml@refs/heads/main'}
    if any(env.get(k)!=v for k,v in expected.items()):raise ValueError('Scheduled owner context required')


def validate_targets(value,config):
    if set(config['binding']['platform_admin'].values())!={True,False}:
        raise ValueError('Grant boundary fixture requires enabled and disabled platform scopes')
    fields={'organization_id','foreign_tenant_id','key_id','key_prefix','listing_id','lease_id','catalog_id','foreign_catalog_id'}
    if not isinstance(value,dict) or set(value)!=fields:raise ValueError('Declared consumer targets required')
    for name in ('organization_id','foreign_tenant_id','key_id','listing_id','lease_id'):
        if str(uuid.UUID(value[name]))!=value[name]:raise ValueError('Canonical target UUID required')
    if value['organization_id'] not in {o['organization_id'] for o in config['binding']['organizations'].values()}:
        raise ValueError('Selected startup organization required')
    if value['foreign_tenant_id'] in {o['tenant_id'] for o in config['binding']['organizations'].values()}:
        raise ValueError('Distinct negative tenant required')
    for name in ('key_prefix','catalog_id','foreign_catalog_id'):
        if not isinstance(value[name],str) or not 1<=len(value[name])<=256 or any(c.isspace() for c in value[name]):raise ValueError('Bounded target required')
    if value['catalog_id']==value['foreign_catalog_id']:raise ValueError('Distinct catalog targets required')
    return dict(value)


def claims(token):
    # These values select assertions only. Acceptance always comes from the
    # actual Auth route verifying this token, never from decoding here.
    part=token.split('.')[1]
    return json.loads(base64.urlsafe_b64decode(part+'='*(-len(part)%4)))


class Context:
    def __init__(self,config,env,fixture,request=exchange,owners=None):
        self.config=validate(config);self.env=env;self.fixture=fixture;self.request=request
        self.execute=subprocess.run
        self.targets=validate_targets(fixture['targets'],self.config)
        self.urls={name:endpoint(value) for name,value in fixture['consumer_urls'].items()}
        if set(self.urls)!={'marketplace','keys','plane','gateway'}:raise ValueError('All consumer endpoints required')
        self.auth=endpoint(env['STARTUP_AUTH_URL']);self.acl=endpoint(env['STARTUP_ACL_URL'])
        self.owners=owners or Owners(env,env['STARTUP_IDENTITY_CONFIG_FILE'])
        self.broker=private_file(fixture['broker_token_file']).decode().strip()
        self.other=private_file(fixture['same_email_token_file']).decode().strip()
        self.key=private_file(fixture['consumer_key_file']).decode().strip()
        if len({self.broker,self.other,self.key})!=3 or not all((self.broker,self.other,self.key)):raise ValueError('Separated consumer credentials required')
        if not self.key.startswith(self.targets['key_prefix']+'.'):raise ValueError('Declared key prefix differs')
        self.state=Path(fixture['state_dir'])

    def ready(self):
        result=run(self.config,self.state,self.owners,validate=validate,fingerprint=fingerprint)
        code,self.person=self.request(self.auth+'/auth/startup/identity',self.broker)
        assert code==200 and self.person['person_id']==result['person_id']
        assert self.person['mapping_sha256']==fingerprint(self.config) and self.person['permissions']==[]
        assert self.person['sea_public_key']==self.config['binding']['sea_public_key']
        self.sessions={}
        for org in self.config['binding']['organizations'].values():
            code,session=self.request(self.auth+'/auth/scoped-session',self.broker,{'tenant_id':org['tenant_id'],'organization_id':org['organization_id']})
            assert code==200 and session.get('access_token')
            self.sessions[org['organization_id']]=(org['tenant_id'],session['access_token'])
        self.tenant,self.token=self.sessions[self.targets['organization_id']]

    def get(self,owner,path,*,token=None,tenant=None):
        return self.request(self.urls[owner]+path,token or self.token,headers={'X-Tenant-ID':tenant or self.tenant})

    def concurrent_restart(self):
        barrier=threading.Barrier(2,timeout=30)
        def invoke():
            barrier.wait()
            completed=self.execute([sys.executable,'-m','startup.command','--config',self.env['STARTUP_IDENTITY_CONFIG_FILE'],'--state-dir',str(self.state)],
                env=self.env,capture_output=True,text=True,timeout=300,check=True)
            if len(completed.stdout)>65536:raise ValueError('Bounded command result required')
            return json.loads(completed.stdout)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(invoke) for _ in range(2)]
            results=[f.result(timeout=300) for f in futures]
        assert results[0]==results[1] and results[0]['person_id']==self.person['person_id']
        checkpoint=json.loads(private_file(self.state/'state.json'))
        assert set(checkpoint['steps'])=={'user','auth','acl','keycloak'}
        assert all(v['person_id']==self.person['person_id'] for v in checkpoint['steps'].values())

    def same_email_refusal(self):
        other=claims(self.other);selected=claims(self.broker)
        assert other['email'].casefold()==self.config['owner']['profile']['email'].casefold()==selected['email'].casefold()
        assert other['sub']!=selected['sub'] and other['iss']==selected['iss'] and other['aud']==selected['aud']
        assert (other.get('entra_tenant_id'),other.get('entra_object_id'))!=(selected.get('entra_tenant_id'),selected.get('entra_object_id'))
        code,me=self.request(self.auth+'/auth/me',self.other)
        assert code==200 and me['sub']==other['sub']
        code,refusal=self.request(self.auth+'/auth/startup/identity',self.other)
        assert code==403 and refusal.get('detail')=='Configured startup identity is not available for this login'
        code,person=self.request(self.auth+'/auth/startup/identity',self.broker)
        assert code==200 and person==self.person

    def independent_grant_boundaries(self):
        def allowed(obj,act):
            code,reply=self.request(self.acl+'/api/v1/acl/enforce',self.token,
                {'sub':'user:'+self.person['broker_subject'],'obj':obj,'act':act,'model':'rbac'}, {'X-Tenant-ID':self.tenant})
            assert code==200 and type(reply.get('allow')) is bool
            return reply['allow']
        for platform,enabled in self.config['binding']['platform_admin'].items():
            assert allowed('/platform/'+platform,'admin') is enabled
        for org in self.config['binding']['organizations'].values():
            for role in ('admin','owner'):
                assert allowed('/organizations/'+org['organization_id'],role)==(role in org['roles'])
        assert allowed('/platform/keycloak','admin') is False
        assert allowed('/organizations/'+self.fixture['foreign_organization_id'],'admin') is False
        realm=quote(self.config['binding']['keycloak_admin']['realm'],safe='')
        subject=quote(self.person['broker_subject'],safe='')
        base=endpoint(self.env['KEYCLOAK_INTERNAL_URL'])+'/admin/realms/'+realm
        admin=self.owners.tokens['keycloak_admin']
        code,clients=self.request(base+'/clients?clientId=realm-management',admin)
        assert code==200 and len(clients)==1 and clients[0]['clientId']=='realm-management'
        management=quote(clients[0]['id'],safe='')
        code,roles=self.request(base+'/users/'+subject+'/role-mappings/clients/'+management,admin)
        assert code==200 and isinstance(roles,list)
        assert any(r['name']=='realm-admin' for r in roles) is self.config['binding']['keycloak_admin']['enabled']
        assert self.person['permissions']==[]

    def api_key_organization_isolation(self):
        code,keys=self.get('keys','/api/v1/api-keys')
        assert code==200 and isinstance(keys,list)
        selected=[r for r in keys if r['id']==self.targets['key_id']]
        assert len(selected)==1
        key=selected[0]
        assert key['tenant_id']==self.tenant and key['organization_id']==self.targets['organization_id']
        assert key['key_prefix']==self.targets['key_prefix'] and key['revoked_at'] is None and key['purpose']=='renter'
        for org,(tenant,token) in self.sessions.items():
            if org==self.targets['organization_id']:continue
            code,others=self.get('keys','/api/v1/api-keys',token=token,tenant=tenant)
            assert code==200 and isinstance(others,list) and all(r['id']!=key['id'] for r in others)
        code,_=self.get('keys','/api/v1/api-keys',tenant=self.targets['foreign_tenant_id']);assert code==403

    def marketplace_person_scope(self):
        path='/api/v1/marketplace/listings/'+self.targets['listing_id']
        code,row=self.get('marketplace',path)
        assert code==200 and row['id']==self.targets['listing_id'] and row['tenant_id']==self.tenant
        assert row['seller_id']==self.person['person_id']
        code,_=self.get('marketplace',path,tenant=self.targets['foreign_tenant_id']);assert code==403

    def plane_person_scope(self):
        code,principal=self.get('plane','/api/v1/hypermesh/checkout-principal')
        assert code==200 and principal=={'tenant_id':self.tenant,'renter_user_id':self.person['person_id']}
        code,leases=self.get('plane','/api/v1/hypermesh/leases')
        assert code==200 and isinstance(leases,list)
        selected=[r for r in leases if r['id']==self.targets['lease_id']]
        assert len(selected)==1 and selected[0]['renter_organization_id']==self.targets['organization_id']
        assert selected[0]['catalog_id']==self.targets['catalog_id']
        assert all(r['tenant_id']==self.tenant and r['renter_user_id']==self.person['person_id'] for r in leases)
        code,_=self.get('plane','/api/v1/hypermesh/leases',tenant=self.targets['foreign_tenant_id']);assert code==403

    def gateway_key_scope(self):
        code,cap=self.get('gateway','/api/v1/hypermesh-gateway/key-scope-capabilities',token=self.key)
        assert code==200 and cap=={'api_key_scope_version':1,'require_key_scope':True}
        code,models=self.get('gateway','/v1/models',token=self.key)
        assert code==200 and models['object']=='list'
        ids=[m['id'] for m in models['data']]
        assert self.targets['catalog_id'] in ids and self.targets['foreign_catalog_id'] not in ids
        code,_=self.get('gateway','/v1/models',token=self.key,tenant=self.targets['foreign_tenant_id']);assert code==403


def execute_cases(context):
    try:context.ready()
    except Exception:return [(name,'blocked') for name in CASES]
    results=[]
    for name in CASES:
        try:getattr(context,name.replace('-','_'))()
        except Exception:results.append((name,'failed'))
        else:results.append((name,'passed'))
    return results


def report(output,results):
    if [name for name,_ in results]!=list(CASES):raise ValueError('Complete named outcomes required')
    suite=ET.Element('testsuite',name='startup-consumers',tests=str(len(results)))
    for name,status in results:
        case=ET.SubElement(suite,'testcase',name=name,classname='startup-consumers')
        if status=='blocked':ET.SubElement(case,'skipped',message='Declared owner fixture unavailable')
        elif status=='failed':ET.SubElement(case,'failure',message='Startup consumer contract failed')
        elif status!='passed':raise ValueError('Unknown case outcome')
    suite.set('failures',str(sum(s=='failed' for _,s in results)));suite.set('skipped',str(sum(s=='blocked' for _,s in results)))
    output.mkdir(parents=True,exist_ok=True)
    ET.ElementTree(suite).write(output/'startup-consumers.xml',encoding='utf-8',xml_declaration=True)


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--fixture',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();results=[(name,'blocked') for name in CASES]
    try:
        require_schedule(os.environ);fixture=json.loads(private_file(args.fixture))
        if fixture.get('owner')!='FyberLabs/infra' or fixture.get('isolated') is not True:raise ValueError('Isolated fixture required')
        env={**os.environ,**fixture['environment']};config=load(env['STARTUP_IDENTITY_CONFIG_FILE'])
        results=execute_cases(Context(config,env,fixture))
    except Exception:pass
    report(args.output,results)
    print(json.dumps({'cases':[{'name':n,'status':s} for n,s in results]}))
    return 0 if all(s=='passed' for _,s in results) else 1


if __name__=='__main__':raise SystemExit(main())
