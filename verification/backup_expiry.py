"""Expire finalized local verification archives by their UTC snapshot time."""
import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat

ARCHIVE = re.compile(r"verification-(\d{8}T\d{6}Z)-[0-9a-f]{32}\.tar")


def expire(directory, days, *, apply=False, now=None):
    if days < 1:
        raise ValueError("Retention must be at least one day")
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    count = 0
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if apply else fcntl.LOCK_SH)
        for name in os.listdir(fd):
            match = ARCHIVE.fullmatch(name)
            if not match or not stat.S_ISREG(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
                continue
            try:
                captured = datetime.strptime(match[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                continue  # An impossible date cannot establish an archive age.
            if captured <= cutoff:
                count += 1
                if apply:
                    os.unlink(name, dir_fd=fd)
        if apply:
            os.fsync(fd)
    finally:
        os.close(fd)
    return {"eligible_archives": count, "applied": apply}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--retention-days", required=True, type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(expire(args.directory, args.retention_days, apply=args.apply)))


if __name__ == "__main__":
    main()
