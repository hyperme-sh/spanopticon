"""Ordered owner reconciliation with private atomic per-step checkpoints."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

STEPS=('user','auth','acl','keycloak')


def private_file(path):
    path=Path(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.getuid() or info.st_mode&0o077:
            raise ValueError('Private startup file required')
        raw=os.read(fd,65537)
        if len(raw)>65536:raise ValueError('Startup file exceeds limit')
        return raw
    finally:os.close(fd)


def atomic(path,value):
    fd,name=tempfile.mkstemp(prefix='.startup-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(name):os.unlink(name)


def result(step,value,stamp,person_id):
    if not isinstance(value,dict) or value.get('mapping_sha256')!=stamp:
        raise ValueError('Owner startup mapping differs: '+step)
    selected=value.get('person_id')
    if not isinstance(selected,str) or not selected or (person_id is not None and selected!=person_id):
        raise ValueError('Owner startup person differs: '+step)
    expected='bound' if step=='auth' else 'reconciled'
    if value.get('status')!=expected:raise ValueError('Owner startup step incomplete: '+step)
    # Store only the response contract, never a peer's arbitrary fields/secrets.
    allowed={'schema_version','mapping_sha256','person_id','tenant_id','network_id','organizations',
             'broker_subject','status','permissions','grant_count','keycloak_admin','application_permissions'}
    if set(value)-allowed:raise ValueError('Unexpected owner response fields: '+step)
    return value


def run(config, state_dir, call, *, validate, fingerprint):
    # Validate every binding before opening the state directory or calling peers.
    config=validate(config);stamp=fingerprint(config)
    directory=Path(state_dir)
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    info=directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
        raise ValueError('Private startup state directory required')
    descriptor=os.open(directory/'lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(descriptor,fcntl.LOCK_EX)
        checkpoint=directory/'state.json'
        state=json.loads(private_file(checkpoint)) if checkpoint.exists() or checkpoint.is_symlink() else {'mapping_sha256':stamp,'steps':{}}
        if set(state)!={'mapping_sha256','steps'} or state['mapping_sha256']!=stamp or not isinstance(state['steps'],dict):
            raise ValueError('Stored startup mapping differs')
        if set(state['steps'])-set(STEPS):raise ValueError('Unknown stored startup step')
        person_id=None;current={}
        for step in STEPS:
            # Reconcile is idempotent at each owner. Rechecking every earlier
            # result prevents a saved checkpoint from authorizing stale grants.
            value=result(step,call(step,config,dict(current)),stamp,person_id)
            previous=state['steps'].get(step)
            if previous:
                # User's subject may become bound in Auth's following step.
                before={k:v for k,v in previous.items() if k!='broker_subject'}
                after={k:v for k,v in value.items() if k!='broker_subject'}
                if before!=after:raise ValueError('Previously reconciled startup step changed: '+step)
                old_subject=previous.get('broker_subject')
                if old_subject is not None and value.get('broker_subject')!=old_subject:
                    raise ValueError('Stored broker subject changed: '+step)
            person_id=value['person_id'];current[step]=value
            state['steps'][step]=value;atomic(checkpoint,state)
        return {'status':'reconciled','mapping_sha256':stamp,'person_id':person_id,'steps':list(STEPS)}
    finally:os.close(descriptor)
