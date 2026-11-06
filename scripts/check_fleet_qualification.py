#!/usr/bin/env python3
"""Exercise the fleet producer with an explicit fixture; never publish its artifact."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from deployment.qualify import qualify
from deployment.publish import QUALIFIER
from runtime_fixture import runtime_fixture,ASSURANCE
from check_deployment_runtime import IMAGE


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',required=True)
    parser.add_argument('--receipt',required=True,type=Path)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='fleet-producer-fixture-') as directory:
        root=Path(directory);fixture,_,_=runtime_fixture(root/'fixture',IMAGE)
        source=root/'inputs';source.mkdir()
        (source/'test.json').write_text(json.dumps({'schema':1,'components':fixture.bundle['components'],'assurance':ASSURANCE}))
        for kind,name in [('compose','compose.json'),('helm','chart.tgz')]:
            (source/name).write_bytes(fixture.path(fixture.bundle['deployment_assets'][kind]).read_bytes())
        env={'GITHUB_REPOSITORY':'hyperme-sh/spanopticon','GITHUB_REF':'refs/heads/main','GITHUB_EVENT_NAME':'push',
             'GITHUB_WORKFLOW_REF':QUALIFIER,'GITHUB_SHA':'a'*40,'GITHUB_RUN_ID':'7','GITHUB_RUN_ATTEMPT':'1',
             'FLEET_TEST_CREDENTIALS':json.dumps({'verification-test-client':'synthetic-not-provider-credential'})}
        result=qualify(source,root/'output',env,kube_context=args.context)
        receipt={'schema':1,'scope':'synthetic producer fixture only','published':False,
                 'provider_acceptance':False,'qualification':result}
        with args.receipt.open('x') as stream:stream.write(json.dumps(receipt,indent=2)+'\n')
    print('Fixture producer passed; temporary candidate removed, nothing published')


if __name__=='__main__':main()
