"""Authenticated contact-challenge endpoints for the local reference service."""
from contextlib import asynccontextmanager
import os
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from pydantic import BaseModel, ConfigDict, Field

from . import store


@asynccontextmanager
async def lifespan(app):
    store.ready()
    store.key()
    app.state.key = Path(os.environ["IDENTITY_PUBLIC_KEY_FILE"]).read_bytes()
    app.state.issuer = os.environ["IDENTITY_ISSUER"]
    app.state.audience = os.environ["IDENTITY_AUDIENCE"]
    app.state.algorithm = os.environ["IDENTITY_ALGORITHM"]
    if app.state.algorithm not in ("RS256", "EdDSA"):
        raise ValueError("Unsupported signature algorithm")
    yield


app = FastAPI(title="Local contact verification", lifespan=lifespan)


def scope(credential: HTTPAuthorizationCredentials = Depends(HTTPBearer())):
    try:
        claims = jwt.decode(credential.credentials, app.state.key, algorithms=[app.state.algorithm],
                            audience=app.state.audience, issuer=app.state.issuer,
                            options={"require": ["sub", "iat", "exp", "tenant_id"]})
        tenant = str(UUID(str(claims["tenant_id"])))
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(401, "Invalid access token") from None
    return store.principal_scope(app.state.issuer, tenant, claims["sub"])


class Contact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str = Field(pattern=r"^[a-z0-9._+-]+@[a-z0-9.-]+\.test$", max_length=254)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^[0-9]{6}$")


@app.get("/health/ready")
def ready():
    store.ready()
    return {"ready": True}


@app.post("/v1/challenges")
def create(body: Contact, idempotency_key: UUID = Header(), principal: str = Depends(scope)):
    try:
        return store.create(principal, body.address, str(idempotency_key))
    except store.Conflict:
        raise HTTPException(409, "Request key already used for another contact") from None
    except store.RateLimited:
        raise HTTPException(429, "Challenge limit reached") from None


@app.post("/v1/challenges/{cid}/complete")
def complete(cid: UUID, body: Answer, principal: str = Depends(scope)):
    if not store.complete(principal, str(cid), body.code):
        raise HTTPException(400, "Invalid or expired challenge")
    return {"verified": True, "method": "local-test-delivery"}


@app.get("/v1/evidence")
def evidence(principal: str = Depends(scope)):
    return store.evidence(principal)
