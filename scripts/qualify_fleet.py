#!/usr/bin/env python3
"""Validate reviewed fleet inputs or qualify their exact bytes in disposable runtimes."""
import argparse
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deployment.qualify import assemble_input,qualify


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,default=Path('fleet'))
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--validate-only',action='store_true')
    parser.add_argument('--kube-context')
    args=parser.parse_args()
    if args.validate_only:
        assemble_input(args.inputs,args.output,os.environ)
        print('Fleet assets validated; no runtime qualification or publication')
    else:
        qualify(args.inputs,args.output,kube_context=args.kube_context)
        print('Exact fleet bundle passed Compose readiness, Helm install and upgrade')


if __name__=='__main__':main()
