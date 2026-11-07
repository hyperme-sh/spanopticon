"""SQLite persistence for the independent operator's local contact workflow."""
from contextlib import closing, contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from .challenge import check_code, code_digest


def connect():
    db = sqlite3.connect(os.environ["VERIFICATION_DB"], timeout=10)
    db.row_factory = sqlite3.Row
    return db


@contextmanager
def transaction():
    with closing(connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        yield db


def migrate():
    with transaction() as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            raise RuntimeError("Unknown verification schema")
        for target in range(version + 1, 3):
            sql = Path(__file__).with_name("migrations").joinpath(f"{target:03}.sql").read_text()
            for statement in sql.split(";"):
                if statement.strip():
                    db.execute(statement)
            db.execute(f"PRAGMA user_version = {target}")


def ready():
    with closing(connect()) as db:
        if db.execute("PRAGMA user_version").fetchone()[0] != 2:
            raise RuntimeError("Verification migration required")


def key():
    secret = Path(os.environ["VERIFICATION_KEY_FILE"]).read_bytes()
    if len(secret) != 32:
        raise ValueError("Expected a 32-byte verification secret")
    return secret


def delivery_code(secret, cid):
    # A random challenge ID determines one repeatable code for local delivery.
    raw = hmac.digest(secret, b"local-delivery-v1\0" + cid.encode(), "sha256")
    return f"{int.from_bytes(raw[:8], 'big') % 1_000_000:06d}"


class Conflict(Exception):
    pass


class RateLimited(Exception):
    pass


def principal_scope(issuer, tenant, subject):
    return hashlib.sha256(json.dumps([issuer, tenant, subject], separators=(",", ":")).encode()).hexdigest()


def delivery_method():
    mode = os.environ.get("VERIFICATION_DELIVERY_MODE", "local")
    if mode not in ("local", "operator"):
        raise ValueError("Unknown verification delivery mode")
    return "operator-delivery" if mode == "operator" else "local-test-delivery"


def hourly_limit(name, default):
    value = int(os.environ.get("VERIFICATION_LIMIT_" + name, default))
    if value < 1:
        raise ValueError("Verification limits must be positive")
    return value


def create(scope, address, request_key):
    with transaction() as db:
        now = int(time.time())
        prior = db.execute("SELECT * FROM challenge WHERE scope=? AND request_key=?", (scope, request_key)).fetchone()
        if prior:
            if prior["contact"] != address:
                raise Conflict()
            return {"challenge_id": prior["id"], "expires_at": prior["expires"]}
        # All three budgets are charged by the same insert under BEGIN IMMEDIATE.
        for predicate, args, limit in (
            ("scope=?", (scope,), hourly_limit("SUBJECT", 10)),
            ("contact=?", (address,), hourly_limit("RECIPIENT", 3)),
            ("1=1", (), hourly_limit("OPERATOR", 100)),
        ):
            count = db.execute(f"SELECT count(*) FROM challenge WHERE {predicate} AND created>?",
                               (*args, now - 3600)).fetchone()[0]
            if count >= limit:
                raise RateLimited()
        current = db.execute("SELECT * FROM contact WHERE scope=?", (scope,)).fetchone()
        revision = current["revision"] + 1 if current else 1
        keep = current["verified_until"] if current and current["address"] == address else 0
        db.execute("INSERT OR REPLACE INTO contact VALUES (?,?,?,?,?)",
                   (scope, revision, address, keep, current["method"] if keep else delivery_method()))
        db.execute("UPDATE challenge SET state='superseded' WHERE scope=? AND state='pending'", (scope,))
        cid = str(uuid4())
        secret = key()
        digest = code_digest(secret, cid, scope, revision, delivery_code(secret, cid))
        db.execute("""INSERT INTO challenge(id,scope,request_key,contact,revision,digest,created,expires,method)
                      VALUES (?,?,?,?,?,?,?,?,?)""", (cid, scope, request_key, address, revision, digest, now, now+600, delivery_method()))
        return {"challenge_id": cid, "expires_at": now+600}


def complete(scope, cid, submitted):
    with transaction() as db:
        now = int(time.time())
        row = db.execute("SELECT * FROM challenge WHERE id=? AND scope=?", (cid, scope)).fetchone()
        if not row or not row["delivered"]:
            return False
        contact = db.execute("SELECT revision FROM contact WHERE scope=?", (scope,)).fetchone()
        result = check_code(challenge_id=cid, scope=scope, revision=row["revision"], state=row["state"],
                            attempts=row["attempts"], expires_at=row["expires"], expected_digest=row["digest"],
                            key=key(), submitted_code=submitted, current_scope=scope, current_revision=contact[0],
                            now=now, limit=5)
        db.execute("UPDATE challenge SET state=?,attempts=? WHERE id=?", (result.state, result.attempts, cid))
        if result.accepted:
            db.execute("UPDATE contact SET verified_until=?, method=? WHERE scope=?", (now+86400, row["method"], scope))
        return row["method"] if result.accepted else False


def evidence(scope):
    with closing(connect()) as db:
        row = db.execute("SELECT verified_until, method FROM contact WHERE scope=?", (scope,)).fetchone()
        until = row[0] if row else 0
        return {"method": row["method"] if row else delivery_method(), "verified": until > int(time.time()), "expires_at": until}


if __name__ == "__main__":
    migrate()
