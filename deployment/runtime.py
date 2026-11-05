"""Test-only Compose/Helm consumers for verified deployment bundles."""
import hashlib
import gzip
import io
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import sys
import tarfile
import tempfile

import yaml
from deployment import bundle as b

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assurance import settings

LIMIT = 16 * 1024 * 1024
NAME = r'[a-z](?:[a-z0-9-]{0,38}[a-z0-9])?'


class StrictLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ValueError('YAML aliases are unsupported in rendered deployments')
        return super().compose_node(parent,index)

    def construct_mapping(self,node,deep=False):
        pairs = self.construct_pairs(node,deep=deep)
        result = {}
        for key,value in pairs:
            b.require(isinstance(key,str) and key not in result, 'Duplicate or non-string YAML key')
            result[key]=value
        return result


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


def command(args, *, env=None, input_bytes=None, timeout=180):
    # Child output may contain expanded config/secrets: capture, never log it on failure.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            result=subprocess.run(args,input=input_bytes,stdout=out,stderr=err,env=env,timeout=timeout,check=False)
        except (OSError,subprocess.TimeoutExpired):
            raise ValueError('Deployment tool unavailable or timed out') from None
        b.require(result.returncode == 0, 'Deployment tool failed; inspect the target using its native tooling')
        b.require(out.tell() <= LIMIT, 'Deployment tool output exceeds limit')
        out.seek(0);return out.read()


def selection(layout):
    root=Path(layout).resolve()
    raw=(root/'selection.json').read_bytes()
    b.require(len(raw)<=b.MAX_METADATA,'Selection exceeds size limit')
    receipt=b.document(raw)
    b.keys(receipt,('schema','channel','selector','next','bundle_reference','candidate'),'test selection')
    b.require(type(receipt['schema']) is int and receipt['schema']==1 and receipt['channel']=='test','Only test deployments are supported')
    b.require(b.matches(b.VERSION,receipt['next']),'Invalid next version')
    lock=b.verify(root,receipt['bundle_reference'])
    b.require(receipt['candidate']==lock['candidate'] and lock['candidate'].startswith(receipt['next']+'-test.'), 'Selection differs from verified bundle')
    b.require(receipt['selector'] in ('test',lock['candidate']),'Invalid test selector')
    # The saved lock is only a cache. Re-derive it from the immutable bytes on every invocation.
    return root,receipt,lock


def assets(root,lock):
    result={}
    for name,item in lock['deployment_assets'].items():
        b.require(item['size']<=LIMIT,'Deployment asset exceeds runtime profile limit')
        result[name]=b.blob(root,item['digest'],LIMIT,item['size'])
    # Helm receives a packaged chart, never an extracted user-controlled tree.
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(result['helm']),mode='rb') as zipped:
            expanded=zipped.read(LIMIT+1)
        b.require(len(expanded)<=LIMIT,'Expanded chart exceeds runtime profile limit')
        with tarfile.open(fileobj=io.BytesIO(expanded),mode='r:') as archive:
            paths=set();roots=set();size=0
            for item in archive:
                size+=item.size
                b.require(len(paths)<1024 and size<=LIMIT,'Invalid chart size')
                path=PurePosixPath(item.name)
                normalized=str(path)
                b.require(not path.is_absolute() and '..' not in path.parts and path.parts
                          and 'crds' not in path.parts and normalized not in paths
                          and (item.isfile() or item.isdir()),'Unsafe chart member')
                paths.add(normalized);roots.add(path.parts[0])
            b.require(len(roots)==1 and next(iter(roots))+'/Chart.yaml' in paths,'Expected one packaged chart')
    except (tarfile.TarError,EOFError,gzip.BadGzipFile):
        raise ValueError('Invalid packaged Helm chart') from None
    return result


