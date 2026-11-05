#!/usr/bin/env python3
"""Resolve a GHCR test selector once and download a verified deployment bundle."""
import argparse
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deployment.registry import GHCR, fetch_test


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository',required=True)
    parser.add_argument('--selector',default='test')
    parser.add_argument('--next',required=True,dest='next_version')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    try:
        client=GHCR(args.repository,os.environ.get('GHCR_USERNAME'),os.environ.get('GHCR_TOKEN'))
        receipt=fetch_test(args.repository,args.selector,args.next_version,args.output,client)
    except (ValueError,OSError,TypeError,KeyError,RecursionError) as error:
        parser.exit(1,f'Test fetch rejected: {error}\n')
    print(receipt['bundle_reference'])


if __name__=='__main__':main()
