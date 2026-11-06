"""Local TEST issuer with asymmetric tokens; private keys never reach the operator."""
import hmac
import json
import os
from pathlib import Path
import time

from fastapi import FastAPI, HTTPException
import jwt
from pydantic import BaseModel, ConfigDict

from .common import ISSUER, AUDIENCE, digest


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    username: str
    password: str


def create_app():
    accounts = json.loads(Path(os.environ["IDENTITY_ACCOUNTS_FILE"]).read_text())
    private_key = Path(os.environ["IDENTITY_PRIVATE_KEY_FILE"]).read_bytes()
    app = FastAPI(title="Cedar TEST identity")

    @app.get("/health/ready")
    def ready():
        return {"ready": True, "issuer": ISSUER}

    @app.post("/token")
    def token(body: Login):
        account = accounts.get(body.username)
        if account is None or not hmac.compare_digest(account["password_hash"], digest(body.password)):
            raise HTTPException(401, "Invalid TEST identity")
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": account["subject"],
                  "tenant_id": account["tenant_id"], "iat": now, "exp": now + 300}
        return {"access_token": jwt.encode(claims, private_key, algorithm="EdDSA"), "token_type": "Bearer"}

    return app
