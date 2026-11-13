#!/usr/bin/env python3
"""Qualify the pinned core image against a disposable owner database."""
import argparse,json,os,secrets,socket,subprocess,sys,tempfile,time,urllib.request,urllib.error,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deployment import owner_image as image
ROOT=Path(__file__).resolve().parents[1]

def run(args,**kw):return subprocess.run(args,check=True,timeout=300,**kw)
def probe(base,source):
    def request(path,method='GET',headers=None):
        req=urllib.request.Request(base+path,method=method,headers=headers or {},data=b'{}' if method=='POST' else None)
        try:
            with urllib.request.urlopen(req,timeout=5) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,None
    status,version=request('/version');assert status==200 and version['git_sha']==source
    status,classes=request('/api/v1/hypermesh/classes');assert status==200 and classes
    status,_=request('/api/v1/hypermesh/host-org/classes',headers={'X-Tenant-ID':'00000000-0000-0000-0000-000000000001'});assert status in (401,403)
    status,_=request('/api/v1/hypermesh/internal/billing/allocate','POST');assert status==404
    return {'source_identity':True,'catalog':True,'tenant_header_cannot_authorize':True,'billing_route_absent':True}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--directory',required=True,type=Path);parser.add_argument('--engine',choices=['compose','helm'],required=True);args=parser.parse_args()
    pin=json.loads((ROOT/'fleet/owner-images/hypermesh-plane.json').read_text());image.verify(pin,args.directory/'image.tar')
    run(['docker','pull','--platform','linux/amd64',pin['source']+'@'+pin['manifest_digest']],stdout=subprocess.DEVNULL)
    run(['docker','image','tag',pin['source']+'@'+pin['manifest_digest'],'qualified-owner:ci'])
    project='plane-check-'+uuid.uuid4().hex[:10];password=secrets.token_hex(24);key=secrets.token_hex(32)
    with tempfile.TemporaryDirectory(prefix='plane-qualification-') as directory:
        scratch=Path(directory)
        if args.engine=='compose':
            env={**os.environ,'TEST_POSTGRES_PASSWORD':password,'TEST_TICKET_KEY':key,'SPANOPTICON_TEST_TAG':'qualification'}
            override=scratch/'override.json';override.write_text(json.dumps({'services':{n:{'image':'qualified-owner:ci','pull_policy':'never'} for n in ('plane','provision')}}))
            cmd=['docker','compose','-p',project,'-f',str(ROOT/'fleet/plane/compose.yaml'),'-f',str(override)]
            try:
                run([*cmd,'up','-d','--wait','--wait-timeout','150'],env=env)
                endpoint=subprocess.check_output([*cmd,'port','plane','8000'],text=True,env=env).strip()
                checks=probe('http://'+endpoint,pin['source_revision'])
                run([*cmd,'restart','plane'],env=env)
                run([*cmd,'up','-d','--wait','--wait-timeout','150'],env=env)
                endpoint=subprocess.check_output([*cmd,'port','plane','8000'],text=True,env=env).strip()
                probe('http://'+endpoint,pin['source_revision']);checks['restart']=True
            finally:run([*cmd,'down'],env=env)
        else:
            env={**os.environ,'KUBECONFIG':str(scratch/'kubeconfig')};forward=None
            try:
                run(['kind','create','cluster','--name',project,'--image','kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5','--wait','120s'],env=env)
                run(['kind','load','image-archive',str(args.directory/'image.tar'),'--name',project],env=env)
                run(['docker','exec',project+'-control-plane','ctr','-n','k8s.io','images','tag','docker.io/library/qualified-owner:ci',pin['destination']+':qualification'])
                secret={'apiVersion':'v1','kind':'Secret','metadata':{'name':'plane-test'},'stringData':{'postgres-password':password,'ticket-key':key,'database-url':f'postgresql+psycopg://plane_test:{password}@{project}-postgres:5432/plane_test'}}
                run(['kubectl','apply','-f','-'],env=env,input=json.dumps(secret).encode(),stdout=subprocess.DEVNULL)
                helm=['helm','upgrade','--install',project,str(ROOT/'fleet/plane/chart'),'--set','existingSecret=plane-test','--set-string','imageTag=qualification','--wait','--timeout','180s']
                run(helm,env=env)
                with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                forward=subprocess.Popen(['kubectl','port-forward','service/'+project+'-plane',str(port)+':8000'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                for attempt in range(30):
                    try:checks=probe('http://127.0.0.1:'+str(port),pin['source_revision']);break
                    except (OSError,urllib.error.URLError):
                        if attempt==29:raise
                        time.sleep(1)
                run(helm,env=env);probe('http://127.0.0.1:'+str(port),pin['source_revision']);checks['upgrade']=True
            finally:
                if forward:forward.terminate();forward.wait(timeout=10)
                run(['kind','delete','cluster','--name',project],env=env)
    receipt={'schema':1,'engine':args.engine,'image':pin['destination']+'@'+pin['manifest_digest'],'source_revision':pin['source_revision'],'checks':checks,'database':'disposable PostgreSQL; platform/events/hypermesh owner migrations','fleet_acceptance':False}
    (args.directory/('plane-'+args.engine+'.json')).write_text(json.dumps(receipt,indent=2)+'\n')
if __name__=='__main__':main()
