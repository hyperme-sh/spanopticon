"""Rehearse version expiry and restore using Moto and synthetic verification data."""
import hashlib
import os
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
from unittest.mock import patch

import boto3
from moto import mock_aws

from . import authority, erasure, store, worker
from .s3_expiry import expire


@mock_aws
def qualify(root):
    client = boto3.client("s3", region_name="us-east-1")
    bucket = "verification-backups"
    client.create_bucket(Bucket=bucket)
    client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    old = "cold/verification-20000101T000000Z-" + "a" * 32 + ".tar"
    retained = old.replace("20000101", "20990101")
    for key in (old, old, "other/" + old, "cold/nested/" + old):
        client.put_object(Bucket=bucket, Key=key, Body=b"expired")
    client.delete_object(Bucket=bucket, Key=old)
    db, mailbox, secret = (root / n for n in ("state", "inbox", "key"))
    secret.write_bytes(os.urandom(32))
    with patch.dict(os.environ, VERIFICATION_DB=str(db), VERIFICATION_MAILBOX=str(mailbox),
                    VERIFICATION_KEY_FILE=str(secret), VERIFICATION_DELIVERY_MODE="local"):
        store.migrate()
        subjects = [store.principal_scope("issuer", tenant, "owner") for tenant in ("one", "two")]
        for idx, scope in enumerate(subjects):
            cid = store.create(scope, f"person{idx}@example.test", "key")["challenge_id"]
            assert worker.deliver_one() and store.complete(scope, cid, store.delivery_code(store.key(), cid))
        archive = root / "snapshot.tar"
        with tarfile.open(archive, "w") as tar:
            for path in (db, mailbox):
                tar.add(path, arcname=path.name)
        digest = hashlib.sha256(archive.read_bytes()).digest()
        version = client.put_object(Bucket=bucket, Key=retained, Body=archive.read_bytes())["VersionId"]
        client.put_object(Bucket=bucket, Key=retained, Body=b"another version")
        authority.record(secret, subjects[0])
        for path in (archive, db, mailbox):
            path.unlink()
        expected = {"versions": 2, "delete_markers": 1, "applied": False}
        assert expire(client, bucket, "cold/", 1) == expected
        assert expire(client, bucket, "cold/", 1, apply=True) == expected | {"applied": True}
        assert expire(client, bucket, "cold/", 1) == {"versions": 0, "delete_markers": 0, "applied": False}
        remaining = client.list_object_versions(Bucket=bucket)
        assert not remaining.get("DeleteMarkers")
        assert sorted(v["Key"] for v in remaining["Versions"]) == sorted([retained, retained, "other/" + old, "cold/nested/" + old])
        payload = client.get_object(Bucket=bucket, Key=retained, VersionId=version)["Body"].read()
        assert hashlib.sha256(payload).digest() == digest
        archive.write_bytes(payload)
        with tarfile.open(archive) as tar:
            tar.extractall(root, filter="data")
        erasure.replay()
        store.ready()
        assert not store.evidence(subjects[0])["verified"] and store.evidence(subjects[1])["verified"]


if __name__ == "__main__":
    with TemporaryDirectory() as tmp:
        qualify(Path(tmp))
