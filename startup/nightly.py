"""Scheduled startup qualification against the Infra-declared isolated owner fixture."""
import json
import os
from pathlib import Path
import uuid
from urllib.error import HTTPError
from urllib.request import Request,build_opener
import xml.etree.ElementTree as ET
from .command import Owners,NoRedirect,endpoint
from .contract import load,validate,fingerprint
from .orchestrator import run,private_file

CASES=('interrupted-boot-retry','repeated-boot','bound-sea-identity','declared-organization-sessions','cross-organization-denial')


def require_schedule(env):
    if not (env.get('GITHUB_ACTIONS')=='true' and env.get('GITHUB_EVENT_NAME')=='schedule' and
            env.get('GITHUB_REPOSITORY')=='FyberLabs/infra' and env.get('GITHUB_REF')=='refs/heads/main'):
        raise ValueError('Scheduled Infra batch required')


def exchange(url,token,body=None,headers=None):
    request=Request(url,data=None if body is None else json.dumps(body).encode(),
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json',**(headers or {})},
        method='GET' if body is None else 'POST')
    try:
        with build_opener(NoRedirect()).open(request,timeout=30) as response:
            raw=response.read(65537)
            if len(raw)>65536:raise ValueError('Bounded owner response required')
            return response.status,json.loads(raw)
    except HTTPError as error:
        return error.code,{}


def qualify(config,environment,state_dir,broker_token,foreign_organization_id,*,owners=None,request=exchange):
    config=validate(config)
    foreign=str(uuid.UUID(foreign_organization_id))
    if foreign in {o['organization_id'] for o in config['binding']['organizations'].values()}:
        raise ValueError('Distinct declared negative organization required')
    owners=owners or Owners(environment,environment['STARTUP_IDENTITY_CONFIG_FILE'])
    directory=Path(state_dir);directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    class Interrupted(Exception):pass
    def interrupt(step,c,prior):
        value=owners(step,c,prior)
        # Interrupt after the owner's committed write but before its checkpoint.
        if step=='auth':raise Interrupted()
        return value
    try:run(config,directory,interrupt,validate=validate,fingerprint=fingerprint)
    except Interrupted:pass
    else:raise AssertionError('Interruption point not reached')
    first=run(config,directory,owners,validate=validate,fingerprint=fingerprint)
    yield CASES[0]
    assert run(config,directory,owners,validate=validate,fingerprint=fingerprint)==first
    yield CASES[1]
    auth=endpoint(environment['STARTUP_AUTH_URL']);user=endpoint(environment['STARTUP_USER_URL'])
    status,person=request(auth+'/auth/startup/identity',broker_token)
    assert status==200 and person['status']=='resolved' and person['person_id']==first['person_id']
    assert person['sea_public_key']==config['binding']['sea_public_key'] and person['binding_sha256']==config['binding_sha256']
    assert person['permissions']==[] and person['mapping_sha256']==fingerprint(config)
    expected=[{'slug':s,'id':o['organization_id'],'tenant_id':o['tenant_id'],'roles':o['roles']} for s,o in sorted(config['binding']['organizations'].items())]
    assert person['organizations']==expected
    yield CASES[2]
    tenant=config['owner']['tenant_id'];scoped=[]
    for org in expected:
        status,session=request(auth+'/auth/scoped-session',broker_token,{'tenant_id':tenant,'organization_id':org['id']})
        assert status==200 and session.get('access_token')
        scoped.append((org['id'],session['access_token']))
        status,check=request(user+'/api/v1/authority/check',session['access_token'],{'version':1,'request_id':str(uuid.uuid4()),
            'action':'organization.admin' if 'admin' in org['roles'] else 'organization.read',
            'tenant_id':tenant,'organization_id':org['id']},{'X-Tenant-ID':tenant})
        assert status==200 and check['allowed'] is True
    yield CASES[3]
    status,_=request(auth+'/auth/scoped-session',broker_token,{'tenant_id':tenant,'organization_id':foreign})
    assert status==403
    for index,(selected,token) in enumerate(scoped):
        other=scoped[(index+1)%len(scoped)][0]
        status,_=request(user+'/api/v1/authority/check',token,{'version':1,'request_id':str(uuid.uuid4()),
            'action':'organization.admin','tenant_id':tenant,'organization_id':other},{'X-Tenant-ID':tenant})
        assert status==403
    yield CASES[4]


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();completed=[];status='failed'
    try:
        require_schedule(os.environ)
        fixture=json.loads(private_file(args.fixture))
        if fixture.get('owner')!='FyberLabs/infra' or fixture.get('isolated') is not True:
            raise ValueError('Declared isolated fixture required')
        env={**os.environ,**fixture['environment']}
        config=load(env['STARTUP_IDENTITY_CONFIG_FILE'])
        token=private_file(fixture['broker_token_file']).decode().strip()
        for case in qualify(config,env,fixture['state_dir'],token,fixture['foreign_organization_id']):completed.append(case)
        status='passed'
    except (ValueError,OSError,KeyError,AssertionError):
        pass
    args.output.mkdir(parents=True,exist_ok=True)
    suite=ET.Element('testsuite',name='startup-identity',tests=str(len(CASES)),failures=str(len(CASES)-len(completed)))
    for name in CASES:
        case=ET.SubElement(suite,'testcase',classname='startup-identity',name=name)
        if name not in completed:ET.SubElement(case,'failure',message='Owner fixture unavailable or startup contract failed')
    ET.ElementTree(suite).write(args.output/'startup.xml',encoding='utf-8',xml_declaration=True)
    print(json.dumps({'status':status,'completed':completed},sort_keys=True))
    return 0 if status=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
