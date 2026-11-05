#!/usr/bin/env python3
"""Validate or apply an already fetched test bundle using Compose or Helm."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from assurance import load
from deployment.runtime import deploy


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',required=True,type=Path)
    parser.add_argument('--engine',required=True,choices=['compose','helm'])
    parser.add_argument('--name',required=True)
    parser.add_argument('--context',required=True,help='Explicit Docker or Kubernetes context')
    parser.add_argument('--assurance-config',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--apply',action='store_true',help='Apply to the explicitly selected test context')
    parser.add_argument('--timeout',type=int,default=180)
    args=parser.parse_args()
    try:
        result=deploy(args.bundle,args.engine,args.name,load(args.assurance_config),args.output,
                      apply=args.apply,context=args.context,timeout=args.timeout)
    except (ValueError,OSError,TypeError,KeyError,RecursionError) as error:
        parser.exit(1,f'Test deployment rejected: {error}\n')
    print(result['result'])


if __name__=='__main__':main()
