"""Stream a verification archive to or from a private replica directory over SSH."""
import argparse
from datetime import datetime
import fcntl
import hashlib
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from .backup_expiry import ARCHIVE


def transfer(directory, name, digest, *, upload, stream):
    match = ARCHIVE.fullmatch(name)
    if not match or not re.fullmatch("[0-9a-f]{64}", digest):
        raise ValueError("Expected a finalized archive name and SHA-256 digest")
    datetime.strptime(match[1], "%Y%m%dT%H%M%SZ")
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if upload else fcntl.LOCK_SH)
        if upload:
            with tempfile.NamedTemporaryFile(dir=directory) as staged:
                shutil.copyfileobj(stream, staged)
                staged.flush()
                staged.seek(0)
                if hashlib.file_digest(staged, "sha256").hexdigest() != digest:
                    raise ValueError("Archive digest mismatch")
                os.fsync(staged.fileno())
                os.link(staged.name, name, dst_dir_fd=fd)
                os.fsync(fd)
        else:
            with os.fdopen(os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd), "rb") as archive:
                if hashlib.file_digest(archive, "sha256").hexdigest() != digest:
                    raise ValueError("Archive digest mismatch")
                archive.seek(0)
                shutil.copyfileobj(archive, stream)
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["put", "get"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("name")
    parser.add_argument("sha256")
    args = parser.parse_args()
    upload = args.operation == "put"
    transfer(args.directory, args.name, args.sha256, upload=upload,
             stream=sys.stdin.buffer if upload else sys.stdout.buffer)


if __name__ == "__main__":
    main()
