"""Preview or apply time-based retention to the verification service database."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import time

# Challenges are also issuance counters. Never shorten the rolling abuse window.
MIN_RETENTION = 3600


def prune(path, retention_seconds, *, apply=False):
    if retention_seconds < MIN_RETENTION:
        raise ValueError("Retention must cover at least the 3600-second issuance window")
    # A typo must not create an empty database. Dry runs use SQLite read-only mode.
    uri = Path(path).resolve().as_uri() + ("?mode=rw" if apply else "?mode=ro")
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as db, db:
        db.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        if db.execute("PRAGMA user_version").fetchone()[0] != 2:
            raise ValueError("Expected verification database schema 2")
        now = int(time.time())
        deadline = now - retention_seconds
        # Keep uncertain deliveries available for inspection for the entire
        # selected period after expiry, even when they were claimed at expiry.
        expired = "expires <= ? AND created <= ?"
        bounds = (deadline, now - MIN_RETENTION)
        challenges = db.execute("SELECT count(*) FROM challenge WHERE " + expired, bounds).fetchone()[0]
        # Count contacts as they would look after challenge pruning. Keep a
        # current address for any surviving challenge or unexpired evidence.
        obsolete = f"""verified_until <= ? AND NOT EXISTS (
            SELECT 1 FROM challenge WHERE challenge.scope = contact.scope AND NOT ({expired})
        )"""
        contact_bounds = (deadline, *bounds)
        contacts = db.execute("SELECT count(*) FROM contact WHERE " + obsolete, contact_bounds).fetchone()[0]
        if apply:
            db.execute("DELETE FROM challenge WHERE " + expired, bounds)
            db.execute("DELETE FROM contact WHERE " + obsolete, contact_bounds)
        return {"applied": apply, "retention_seconds": retention_seconds,
                "challenge_rows": challenges, "contact_rows": contacts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retention-seconds", required=True, type=int)
    parser.add_argument("--apply", action="store_true", help="delete eligible rows in one transaction")
    args = parser.parse_args()
    print(json.dumps(prune(os.environ["VERIFICATION_DB"], args.retention_seconds, apply=args.apply)))


if __name__ == "__main__":
    main()
