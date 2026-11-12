"""Deployment-only startup command; peer access is scoped and disabled by default."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from .contract import load, fingerprint
from .orchestrator import run, private_file, atomic

PATHS={'user':'/api/v1/internal/startup/reconcile','auth':'/auth/startup/reconcile','acl':'/api/v1/internal/startup/reconcile'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def endpoint(value):
    parsed=urlsplit(value)
    if (parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ('localhost','127.0.0.1','::1')) or
            not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment):
        raise ValueError('Verified owner endpoint required')
    return value.rstrip('/')


def credentials(environment):
    tokens={}
    for step in ('USER','AUTH','ACL','KEYCLOAK_ADMIN'):
        value=private_file(environment['STARTUP_'+step+'_TOKEN_FILE']).decode().strip()
        if not value or any(c.isspace() for c in value):raise ValueError('Private scoped credential required')
        tokens[step.lower()]=value
    if len(set(tokens.values()))!=len(tokens):raise ValueError('Startup credentials must be separated by owner')
    return tokens


class Owners:
    def __init__(self,environment,config_path,*,opener=None,execute=subprocess.run):
        # Preflight every credential and URL before the first owner can mutate.
        self.tokens=credentials(environment)
        self.urls={step:endpoint(environment['STARTUP_'+step.upper()+'_URL']) for step in PATHS}
        self.config_path=str(Path(config_path).resolve())
        self.tool=Path(environment['STARTUP_KEYCLOAK_TOOL']).resolve(strict=True)
        if not self.tool.is_file():raise ValueError('Keycloak startup tool required')
        self.keycloak_url=endpoint(environment['KEYCLOAK_INTERNAL_URL'])
        self.admin_file=str(Path(environment['STARTUP_KEYCLOAK_ADMIN_TOKEN_FILE']).resolve())
        self.secret_file=environment.get('STARTUP_ENTRA_CLIENT_SECRET_FILE')
        if self.secret_file:
            self.secret_file=str(Path(self.secret_file).resolve())
            if not private_file(self.secret_file).strip():raise ValueError('Configured Entra application secret required')
        self.opener=opener or build_opener(NoRedirect())
        self.execute=execute

    def __call__(self,step,config,prior):
        if step!='keycloak':
            request=Request(self.urls[step]+PATHS[step],data=json.dumps({'mapping_sha256':fingerprint(config)}).encode(),
                headers={'Authorization':'Bearer '+self.tokens[step],'Content-Type':'application/json'},method='POST')
            with self.opener.open(request,timeout=30) as response:
                raw=response.read(65537)
                if len(raw)>65536:raise ValueError('Owner response exceeds limit')
                return json.loads(raw)
        # Keycloak's native owner command receives only its own credential and
        # the already bound person / committed ACL result, never peer tokens.
        with tempfile.TemporaryDirectory(prefix='span-startup-') as directory:
            directory=Path(directory);person=directory/'person.json';acl=directory/'acl.json';mapping=directory/'mapping.json'
            atomic(person,prior['auth']);atomic(acl,prior['acl']);atomic(mapping,config)
            env={'PATH':os.defpath,'PYTHONDONTWRITEBYTECODE':'1','STARTUP_IDENTITY_ENABLED':'1',
                 'KEYCLOAK_INTERNAL_URL':self.keycloak_url,'KEYCLOAK_REALM':config['binding']['keycloak_admin']['realm'],
                 'STARTUP_KEYCLOAK_ADMIN_TOKEN_FILE':self.admin_file}
            if self.secret_file:env['STARTUP_ENTRA_CLIENT_SECRET_FILE']=self.secret_file
            completed=self.execute([sys.executable,str(self.tool),'--config',str(mapping),
                '--bound-person',str(person),'--acl-result',str(acl)],env=env,capture_output=True,text=True,timeout=120,check=False)
            if completed.returncode or len(completed.stdout)>65536:raise ValueError('Keycloak startup command failed')
            return json.loads(completed.stdout)


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--state-dir',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        if os.environ.get('STARTUP_IDENTITY_ENABLED')!='1':raise ValueError('Disabled')
        config=load(args.config)
        owners=Owners(os.environ,args.config)
        from .contract import validate
        result=run(config,args.state_dir,owners,validate=validate,fingerprint=fingerprint)
    except (ValueError,OSError,KeyError,subprocess.SubprocessError):
        print('Startup reconciliation refused; check private configuration and owner status.',file=sys.stderr)
        return 1
    print(json.dumps(result,sort_keys=True))
    return 0


if __name__=='__main__':raise SystemExit(main())
