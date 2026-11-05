#!/usr/bin/env python3
"""Prepare Compose and Helm assets for digest-pinned HTTP plane adapters."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from deployment import bundle as b
from deployment.generate import write
from assurance import load


def read(path):
    with path.open('rb') as stream:data=stream.read(b.MAX_METADATA+1)
    b.require(len(data)<=b.MAX_METADATA,'Input metadata too large')
    return b.document(data)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('metadata','runtime-config','assurance-config','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    try:print(write(read(args.metadata),read(args.runtime_config),load(args.assurance_config),args.output))
    except (ValueError,OSError,TypeError,KeyError,RecursionError) as error:parser.exit(1,f'Plane preparation rejected: {error}\n')


if __name__=='__main__':main()
