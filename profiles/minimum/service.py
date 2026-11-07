"""Tenant-scoped reference resource for the minimum profile."""
from contextlib import asynccontextmanager, closing
import os
from pathlib import Path
import sqlite3
from uuid import UUID

from fastapi import FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi import Depends
import jwt


def connect():
    return sqlite3.connect(os.environ["MINIMUM_DATABASE"], timeout=10)


@asynccontextmanager
async def lifespan(app):
    app.state.key = Path(os.environ["IDENTITY_PUBLIC_KEY_FILE"]).read_bytes()
    app.state.algorithm = os.environ["IDENTITY_ALGORITHM"]
    if app.state.algorithm not in ("EdDSA", "RS256"):
        raise ValueError("Unsupported issuer algorithm")
    app.state.issuer = os.environ["IDENTITY_ISSUER"]
    app.state.audience = os.environ["IDENTITY_AUDIENCE"]
    with closing(connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            db.execute("CREATE TABLE tenants (id TEXT PRIMARY KEY)")
            db.execute("PRAGMA user_version = 1")
        elif version != 1:
            raise RuntimeError("Unsupported database schema")
        db.execute("INSERT OR IGNORE INTO tenants VALUES (?)", (str(UUID(os.environ["MINIMUM_TENANT"])),))
    yield


app = FastAPI(title="Minimum tenant directory", version="1", lifespan=lifespan)


@app.get("/health/ready")
def ready():
    with closing(connect()) as db:
        db.execute("SELECT id FROM tenants LIMIT 1").fetchall()
    return {"ready": True}


@app.get("/v1/tenants/{tenant_id}")
def tenant(tenant_id: UUID, credential: HTTPAuthorizationCredentials = Depends(HTTPBearer())):
    try:
        claims = jwt.decode(credential.credentials, app.state.key, algorithms=[app.state.algorithm],
                            audience=app.state.audience, issuer=app.state.issuer,
                            options={"require": ["sub", "tenant_id", "exp", "iat"]})
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid access token") from None
    if claims["tenant_id"] != str(tenant_id):
        raise HTTPException(403, "Tenant access denied")
    with closing(connect()) as db:
        row = db.execute("SELECT id FROM tenants WHERE id = ?", (str(tenant_id),)).fetchone()
    if row is None:
        raise HTTPException(404, "Unknown tenant")
    return {"tenant_id": row[0]}
