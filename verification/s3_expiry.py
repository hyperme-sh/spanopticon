"""Expire archive versions and delete markers below one verification backup prefix."""
import argparse
from datetime import datetime, timedelta, timezone
import json

from .backup_expiry import ARCHIVE


def expire(client, bucket, prefix, days, *, apply=False):
    if days < 1 or not prefix.endswith("/") or any(p in ("", ".", "..") for p in prefix[:-1].split("/")):
        raise ValueError("Supply a nonempty directory prefix and at least one retention day")
    if client.get_bucket_versioning(Bucket=bucket).get("Status") != "Enabled":
        raise ValueError("Expected a versioning-enabled bucket")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    old_versions, current_versions, markers = [], [], []
    pages = client.get_paginator("list_object_versions").paginate(Bucket=bucket, Prefix=prefix, Delimiter="/")
    for page in pages:
        for group in ("Versions", "DeleteMarkers"):
            for obj in page.get(group, []):
                key = obj["Key"]
                match = ARCHIVE.fullmatch(key.removeprefix(prefix))
                if not key.startswith(prefix) or not match:
                    continue
                try:
                    timestamp = datetime.strptime(match[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if timestamp > cutoff:
                    continue
                target = markers if group == "DeleteMarkers" else current_versions if obj["IsLatest"] else old_versions
                target.append((key, obj["VersionId"]))
    if apply:
        # Finish listing before mutating pagination cursors. Retain markers if deleting data fails.
        for group in (old_versions, current_versions, markers):
            for key, version in group:
                client.delete_object(Bucket=bucket, Key=key, VersionId=version)
    return {"versions": len(old_versions) + len(current_versions), "delete_markers": len(markers), "applied": apply}


def main():
    import boto3

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bucket")
    parser.add_argument("prefix")
    parser.add_argument("--retention-days", required=True, type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(expire(boto3.client("s3"), args.bucket, args.prefix, args.retention_days, apply=args.apply)))


if __name__ == "__main__":
    main()