def compose_check(document,lock,assurance):
    b.require(isinstance(document,dict) and set(document)<= {'name','services','volumes','networks','secrets'},'Unsupported Compose top-level fields')
    services=document.get('services')
    b.require(isinstance(services,dict) and set(services)==set(lock['components']),'Compose component inventory differs')
    expected,secrets=settings(assurance); adapters=0
    for name,service in services.items():
        b.require(isinstance(service,dict) and service.get('image')==lock['components'][name]['image'],'Compose image differs from bundle')
        b.require(not any(key in service for key in ('build','extends','profiles','develop','provider','pre_start','post_start','pre_stop','env_file')),'Compose build/overrides/hooks are unsupported')
        b.require(not service.get('privileged') and service.get('network_mode')!='host'
                  and service.get('pid')!='host' and service.get('ipc')!='host','Host privilege is unsupported')
        health=service.get('healthcheck',{})
        b.require(isinstance(health,dict) and health.get('test') and not health.get('disable')
                  and health['test'][0]!='NONE','Each component needs an active healthcheck')
        environment=service.get('environment',{})
        b.require(isinstance(environment,dict),'Compose environment must be a mapping')
        if 'SPANOPTICON_ASSURANCE_MODE' in environment:
            adapters+=1
            b.require({k:v for k,v in environment.items() if k.startswith('SPANOPTICON_ASSURANCE_') or k.startswith('SPANOPTICON_STRIPE_KYC_') or k.startswith('SPANOPTICON_VERIFICATION_')}==expected,'Assurance configuration differs')
            mounts=service.get('secrets',[])
            for secret in secrets:
                matching=[m for m in mounts if m==secret or (isinstance(m,dict) and m.get('source')==secret
                          and m.get('target',secret) in (secret,'/run/secrets/'+secret))]
                b.require(len(matching)==1,'Assurance secret source/target differs')
                definition=document.get('secrets',{}).get(secret,{})
                b.require(isinstance(definition,dict) and definition.get('file'),'Assurance requires operator credential files')
                variable='SPANOPTICON_SECRET_'+secret.upper().replace('-','_')+'_FILE'
                expression='${'+variable+':?provide credential file path}'
                if definition['file'] != expression:
                    chosen=os.environ.get(variable)
                    b.require(chosen and Path(definition['file']).resolve()==Path(chosen).resolve(),
                              'Assurance credential file must match the operator-selected path')
    b.require(adapters>0,'At least one component must enforce the selected assurance contract')
    return document


def kube_documents(raw):
    b.require(len(raw)<=LIMIT,'Rendered manifest exceeds size limit')
    try:
        values=list(yaml.load_all(raw,Loader=StrictLoader))
    except yaml.YAMLError:
        raise ValueError('Invalid rendered Kubernetes YAML') from None
    items=[]
    for value in values:
        if value is None:continue
        b.require(isinstance(value,dict),'Expected Kubernetes objects')
        items.extend(value['items'] if value.get('kind')=='List' else [value])
    return items


def helm_check(raw,lock,assurance,namespace):
    items=kube_documents(raw)
    b.require(0<len(items)<=1024,'Invalid Kubernetes object count')
    expected,secrets=settings(assurance); deployed={};identities=set();adapters=0
    for item in items:
        b.require(isinstance(item,dict) and item.get('kind') in ('Deployment','Service','ConfigMap','PersistentVolumeClaim','NetworkPolicy'),'Unsupported Kubernetes resource; migrations/hooks require separate qualification')
        meta=item.get('metadata',{})
        b.require(meta.get('namespace',namespace)==namespace,'Resource targets another namespace')
        b.require(not any(key.startswith('helm.sh/hook') for key in meta.get('annotations',{})),'Helm hooks are unsupported')
        identity=(item.get('apiVersion'),item['kind'],meta.get('name'))
        b.require(identity not in identities and b.matches(r'[a-z0-9][a-z0-9.-]{0,62}',meta.get('name')),'Duplicate or invalid Kubernetes resource')
        identities.add(identity)
        if item['kind']!='Deployment':continue
        name=meta['name'];pod=item.get('spec',{}).get('template',{}).get('spec',{})
        containers=pod.get('containers',[])
        b.require(name in lock['components'] and name not in deployed and len(containers)==1 and not pod.get('initContainers') and not pod.get('ephemeralContainers'),'Expected one deployment/container per declared component')
        container=containers[0]
        b.require(container.get('image')==lock['components'][name]['image'],'Helm image differs from bundle')
        b.require(not pod.get('hostNetwork') and not pod.get('hostPID') and not pod.get('hostIPC')
                  and not container.get('securityContext',{}).get('privileged'),'Host privilege is unsupported')
        b.require(container.get('readinessProbe') and container.get('livenessProbe'),'Readiness/liveness probes required')
        env_entries=container.get('env',[])
        env={entry['name']:entry.get('value') for entry in env_entries}
        b.require(len(env)==len(env_entries),'Duplicate container environment entry')
        if 'SPANOPTICON_ASSURANCE_MODE' in env:
            b.require(not container.get('envFrom'),'Assurance envFrom overrides are unsupported')
            adapters+=1
            actual={k:v for k,v in env.items() if k.startswith('SPANOPTICON_ASSURANCE_') or k.startswith('SPANOPTICON_STRIPE_KYC_') or k.startswith('SPANOPTICON_VERIFICATION_')}
            b.require(actual==expected,'Assurance configuration differs')
            volumes={v['name']:v for v in pod.get('volumes',[])}
            mounts=container.get('volumeMounts',[])
            for secret in secrets:
                matches=[m for m in mounts if m.get('mountPath')=='/run/secrets/'+secret and m.get('readOnly') is True]
                b.require(len(matches)==1,'Missing assurance secret mount')
                volume=volumes.get(matches[0]['name'],{}).get('secret',{})
                b.require(volume.get('secretName')==secret and volume.get('optional',False) is False
                          and volume.get('items')==[{'key':'token','path':secret}] and matches[0].get('subPath')==secret,'Assurance secret source differs')
        deployed[name]=container['image']
    b.require(set(deployed)==set(lock['components']) and adapters>0,'Helm component/assurance inventory differs')
    return items


