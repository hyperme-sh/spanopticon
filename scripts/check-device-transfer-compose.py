"""CI-only configuration parser contract; never starts a service."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployment.device_transfer import write

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise SystemExit('CI only')
with tempfile.TemporaryDirectory() as root:
    root=Path(root)
    (root/'auth.env').touch();(root/'s3rch.env').touch();(root/'acl:current').mkdir()
    value={'version':1,'edgeOrigin':'https://edge.example','crossApplicationPairs':[],
           'clients':[{'application':'hyperme.sh','origin':'https://portal.example','tenant':'00000000-0000-0000-0000-000000000001','audience':'test','provider':None},
                      {'application':'s3r.ch','origin':'https://s3r.example','tenant':'00000000-0000-0000-0000-000000000002','audience':'test-s3r','provider':'existing'}],
           'ownerFiles':{'authEnvironment':str(root/'auth.env'),'aclPrivateDirectory':str(root/'acl:current'),'s3rchEnvironment':str(root/'s3rch.env')}}
    generated=write(value,root/'generated')
    overlay=json.loads((generated/'compose.override.json').read_text())
    base={'services':{name:{'image':'registry.example/'+name+':test','pull_policy':'always','restart':'unless-stopped'} for name in overlay['services']}}
    (root/'base.json').write_text(json.dumps(base))
    subprocess.run(['docker','compose','-f',str(root/'base.json'),'-f',str(generated/'compose.override.json'),'config','--quiet'],check=True)
