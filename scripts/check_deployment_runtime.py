#!/usr/bin/env python3
"""Exercise synthetic test adapters on a disposable Docker context or kind cluster."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from deployment.registry import fetch_test
from deployment.runtime import deploy
from runtime_fixture import ASSURANCE,runtime_fixture
from test_registry import FakeRegistry

IMAGE='ghcr.io/astral-sh/uv@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine',required=True,choices=['compose','helm'])
    parser.add_argument('--context',required=True)
    parser.add_argument('--receipt',required=True,type=Path)
    args=parser.parse_args()
    name='sp-test-'+uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix='spanopticon-runtime-') as directory:
        root=Path(directory);fixture,_,_=runtime_fixture(root/'source',IMAGE)
        fetch_test('ghcr.io/example/plane','test','0.24.0',root/'download',FakeRegistry(fixture))
        credential=root/'credential';credential.write_text('synthetic-not-a-provider-credential');credential.chmod(0o444)
        os.environ['SPANOPTICON_SECRET_VERIFICATION_TEST_CLIENT_FILE']=str(credential)
        namespace_created=False
        try:
            if args.engine=='helm':
                # Refuse broad contexts in this qualification harness. The deploy CLI still requires an explicit context.
                if not args.context.startswith('kind-spanopticon-'):raise ValueError('Use a disposable kind-spanopticon-* context')
                subprocess.run(['kubectl','--context',args.context,'create','namespace',name],check=True,stdout=subprocess.DEVNULL)
                namespace_created=True
                subprocess.run(['kubectl','--context',args.context,'-n',name,'create','secret','generic','verification-test-client','--from-file=token='+str(credential)],check=True,stdout=subprocess.DEVNULL)
            plan=deploy(root/'download',args.engine,name,ASSURANCE,root/'plan',context=args.context)
            applied=deploy(root/'download',args.engine,name,ASSURANCE,root/'apply',context=args.context,apply=True,timeout=180)
            if args.engine=='compose':
                compose=['docker','--context',args.context,'compose','--env-file',os.devnull,'-p',name,'-f',str(root/'apply/compose.resolved.json')]
                subprocess.run(compose+['exec','-T','api','python3','-c','import urllib.request; assert urllib.request.urlopen("http://127.0.0.1:8080/readyz").read()==b"ok"'],check=True)
            else:
                subprocess.run(['kubectl','--context',args.context,'-n',name,'exec','deploy/api','--','python3','-c','import urllib.request; assert urllib.request.urlopen("http://api:8080/readyz", timeout=5).read()==b"ok"'],check=True)
                # Exercise Helm upgrade as well as fresh install under the same exact plan.
                deploy(root/'download','helm',name,ASSURANCE,root/'upgrade',context=args.context,apply=True,timeout=180)
            receipt={'schema':1,'qualification':'synthetic-adapter-runtime-only','engine':args.engine,
                     'plan':plan,'apply':applied,'http_smoke':'passed','provider_acceptance':False,
                     'registry_transport':'fixture; public GHCR image pulled by runtime'}
            with args.receipt.open('x',encoding='utf-8') as stream:
                stream.write(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
            print(args.engine+' runtime and HTTP smoke passed')
        except Exception:
            if namespace_created:
                # Fixture-only diagnostics omit Secret contents and application logs.
                for resource in ('pods', 'events'):
                    subprocess.run(['kubectl','--context',args.context,'-n',name,'get',resource,'-o','wide'],check=False)
                subprocess.run(['kubectl','--context',args.context,'-n',name,'get','pods','-o',
                                'jsonpath={range .items[*]}{.metadata.name}{"\\n"}{.status.containerStatuses}{"\\n"}{end}'],check=False)
            raise
        finally:
            if args.engine=='compose' and (root/'apply/compose.resolved.json').exists():
                subprocess.run(['docker','--context',args.context,'compose','--env-file',os.devnull,'-p',name,'-f',str(root/'apply/compose.resolved.json'),'down'],check=True,stdout=subprocess.DEVNULL)
            elif namespace_created:
                subprocess.run(['kubectl','--context',args.context,'delete','namespace',name,'--wait=true','--timeout=90s'],check=True,stdout=subprocess.DEVNULL)


if __name__=='__main__':main()
