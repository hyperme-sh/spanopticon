#!/usr/bin/env python3
"""Publish qualified main-run fleet artifacts; never publish a stable tag."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deployment.publish import publish


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact',required=True,type=Path)
    parser.add_argument('--receipt',required=True,type=Path)
    args=parser.parse_args()
    # Reserve the receipt first; failed attempts leave no success payload.
    with args.receipt.open('x',encoding='utf-8') as stream:
        result=publish(args.artifact)
        stream.write(json.dumps(result,sort_keys=True,indent=2)+'\n')
    print(result['bundle_reference'])


if __name__=='__main__':main()
