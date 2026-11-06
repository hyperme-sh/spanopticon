"""Utilities for the Cedar TEST network; no Fyber operator dependency."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3

ISSUER = "https://cedar.test/identity"
AUDIENCE = "cedar-operator"


def config():
    return json.loads(Path(os.environ["OPERATOR_CONFIG_FILE"]).read_text())


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


@contextmanager
def transaction():
    db = sqlite3.connect(os.environ["OPERATOR_DB"], timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def initialize():
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS devices (
            id TEXT PRIMARY KEY, tenant TEXT NOT NULL, organization TEXT NOT NULL,
            serial TEXT NOT NULL, enroll_hash TEXT UNIQUE, secret_hash TEXT UNIQUE,
            wg_public_key TEXT UNIQUE, host_name TEXT, heartbeat REAL,
            UNIQUE(tenant, serial))""")
        db.execute("""CREATE TABLE IF NOT EXISTS leases (
            id TEXT PRIMARY KEY, tenant TEXT NOT NULL, subject TEXT NOT NULL,
            device TEXT NOT NULL, request_id TEXT NOT NULL, request TEXT NOT NULL,
            state TEXT NOT NULL, created REAL NOT NULL, result TEXT, event TEXT,
            settlement TEXT, UNIQUE(tenant, subject, request_id))""")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_lease ON leases(device) WHERE state='starting'")
