#!/usr/bin/env python3
"""Reject an install/upgrade render that differs from the validated offline plan."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deployment.runtime import LIMIT,canonical,kube_documents

try:
    data=sys.stdin.buffer.read(LIMIT+1)
    if len(data)>LIMIT or canonical(kube_documents(data))!=Path(sys.argv[1]).read_bytes():
        raise ValueError('Helm render differs from verified plan')
except Exception:
    print('Helm render rejected; no deployment output emitted',file=sys.stderr)
    sys.exit(1)
sys.stdout.buffer.write(data)
