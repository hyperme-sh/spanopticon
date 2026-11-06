#!/usr/bin/env python3
"""Check the retained gateway's real Compose startup and source identity."""
import argparse
import json
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
value=image.pin(b.document((root/'fleet/owner-images/hypermesh-gateway.json').read_bytes()))
image.verify(value,args.directory/'image.tar')
subprocess.run(['docker','load','-i',str(args.directory/'image.tar')],check=True,stdout=subprocess.DEVNULL)
with tempfile.TemporaryDirectory(prefix='gateway-startup-') as directory:
    override=Path(directory)/'override.json'
    override.write_text(json.dumps({'services':{'gateway':{'image':value['config_digest'],'pull_policy':'never'}}}))
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
