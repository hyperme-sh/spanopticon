"""Recover a synthetic verification archive from a previous Azure blob version."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tarfile
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from . import authority, erasure, store, worker


def qualify(root, account, container):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"cold/verification-{stamp}-{uuid4().hex}.tar"
    upload = ["az", "storage", "blob", "upload", "--account-name", account, "--container-name", container,
              "--name", name, "--auth-mode", "login", "--only-show-errors", "--output", "json"]
    state, inbox, secret = (root / n for n in ("state.db", "mailbox.db", "authority"))
    secret.write_bytes(os.urandom(32))
    with patch.dict(os.environ, VERIFICATION_DB=str(state), VERIFICATION_MAILBOX=str(inbox),
                    VERIFICATION_KEY_FILE=str(secret), VERIFICATION_DELIVERY_MODE="local"):
        store.migrate()
        retired, retained = (store.principal_scope("synthetic", tenant, "owner") for tenant in ("one", "two"))
        for i, scope in enumerate((retired, retained)):
            cid = store.create(scope, f"person{i}@example.test", "first")["challenge_id"]
            if not worker.deliver_one() or not store.complete(scope, cid, store.delivery_code(store.key(), cid)):
                raise RuntimeError("Synthetic evidence setup failed")
        archive = root / "snapshot.tar"
        with tarfile.open(archive, "w") as tar:
            for path in (state, inbox):
                tar.add(path, arcname=path.name)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        first = json.loads(subprocess.check_output(upload + ["--file", str(archive), "--if-none-match", "*"]))
        version = first.get("version_id") or first.get("versionId")
        if not version or not first.get("etag"):
            raise RuntimeError(f"Versioned upload receipt missing for {name}")
        archive.write_bytes(b"synthetic replacement")
        second = json.loads(subprocess.check_output(upload + ["--file", str(archive), "--overwrite", "true",
                                                               "--if-match", first["etag"]]))
        latest = second.get("version_id") or second.get("versionId")
        if not latest or latest == version:
            raise RuntimeError("Expected a distinct replacement version")
        authority.record(secret, retired)
        for path in (state, inbox, archive):
            path.unlink()
        fetch = Path(__file__).with_name("azure") / "fetch-version.sh"
        subprocess.run(["bash", str(fetch), account, container, name, version, digest, str(archive)], check=True)
        with tarfile.open(archive) as tar:
            for path in (state, inbox):
                member = tar.getmember(path.name)
                if not member.isfile():
                    raise RuntimeError("Expected regular database file")
                path.write_bytes(tar.extractfile(member).read())
        try:
            store.ready()
        except RuntimeError as exc:
            if str(exc) != "Verification erasure replay required":
                raise
        else:
            raise RuntimeError("Restored stale evidence passed readiness")
        erasure.replay()
        store.ready()
        if store.evidence(retired)["verified"] or not store.evidence(retained)["verified"]:
            raise RuntimeError("Recovered tenant evidence mismatch")
        with sqlite3.connect(inbox) as db:
            if db.execute("SELECT address FROM message").fetchall() != [("person1@example.test",)]:
                raise RuntimeError("Recovered mailbox mismatch")
        return {"account": account, "container": container, "blob": name, "restored_version": version,
                "current_version": latest, "sha256": digest}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("account")
    parser.add_argument("container")
    args = parser.parse_args()
    with TemporaryDirectory() as tmp:
        print(json.dumps(qualify(Path(tmp), args.account, args.container)))
