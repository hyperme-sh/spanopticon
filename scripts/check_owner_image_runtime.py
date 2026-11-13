#!/usr/bin/env python3
"""Check the retained gateway's real Compose startup and source identity."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deployment import bundle as b
from deployment import owner_image as image

parser=argparse.ArgumentParser();parser.add_argument('--directory',type=Path,required=True)
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
os.environ["SPANOPTICON_TEST_TAG"]="qualification"
value=image.pin(b.document((root/'fleet/owner-images/hypermesh-gateway.json').read_bytes()))
image.verify(value,args.directory/'image.tar')
subprocess.run(['docker','pull','--platform','linux/amd64',value['source']+'@'+value['manifest_digest']],check=True,stdout=subprocess.DEVNULL)
subprocess.run(['docker','image','tag',value['source']+'@'+value['manifest_digest'],'qualified-owner:ci'],check=True)
with tempfile.TemporaryDirectory(prefix='gateway-startup-') as directory:
    override=Path(directory)/'override.json'
    override.write_text(json.dumps({'services':{'gateway':{'image':'qualified-owner:ci','pull_policy':'never'}}}))
    command=['docker','compose','-p','spanopticon-owner-startup','-f',str(root/'fleet/gateway/compose.yaml'),'-f',str(override)]
    try:
        subprocess.run([*command,'up','-d','--wait','--wait-timeout','100'],check=True,timeout=150)
        with urllib.request.urlopen('http://127.0.0.1:18000/version',timeout=5) as response:
            version=b.document(response.read(b.MAX_METADATA))
        b.require(version.get('git_sha')==value['source_revision'],'Running gateway source differs from pin')
        receipt={'schema':1,'image':value['manifest_digest'],'source_revision':value['source_revision'],
                 'result':'Compose health and version passed','fleet_acceptance':False}
        (args.directory/'startup.json').write_text(json.dumps(receipt,indent=2)+'\n')
    finally:
        # No volumes are declared; remove only this task's containers/network.
        subprocess.run([*command,'down'],check=True,timeout=60)
