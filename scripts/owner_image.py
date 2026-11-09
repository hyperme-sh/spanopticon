#!/usr/bin/env python3
"""Download verified owner images, or copy their exact bytes to GHCR test tags."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deployment import bundle as b
from deployment import owner_image as image


def command(args, **kwargs):
    result=subprocess.run(args,stderr=subprocess.PIPE,timeout=1200,**kwargs)
    b.require(result.returncode==0,'Owner image command failed (credentials and response omitted)')
    return result.stdout



def owner_token_scope(value,document):
    # Public gateway downloads keep their proven coordinator job token path.
    if value['owner']!='hypermesh-plane':return
    repositories=document.get('repositories',[])
    b.require(document.get('total_count')==1 and len(repositories)==1
              and repositories[0].get('full_name')=='hyperme-sh/hypermesh-plane',
              'Artifact token must be scoped only to hyperme-sh/hypermesh-plane')


def download(value,output):
    if value['schema'] == 2:
        output.mkdir(mode=0o700)
        reference = value['source']+'@'+value['manifest_digest']
        with tempfile.TemporaryDirectory(prefix='owner-layout-') as scratch:
            layout = Path(scratch)/'layout'
            command(['oras','cp','--to-oci-layout',reference,str(layout)+':verified'])
            index = b.document((layout/'index.json').read_bytes())
            # Name the verified root for containerd imports during Helm qualification.
            roots = [d for d in index['manifests'] if d.get('digest') == value['manifest_digest']]
            b.require(roots, 'Pinned manifest missing from registry layout')
            index['manifests'] = [roots[0]]
            index['manifests'][0].setdefault('annotations',{})['org.opencontainers.image.ref.name']='docker.io/library/qualified-owner:ci'
            (layout/'index.json').write_text(json.dumps(index))
            with tarfile.open(output/'image.tar','w') as archive:
                for item in [layout/'index.json', layout/'oci-layout', *sorted((layout/'blobs'/'sha256').glob('[0-9a-f]'*64))]:
                    archive.add(item,arcname=item.relative_to(layout))
        receipt=image.verify(value,output/'image.tar')
        (output/'verified.json').write_text(json.dumps(receipt)+'\n')
        return receipt
    repo='hyperme-sh/'+value['owner']
    def get(path):
        return b.document(command(['gh','api','repos/'+repo+path],stdout=subprocess.PIPE))
    image.provenance(value,get('/actions/runs/'+str(value['run_id'])),get('/actions/artifacts/'+str(value['artifact_id'])))
    with tempfile.TemporaryDirectory(prefix='owner-image-') as directory:
        zipped=Path(directory)/'artifact.zip'
        with zipped.open('xb') as stream:
            command(['gh','api','repos/'+repo+'/actions/artifacts/'+str(value['artifact_id'])+'/zip'],stdout=stream)
        receipt=image.unpack(value,zipped,output)
    (output/'verified.json').write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
    return receipt



def missing_manifest(error,reference):
    # ORAS 1.3.0 maps a registry HEAD 404 to this exact ErrNotFound diagnostic.
    # Do not accept arbitrary "not found", auth, proxy, or transport errors.
    return error.strip() == (
        f'Error response from registry: failed to find "{reference}": {reference}: not found'
    )


def publish(value,archive,env):
    b.require(env.get('GITHUB_REPOSITORY')=='hyperme-sh/spanopticon'
              and env.get('GITHUB_EVENT_NAME')=='push' and env.get('GITHUB_REF')=='refs/heads/main'
              and env.get('GITHUB_WORKFLOW_REF')=='hyperme-sh/spanopticon/.github/workflows/owner-images.yml@refs/heads/main',
              'Only the main owner image workflow may publish')
    run=env.get('GITHUB_RUN_ID','')
    b.require(b.matches(r'[1-9][0-9]*',run),'Invalid test run identity')
    receipt=image.verify(value,archive)
    candidate='0.24.0-test.'+run
    destination=value['destination']+':'+candidate
    token,user=env.get('GHCR_TOKEN'),env.get('GHCR_USERNAME')
    b.require(token and user,'GHCR credentials required')
    with tempfile.TemporaryDirectory(prefix='owner-image-copy-') as directory:
        root=Path(directory);config=str(root/'auth.json')
        command(['oras','login','ghcr.io','--registry-config',config,'--username',user,'--password-stdin'],input=token.encode(),stdout=subprocess.DEVNULL)
        result=subprocess.run(['oras','manifest','fetch','--descriptor',destination,'--registry-config',config],capture_output=True,timeout=120)
        if result.returncode==0:
            b.require(b.document(result.stdout).get('digest')==value['manifest_digest'],'Test tag already contains different bytes')
        else:
            # Authentication/transport errors are never treated as absence.
            error=result.stderr.decode(errors='replace')
            b.require(missing_manifest(error,destination),
                      'Cannot establish whether test tag exists')
            command(['oras','cp','--from-oci-layout',str(archive)+'@'+value['manifest_digest'],destination,'--to-registry-config',config],stdout=subprocess.DEVNULL)
        actual=b.document(command(['oras','manifest','fetch','--descriptor',destination,'--registry-config',config],stdout=subprocess.PIPE))
        b.require(actual.get('digest')==value['manifest_digest'],'Published tag digest differs')
        layout=root/'readback'
        command(['oras','cp','--to-oci-layout',receipt['image'],str(layout)+':verified','--from-registry-config',config],stdout=subprocess.DEVNULL)
        for digest,size in receipt['blobs'].items():
            blob=layout/'blobs/sha256'/digest.split(':')[1]
            b.require(blob.stat().st_size==size and image.sha(blob)==digest,'Published image blob differs')
    return {**receipt,'candidate':candidate,'verification':'all image blobs read back','stable_published':False}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['download','publish'])
    parser.add_argument('--pin',type=Path,required=True);parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--require-owner-token-scope',action='store_true')
    args=parser.parse_args();value=image.pin(b.document(args.pin.read_bytes()))
    if args.action=='download':
        if args.require_owner_token_scope and value['owner']=='hypermesh-plane':
            owner_token_scope(value,b.document(command(['gh','api','installation/repositories?per_page=100'],stdout=subprocess.PIPE)))
        download(value,args.directory)
    else:
        receipt=publish(value,args.directory/'image.tar',os.environ)
        (args.directory/'publication.json').write_text(json.dumps(receipt,indent=2)+'\n')


if __name__=='__main__':main()
