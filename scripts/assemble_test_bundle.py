#!/usr/bin/env python3
"""Assemble a local OCI test bundle from existing artifacts; never publish or rebuild."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deployment import bundle as b


def assemble(metadata,compose,chart,repository,output):
    b.require(b.matches(b.REPOSITORY,repository),'Expected GHCR repository')
    b.keys(metadata,('schema','candidate','source_revision','components'),'assembly metadata')
    output=Path(output);output.mkdir(mode=0o700)
    blobs=output/'blobs/sha256';blobs.mkdir(parents=True)
    def store(raw,media_type):
        b.require(0<len(raw)<=b.MAX_TOTAL,'Invalid input asset size')
        digest='sha256:'+hashlib.sha256(raw).hexdigest()
        (blobs/digest.split(':')[1]).write_bytes(raw)
        return {'mediaType':media_type,'digest':digest,'size':len(raw)}
    def read(path):
        with Path(path).open('rb') as stream:data=stream.read(b.MAX_TOTAL+1)
        b.require(len(data)<=b.MAX_TOTAL,'Input asset too large')
        return data
    assets={'compose':store(read(compose),b.ASSETS['compose']),'helm':store(read(chart),b.ASSETS['helm'])}
    bundle={**metadata,'deployment_assets':assets}
    raw=(json.dumps(bundle,sort_keys=True,indent=2)+'\n').encode()
    declaration=store(raw,b.BUNDLE)
    manifest={'schemaVersion':2,'mediaType':b.OCI,'artifactType':b.BUNDLE,
              'config':store(b'{}',b.EMPTY),'layers':[declaration,*assets.values()]}
    descriptor=store((json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode(),b.OCI)
    reference=repository+'@'+descriptor['digest']
    b.write_lock(output,reference,output/'deployment.lock.json')
    (output/'oci-layout').write_text('{"imageLayoutVersion":"1.0.0"}\n')
    (output/'index.json').write_text(json.dumps({'schemaVersion':2,'manifests':[descriptor]},sort_keys=True)+'\n')
    return reference


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('metadata','compose','chart','output'):parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--repository',required=True)
    args=parser.parse_args()
    try:
        with args.metadata.open('rb') as stream:raw=stream.read(b.MAX_METADATA+1)
        b.require(len(raw)<=b.MAX_METADATA,'Metadata too large')
        print(assemble(b.document(raw),args.compose,args.chart,args.repository,args.output))
    except (ValueError,OSError,TypeError,KeyError,RecursionError) as error:parser.exit(1,f'Assembly rejected: {error}\n')


if __name__=='__main__':main()
