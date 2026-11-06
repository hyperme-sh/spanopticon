"""Verify owner-qualified OCI image archives before a byte-preserving test copy."""
import hashlib
from pathlib import Path
import re
import shutil
import tarfile
import zipfile
from deployment import bundle as b

OWNERS = {
 'hypermesh-gateway': ('.github/workflows/package.yml','tested-runtime-image'),
 'hypermesh-plane': ('.github/workflows/core.yml','tested-core-runtime-image'),
}
LIMIT=2*1024*1024*1024
INDEX='application/vnd.oci.image.index.v1+json'
CONFIG='application/vnd.oci.image.config.v1+json'


def sha(path):
    with Path(path).open('rb') as stream:return 'sha256:'+hashlib.file_digest(stream,'sha256').hexdigest()


def pin(value):
    b.keys(value,('schema','owner','source_revision','run_id','artifact_id','artifact_digest','archive_digest',
                  'manifest_digest','config_digest','destination'),'owner image pin')
    owner=value['owner'];b.require(owner in OWNERS and type(value['schema']) is int and value['schema']==1,'Unsupported image owner')
    b.require(b.matches(r'[0-9a-f]{40}',value['source_revision']),'Invalid source revision')
    for key in ('run_id','artifact_id'):b.require(type(value[key]) is int and value[key]>0,'Invalid source artifact identity')
    for key in ('artifact_digest','archive_digest','manifest_digest','config_digest'):b.require(b.matches(b.DIGEST,value[key]),'Invalid digest')
    b.require(value['destination']=='ghcr.io/hyperme-sh/spanopticon-'+owner,'Unexpected image destination')
    return value


def provenance(value,run,artifact):
    value=pin(value);workflow,name=OWNERS[value['owner']]
    b.require(run.get('id')==value['run_id'] and run.get('head_sha')==value['source_revision']
              and run.get('head_branch')=='main' and run.get('event')=='push'
              and run.get('path')==workflow and run.get('status')=='completed' and run.get('conclusion')=='success',
              'Image source is not the pinned successful main qualification')
    repo='hyperme-sh/'+value['owner']
    b.require(run.get('repository',{}).get('full_name')==repo and run.get('head_repository',{}).get('full_name')==repo,
              'Image source repository mismatch')
    linked=artifact.get('workflow_run',{})
    b.require(artifact.get('id')==value['artifact_id'] and artifact.get('name')==name and artifact.get('expired') is False
              and artifact.get('digest')==value['artifact_digest'] and linked.get('id')==value['run_id']
              and linked.get('head_sha')==value['source_revision'] and linked.get('head_branch')=='main'
              and linked.get('repository_id')==run['repository']['id']
              and linked.get('head_repository_id')==run['repository']['id'],'Artifact provenance mismatch')


def unpack(value,archive,output):
    value=pin(value);b.require(sha(archive)==value['artifact_digest'],'Owner artifact ZIP digest mismatch')
    output=Path(output);output.mkdir(mode=0o700)
    with zipfile.ZipFile(archive) as zipped:
        names=zipped.namelist()
        b.require(len(names)==len(set(names)) and set(names) in (
            {'image.tar','image.sha256','image.ref'},
            {'image.tar','image.sha256','image.ref','runtime-inputs.json'}),'Unexpected runtime artifact contents')
        for item in zipped.infolist():
            b.require(item.file_size<=(LIMIT if item.filename=='image.tar' else b.MAX_METADATA),'Artifact member too large')
            b.require((item.external_attr>>16)&0o170000 not in (0o120000,),'Artifact symlink refused')
            with zipped.open(item) as source,(output/item.filename).open('xb') as target:shutil.copyfileobj(source,target)
    b.require((output/'image.sha256').read_text().strip()==value['archive_digest'].removeprefix('sha256:'),
              'Owner checksum differs from pinned archive')
    return verify(value,output/'image.tar')


def verify(value,path):
    value=pin(value);path=Path(path)
    b.require(path.stat().st_size<=LIMIT and sha(path)==value['archive_digest'],'Image archive digest mismatch')
    verified={}
    runtime_configs=set()
    with tarfile.open(path,mode='r:') as archive:
        members={}
        for item in archive:
            b.require(len(members)<4096 and item.name not in members and
                      re.fullmatch(r'(?:index.json|manifest.json|repositories|oci-layout|blobs/?|blobs/sha256/?|blobs/sha256/[0-9a-f]{64})',item.name)
                      and (item.isfile() or item.isdir()),'Unsafe OCI archive member')
            members[item.name]=item
        def read(name,limit):
            member=members.get(name)
            b.require(member and member.isfile() and member.size<=limit,'Missing/oversized OCI metadata')
            with archive.extractfile(member) as stream:return stream.read(limit+1)
        def blob(descriptor,metadata=False):
            digest=descriptor.get('digest');size=descriptor.get('size')
            b.require(b.matches(b.DIGEST,digest) and type(size) is int and 0<size<=LIMIT,'Invalid OCI descriptor')
            name='blobs/sha256/'+digest.split(':')[1];member=members.get(name)
            b.require(member and member.isfile() and member.size==size,'Missing or mismatched image blob')
            if digest not in verified:
                with archive.extractfile(member) as stream:actual='sha256:'+hashlib.file_digest(stream,'sha256').hexdigest()
                b.require(actual==digest,'Image blob digest mismatch');verified[digest]=size
            return b.document(read(name,b.MAX_METADATA)) if metadata else None
        index=b.document(read('index.json',b.MAX_METADATA))
        b.require(index.get('schemaVersion')==2 and len(index.get('manifests',[]))==1,'Expected one OCI image root')
        root=index['manifests'][0];b.require(root['digest']==value['manifest_digest'],'Image root differs from pin')
        def visit(descriptor,depth=0):
            b.require(depth<8,'OCI graph depth exceeded')
            document=blob(descriptor,True)
            if descriptor.get('mediaType')==INDEX:
                b.require(0<len(document.get('manifests',[]))<=16,'Invalid image index')
                for child in document['manifests']:visit(child,depth+1)
            else:
                b.require(descriptor.get('mediaType')==b.OCI and document.get('schemaVersion')==2,'Unsupported image manifest')
                config=document['config'];data=blob(config,True)
                if config['digest']==value['config_digest']:
                    runtime_configs.add(config['digest'])
                    labels=data.get('config',{}).get('Labels',{})
                    b.require(config.get('mediaType')==CONFIG and data.get('os')=='linux' and data.get('architecture')=='amd64'
                              and labels.get('org.opencontainers.image.revision')==value['source_revision']
                              and labels.get('org.opencontainers.image.source')=='https://github.com/hyperme-sh/'+value['owner'],
                              'Image source labels/platform mismatch')
                b.require(len(document.get('layers',[]))<=128,'Too many image layers')
                for layer in document['layers']:blob(layer)
        visit(root)
        b.require(value['config_digest'] in runtime_configs,'Expected runtime config absent')
    return {'schema':1,'owner':value['owner'],'source_revision':value['source_revision'],
            'image':value['destination']+'@'+value['manifest_digest'],'blobs':verified,
            'archive_digest':value['archive_digest'],'run_id':value['run_id'],'artifact_id':value['artifact_id']}
