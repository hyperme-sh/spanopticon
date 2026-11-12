"""Copy mounted deployment inputs into private owned files, then reconcile."""
import argparse
import json
import os
from pathlib import Path
from .contract import validate
from .orchestrator import atomic
from .command import main as reconcile

FILES={'user.token':'STARTUP_USER_TOKEN_FILE','auth.token':'STARTUP_AUTH_TOKEN_FILE',
       'acl.token':'STARTUP_ACL_TOKEN_FILE','keycloak.token':'STARTUP_KEYCLOAK_ADMIN_TOKEN_FILE',
       'entra-client.secret':'STARTUP_ENTRA_CLIENT_SECRET_FILE'}


def stage(source,destination):
    # The source is a read-only deployment secret mount (which may use symlinks).
    # Only these named keys are copied. Runtime readers reject symlinks/hardlinks.
    source=Path(source);destination=Path(destination)
    values={}
    for name in ('mapping.json',*FILES):
        if name=='entra-client.secret' and not (source/name).exists():continue
        with (source/name).open('rb') as stream:value=stream.read(16385)
        if not value or len(value)>16384:raise ValueError('Bounded deployment input required')
        values[name]=value
    mapping=validate(json.loads(values['mapping.json']))
    tokens=[values[name].decode().strip() for name in FILES if name!='entra-client.secret']
    if any(not value or any(c.isspace() for c in value) for value in tokens) or len(set(tokens))!=4:
        raise ValueError('Distinct scoped startup credentials required')
    destination.mkdir(mode=0o700,parents=True,exist_ok=True)
    info=destination.lstat()
    if destination.is_symlink() or not destination.is_dir() or info.st_uid!=os.geteuid() or info.st_mode&0o077:
        raise ValueError('Private staging directory required')
    atomic(destination/'mapping.json',mapping)
    for name in FILES:
        if name not in values:continue
        # Replace, never follow an existing destination link.
        import tempfile
        fd,path=tempfile.mkstemp(dir=destination,prefix='.credential-')
        try:
            with os.fdopen(fd,'wb') as stream:stream.write(values[name]);stream.flush();os.fsync(stream.fileno())
            os.replace(path,destination/name)
        finally:
            if os.path.exists(path):os.unlink(path)
    return {variable:str(destination/name) for name,variable in FILES.items() if name in values}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir',type=Path,required=True)
    parser.add_argument('--state-dir',type=Path,required=True)
    args=parser.parse_args()
    try:
        if os.environ.get('STARTUP_IDENTITY_ENABLED')!='1':raise ValueError('Disabled')
        staging=args.state_dir/'private'
        os.environ.update(stage(args.input_dir,staging))
        return reconcile(['--config',str(staging/'mapping.json'),'--state-dir',str(args.state_dir/'steps')])
    except (ValueError,OSError,KeyError):
        print('Startup deployment inputs refused.',file=__import__('sys').stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
