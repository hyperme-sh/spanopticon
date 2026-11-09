#!/usr/bin/env python3
"""Resolve a GHCR test selector once and download a verified deployment bundle."""
import argparse
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deployment.registry import GHCR, fetch_test
from deployment.archive import export_test


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository',required=True)
    parser.add_argument('--selector',default='test')
    parser.add_argument('--next',required=True,dest='next_version')
    destination=parser.add_mutually_exclusive_group(required=True)
    destination.add_argument('--output',type=Path)
    destination.add_argument('--archive',type=Path,help='Export the verified download as .tar.gz or .zip')
    args=parser.parse_args()
    try:
        client=GHCR(args.repository,os.environ.get('GHCR_USERNAME'),os.environ.get('GHCR_TOKEN'))
        if args.archive is not None:
            receipt=export_test(args.repository,args.selector,args.next_version,args.archive,client)
        else:
            receipt=fetch_test(args.repository,args.selector,args.next_version,args.output,client)
    except (ValueError,OSError,TypeError,KeyError,RecursionError) as error:
        parser.exit(1,f'Test fetch rejected: {error}\n')
    print(receipt['bundle_reference'])


if __name__=='__main__':main()
