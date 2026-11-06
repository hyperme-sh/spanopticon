"""Qualify reviewed fleet assets through both deployment consumers before publication."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from deployment import bundle as b
from deployment.publish import REPOSITORY, QUALIFIER, admitted
from deployment.runtime import deploy, selection, assets, compose_check, helm_check, command
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assemble_test_bundle import assemble
from assurance import settings


def read(path):
    with Path(path).open('rb') as stream:raw=stream.read(b.MAX_METADATA+1)
    b.require(len(raw)<=b.MAX_METADATA,'Fleet metadata exceeds size limit')
    return b.document(raw)


def inputs(root):
    root=Path(root)
    b.require(not root.is_symlink() and not (root/'test.json').is_symlink(),'Symlinked fleet inputs are unsupported')
    declaration=read(root/'test.json')
    b.keys(declaration,('schema','components','assurance'),'fleet inputs')
    b.require(type(declaration['schema']) is int and declaration['schema']==1,'Unsupported fleet schema')
    settings(declaration['assurance'])
    for name in ('compose.json','chart.tgz'):
        path=root/name
        b.require(path.is_file() and not path.is_symlink(),'Missing regular fleet deployment asset')
    return declaration


def assemble_input(root,output,env):
    declaration=inputs(root)
    revision,run=env.get('GITHUB_SHA'),env.get('GITHUB_RUN_ID')
    b.require(b.matches(r'[0-9a-f]{40}',revision) and b.matches(r'[1-9][0-9]*',run),'Expected source/run identity')
    metadata={'schema':1,'candidate':'0.24.0-test.'+run,'source_revision':revision,'components':declaration['components']}
    reference=assemble(metadata,Path(root)/'compose.json',Path(root)/'chart.tgz',REPOSITORY,output)
    receipt={'schema':1,'channel':'test','selector':metadata['candidate'],'next':'0.24.0',
             'bundle_reference':reference,'candidate':metadata['candidate']}
    (Path(output)/'selection.json').write_text(json.dumps(receipt)+'\n')
    layout,_,lock=selection(output)
    payload=assets(layout,lock)
    compose_check(b.document(payload['compose']),lock,declaration['assurance'])
    # Rendering is validated without applying or creating credentials in PR runs.
    rendered=command(['helm','template','fleet-validation',str(Path(root).resolve()/'chart.tgz'),
                      '--namespace','fleet-validation'])
    helm_check(rendered,lock,declaration['assurance'],'fleet-validation')
    return declaration,reference


def native(args):
    command(args,timeout=240)


def qualify(root,output,env=None,*,docker_context='default',kube_context=None,apply=deploy,execute=native):
    env=dict(os.environ) if env is None else env
    b.require(env.get('GITHUB_REPOSITORY')=='hyperme-sh/spanopticon'
              and env.get('GITHUB_EVENT_NAME')=='push' and env.get('GITHUB_REF')=='refs/heads/main'
              and env.get('GITHUB_WORKFLOW_REF')==QUALIFIER
              and b.matches(r'[1-9][0-9]*',env.get('GITHUB_RUN_ATTEMPT')),
              'Only the main fleet qualification workflow may apply')
    b.require(kube_context and kube_context.startswith('kind-spanopticon-fleet-'),
              'Qualification requires a disposable kind-spanopticon-fleet-* context')
    b.require(docker_context=='default','Qualification uses the disposable job Docker daemon')
    output=Path(output);output.mkdir(mode=0o700)
    declaration,reference=assemble_input(root,output/'oci',env)
    assurance=declaration['assurance'];_,secret_names=settings(assurance)
    try:credentials=b.document(env.get('FLEET_TEST_CREDENTIALS','{}'))
    except (ValueError,TypeError):raise ValueError('Invalid fleet qualification credentials') from None
    b.require(set(credentials)==set(secret_names) and all(isinstance(v,str) and 0<len(v)<=65536 for v in credentials.values()),
              'Supply exactly the selected assurance credentials')
    name='sp-fleet-'+uuid.uuid4().hex[:12]
    receipts={};created=False;compose_attempted=False;saved={}
    with tempfile.TemporaryDirectory(prefix='fleet-qualification-') as directory:
        scratch=Path(directory)
        try:
            execute(['kubectl','--context',kube_context,'create','namespace',name]);created=True
            for secret,value in credentials.items():
                path=scratch/secret;path.write_text(value);path.chmod(0o444)
                variable='SPANOPTICON_SECRET_'+secret.upper().replace('-','_')+'_FILE'
                saved[variable]=os.environ.get(variable);os.environ[variable]=str(path)
                execute(['kubectl','--context',kube_context,'-n',name,'create','secret','generic',secret,'--from-file=token='+str(path)])
            compose_attempted=True
            receipts['compose']=apply(output/'oci','compose',name,assurance,scratch/'compose',apply=True,context=docker_context)
            receipts['helm']=apply(output/'oci','helm',name,assurance,scratch/'helm',apply=True,context=kube_context)
            # Exercise an upgrade with the same immutable assets as well as first install.
            apply(output/'oci','helm',name,assurance,scratch/'upgrade',apply=True,context=kube_context)
        finally:
            try:
                if compose_attempted and (scratch/'compose/compose.resolved.json').exists():
                    execute(['docker','--context',docker_context,'compose','--env-file',os.devnull,'-p',name,
                             '-f',str(scratch/'compose/compose.resolved.json'),'down'])
            finally:
                try:
                    if created:execute(['kubectl','--context',kube_context,'delete','namespace',name,'--wait=true','--timeout=90s'])
                finally:
                    for variable,old in saved.items():
                        if old is None:os.environ.pop(variable,None)
                        else:os.environ[variable]=old
    record={'schema':'spanopticon-fleet-qualification/v1','source_revision':env['GITHUB_SHA'],
            'run_id':env['GITHUB_RUN_ID'],'run_attempt':env['GITHUB_RUN_ATTEMPT'],
            'bundle_reference':reference,'receipts':receipts}
    # Reserve no success record until both engines, upgrade and cleanup succeed.
    path=output/'qualification.json';path.write_text(json.dumps(record,sort_keys=True,indent=2)+'\n')
    try:admitted(output,env)
    except Exception:
        path.unlink();raise
    return record