def deploy(layout,engine,name,assurance,output,*,apply=False,context=None,timeout=180):
    b.require(engine in ('compose','helm') and b.matches(NAME,name),'Invalid deployment engine or test name')
    b.require(type(timeout) is int and 1<=timeout<=900,'Timeout must be 1..900 seconds')
    root,receipt,lock=selection(layout)
    payload=assets(root,lock)
    settings(assurance)  # Keep existing assurance contract; no provider activation is implied.
    destination=Path(output).absolute();destination.mkdir(mode=0o700)
    env=dict(os.environ)
    for key in list(env):
        if key.startswith('COMPOSE_'):env.pop(key)
    # Freeze verified assets in a private, uniquely reserved directory; never load ambient overrides.
    compose=destination/'compose.json';compose.write_bytes(payload['compose'])
    chart=destination/'chart.tgz';chart.write_bytes(payload['helm'])
    if engine=='compose':
        prefix=['docker']
        b.require(context and not context.startswith('-'),'An explicit Docker context is required')
        prefix+=['--context',context,'compose','--env-file',os.devnull,'--project-directory',str(destination),'-p',name,'-f',str(compose)]
        compose_check(b.document(payload['compose']),lock,assurance)
        normalized=b.document(command(prefix+['config','--format','json'],env=env))
        compose_check(normalized,lock,assurance)
        normalized.pop('name',None)
        # Use the normalized bytes for execution; prevent another interpolation pass.
        frozen=destination/'compose.resolved.json';frozen.write_bytes(canonical(normalized))
        prefix[-1]=str(frozen)
        resolved=b.document(command(prefix+['config','--no-interpolate','--format','json'],env=env))
        compose_check(resolved,lock,assurance)
        if apply:
            # Dollar escaping avoids environment re-expansion in frozen service commands/settings.
            frozen.write_text(json.dumps(normalized).replace('$','$$'))
            command(prefix+['up','--detach','--no-build','--pull','always','--wait','--wait-timeout',str(timeout)],env=env,timeout=timeout+120)
            observed=command(prefix+['ps','--format','json'],env=env)
        else:observed=b''
        rendered=canonical(normalized)
    else:
        b.require(context and not context.startswith('-'),'An explicit Kubernetes context is required')
        rendered=command(['helm','template',name,str(chart),'--namespace',name,'--kube-context',context],env=env)
        items=helm_check(rendered,lock,assurance,name)
        expected=destination/'helm.expected.json';expected.write_bytes(canonical(items))
        gate=destination/'helm-post-renderer'
        script=Path(__file__).resolve().parents[1]/'scripts/helm_bundle_gate.py'
        gate.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(script))+' '+shlex.quote(str(expected))+'\n')
        gate.chmod(0o700)
        if apply:
            command(['helm','upgrade','--install',name,str(chart),'--namespace',name,'--kube-context',context,
                     '--create-namespace','--no-hooks','--skip-crds','--wait','--timeout',str(timeout)+'s','--post-renderer',str(gate)],env=env,timeout=timeout+120)
            observed=command(['helm','status',name,'--namespace',name,'--kube-context',context,'--output','json'],env=env)
        else:observed=b''
    result={'schema':1,'channel':'test','engine':engine,'name':name,'context':context,
            'bundle_reference':receipt['bundle_reference'],'candidate':lock['candidate'],
            'images':{n:c['image'] for n,c in lock['components'].items()},
            'rendered_sha256':hashlib.sha256(rendered).hexdigest(),
            'assurance_config_sha256':hashlib.sha256(canonical(assurance)).hexdigest(),
            'result':'tool-wait-succeeded' if apply else 'validated-plan',
            'observation_sha256':hashlib.sha256(observed).hexdigest() if apply else None}
    (destination/'receipt.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
    return result
