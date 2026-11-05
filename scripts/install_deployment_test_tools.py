#!/usr/bin/env python3
"""Install checksum-pinned native qualification tools into an explicit directory."""
import argparse
import hashlib
import io
from pathlib import Path
import tarfile
import urllib.request

TOOLS={
 'helm':('https://get.helm.sh/helm-v3.22.1-linux-amd64.tar.gz','896cf63684a42a723d61c980369d6e588330927deb13676ca0d85006dd6da0ea'),
 'kind':('https://github.com/kubernetes-sigs/kind/releases/download/v0.33.0/kind-linux-amd64','aee6151561422756b764a4ae28e7f44cda5af5a9eead3cc9985112b1de8d8e0d'),
 'kubectl':('https://dl.k8s.io/release/v1.37.0/bin/linux/amd64/kubectl','6129359f4e1f3848a5572ccb0b26cf28b8ca08cef38c95a765b2f64a2c961a2f'),
}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    args.output.mkdir(mode=0o700)
    for name,(url,sha) in TOOLS.items():
        with urllib.request.urlopen(url,timeout=60) as response:raw=response.read(128*1024*1024+1)
        if len(raw)>128*1024*1024 or hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('Tool download digest mismatch: '+name)
        if name=='helm':
            with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:raw=archive.extractfile('linux-amd64/helm').read()
        path=args.output/name
        with path.open('xb') as stream:stream.write(raw)
        path.chmod(0o700)


if __name__=='__main__':main()
