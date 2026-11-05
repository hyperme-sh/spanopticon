"""Prepare Compose and a Helm chart for HTTP plane adapters with explicit health commands."""
import gzip
import io
import json
from pathlib import Path
import sys
import tarfile
from deployment import bundle as b
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from new_plane import manifests

TEMPLATE=b'''{{- $resources := .Files.Get "resources.json" | fromJson -}}
{{- range $resources.items }}
{{- $_ := set .metadata "namespace" $.Release.Namespace }}
---
{{ toJson . }}
{{ end -}}
'''


def chart_bytes(candidate,items):
    files={
        'spanopticon-plane/Chart.yaml':('apiVersion: v2\nname: spanopticon-plane\nversion: '+candidate+'\ntype: application\n').encode(),
        'spanopticon-plane/resources.json':(json.dumps({'apiVersion':'v1','kind':'List','items':items},sort_keys=True,indent=2)+'\n').encode(),
        'spanopticon-plane/templates/resources.yaml':TEMPLATE,
    }
    target=io.BytesIO()
    with gzip.GzipFile(fileobj=target,mode='wb',mtime=0) as zipped:
        with tarfile.open(fileobj=zipped,mode='w') as archive:
            for name,raw in sorted(files.items()):
                info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o644;info.mtime=0
                archive.addfile(info,io.BytesIO(raw))
    return target.getvalue()


def prepare(metadata,runtime,assurance):
    b.keys(metadata,('schema','candidate','source_revision','components'),'plane metadata')
    b.require(type(metadata['schema']) is int and metadata['schema']==1,'Unsupported schema')
    b.require(b.matches(b.VERSION+r'-test\.(0|[1-9][0-9]*)',metadata['candidate']),'Expected test candidate')
    b.require(b.matches(r'[0-9a-f]{40}',metadata['source_revision']),'Expected source revision')
    components=metadata['components']
    b.require(isinstance(components,dict) and 0<len(components)<=128,'Expected components')
    b.require(isinstance(runtime,dict) and set(runtime)==set(components),'Runtime settings must cover exactly the declared components')
    compose={'services':{},'secrets':{}};items=[]
    for name,component in components.items():
        b.keys(component,('image','version','source_revision'),'component')
        b.require(b.matches(b.REPOSITORY+'@'+b.DIGEST,component['image']),'Expected pinned GHCR image')
        b.require(b.matches(r'[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}',component['version'])
                  and b.matches(r'[0-9a-f]{40}',component['source_revision']),'Invalid component identity')
        b.keys(runtime[name],('healthcheck',),'component runtime')
        check=runtime[name]['healthcheck']
        b.require(isinstance(check,list) and 2<=len(check)<=32 and check[0]=='CMD'
                  and all(isinstance(arg,str) and 0<len(arg)<=4096 and '\x00' not in arg for arg in check),'Supply a native CMD healthcheck for the image')
        generated=manifests(name,component['image'],assurance)
        service=generated['compose.yaml']['services'][name]
        service.pop('ports')  # No implicit host port collisions or public ingress.
        service['restart']='unless-stopped'
        service['healthcheck']={'test':check,'interval':'5s','timeout':'3s','retries':12,'start_period':'5s'}
        compose['services'][name]=service
        compose['secrets'].update(generated['compose.yaml']['secrets'])
        for item in generated['kubernetes.json']['items']:
            if item['kind']=='Namespace':continue
            item['metadata'].pop('namespace',None)  # The Helm release selects exactly one test namespace.
            items.append(item)
    return (json.dumps(compose,sort_keys=True,indent=2)+'\n').encode(),chart_bytes(metadata['candidate'],items)


def write(metadata,runtime,assurance,output):
    compose,chart=prepare(metadata,runtime,assurance)
    output=Path(output);output.mkdir(mode=0o700)
    (output/'compose.json').write_bytes(compose)
    (output/'chart.tgz').write_bytes(chart)
    (output/'metadata.json').write_text(json.dumps(metadata,sort_keys=True,indent=2)+'\n')
    return output
