"""Operator-held verification secret and durable subject erasure markers."""
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile


def read(path):
    data = Path(path).read_bytes()
    if len(data) == 32:  # Original secret format, before the first erasure.
        return data, frozenset()
    record = json.loads(data)
    if set(record) != {"version", "key", "retired"} or record["version"] != 1:
        raise ValueError("Invalid verification authority")
    if not re.fullmatch("[0-9a-f]{64}", record["key"]):
        raise ValueError("Invalid verification key")
    scopes = record["retired"]
    if not isinstance(scopes, list) or not all(re.fullmatch("[0-9a-f]{64}", s) for s in scopes):
        raise ValueError("Invalid verification erasure markers")
    return bytes.fromhex(record["key"]), frozenset(scopes)


def record(path, scope):
    path = Path(path)
    with path.with_name(path.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        key, scopes = read(path)
        if scope in scopes:
            return
        value = {"version": 1, "key": key.hex(), "retired": sorted(scopes | {scope})}
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            try:
                tmp.write(json.dumps(value).encode())
                tmp.flush()
                # Mounted secrets can be read by a different service UID.
                stat = path.stat()
                os.fchown(tmp.fileno(), stat.st_uid, stat.st_gid)
                os.fchmod(tmp.fileno(), stat.st_mode & 0o777)
                os.fsync(tmp.fileno())
                os.replace(tmp.name, path)
                directory = os.open(path.parent, os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                Path(tmp.name).unlink(missing_ok=True)
