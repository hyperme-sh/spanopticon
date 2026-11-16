"""Machine-authenticated intake and reviewer APIs; collection defaults off."""

import asyncio
from contextlib import asynccontextmanager, suppress
import json
import os
from pathlib import Path
import re
from uuid import UUID
import jwt
import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from .github import GitHub
from .policy import Consent, Submission, Decision, Reconcile
from .store import Principal, Store, Refusal


def configuration():
    cfg = json.loads(Path(os.environ["INTAKE_CONFIG_FILE"]).read_text())
    if cfg.get("enabled", False) is not True:
        raise RuntimeError("Intake collection is disabled")
    if (
        cfg["algorithm"] not in ("RS256", "EdDSA")
        or not isinstance(cfg["issuer"], str)
        or not isinstance(cfg["audience"], str)
    ):
        raise ValueError("Invalid identity configuration")
    for tenant, routes in cfg["tenants"].items():
        if str(UUID(tenant)) != tenant or not routes:
            raise ValueError("Invalid tenant configuration")
        for component, repo in routes.items():
            if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", component) or not re.fullmatch(
                r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo
            ):
                raise ValueError("Invalid destination configuration")
    return cfg


async def maintenance(application):
    healthy = True
    # Finish local expiry for every tenant before external provider work.
    for tenant in application.state.config["tenants"]:
        try:
            await asyncio.to_thread(application.state.store.purge, tenant)
        except Exception:
            healthy = False
    application.state.expiry_ok = healthy
    if application.state.config.get("publish_enabled", False) and os.environ.get(
        "GITHUB_INSTALLATION_TOKEN_FILE"
    ):
        for tenant, routes in application.state.config["tenants"].items():
            try:
                adapter = GitHub(
                    os.environ["GITHUB_INSTALLATION_TOKEN_FILE"], set(routes.values())
                )
                await asyncio.to_thread(
                    application.state.store.cleanup_destinations, tenant, adapter
                )
            except Exception:
                application.state.expiry_ok = False


@asynccontextmanager
async def lifespan(app):
    app.state.config = configuration()
    app.state.store = Store(
        Path(os.environ["INTAKE_DATABASE_URL_FILE"]).read_text().strip()
    )
    await asyncio.to_thread(app.state.store.ready)
    app.state.key = Path(os.environ["IDENTITY_PUBLIC_KEY_FILE"]).read_bytes()

    async def sweep():
        while True:
            await maintenance(app)
            await asyncio.sleep(60)

    # Apply expiry before admitting requests, including after a restart.
    for tenant in app.state.config["tenants"]:
        await asyncio.to_thread(app.state.store.purge, tenant)
    app.state.expiry_ok = True
    task = asyncio.create_task(sweep())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="Human feedback intake", lifespan=lifespan, docs_url=None, redoc_url=None
)


@app.middleware("http")
async def bounded_request(request, call_next):
    # Bound streamed bodies too; Content-Length alone is attacker-controlled.
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 24576:
            return JSONResponse({"detail": "Request too large"}, status_code=413)
    request._body = bytes(raw)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def invalid(request, exc):
    # FastAPI's default includes the submitted text in validation errors.
    return JSONResponse({"detail": "Invalid intake request"}, status_code=422)


@app.exception_handler(Refusal)
async def refusal(request, exc):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status)


@app.exception_handler(psycopg.Error)
async def database_unavailable(request, exc):
    return JSONResponse({"detail": "Intake unavailable"}, status_code=503)


def principal(credential: HTTPAuthorizationCredentials = Depends(HTTPBearer())):
    cfg = app.state.config
    try:
        claims = jwt.decode(
            credential.credentials,
            app.state.key,
            algorithms=[cfg["algorithm"]],
            audience=cfg["audience"],
            issuer=cfg["issuer"],
            options={"require": ["sub", "iat", "exp", "tenant_id", "device_id"]},
        )
        tenant = str(UUID(claims["tenant_id"]))
        subject, device = claims["sub"], claims["device_id"]
        if (
            not isinstance(subject, str)
            or not 1 <= len(subject) <= 200
            or not isinstance(device, str)
            or not 1 <= len(device) <= 200
        ):
            raise ValueError("Invalid subject")
        if tenant not in cfg["tenants"]:
            raise ValueError("Unknown tenant")
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
        raise HTTPException(401, "Invalid machine credential") from None
    return Principal(tenant, cfg["issuer"], subject, device)


def github(p):
    cfg = app.state.config
    if not cfg.get("publish_enabled", False) or not os.environ.get(
        "GITHUB_INSTALLATION_TOKEN_FILE"
    ):
        raise HTTPException(503, "GitHub forwarding is disabled")
    return GitHub(
        os.environ["GITHUB_INSTALLATION_TOKEN_FILE"],
        set(cfg["tenants"][p.tenant].values()),
    )


@app.get("/health/ready")
def ready():
    app.state.store.ready()
    if not app.state.expiry_ok:
        raise HTTPException(503, "Expiry maintenance unavailable")
    return {"ready": True}


@app.get("/v1/consent")
def consent_get(p: Principal = Depends(principal)):
    return app.state.store.consent(p)


@app.put("/v1/consent")
def consent_put(body: Consent, p: Principal = Depends(principal)):
    return app.state.store.consent(p, body)


@app.post("/v1/reports", status_code=201)
def submit(
    body: Submission,
    idempotency_key: UUID = Header(),
    p: Principal = Depends(principal),
):
    repo = app.state.config["tenants"][p.tenant].get(body.component)
    if repo is None:
        raise HTTPException(422, "Unknown component")
    return app.state.store.submit(p, idempotency_key, body, repo)


@app.get("/v1/reports")
def reports(p: Principal = Depends(principal)):
    return app.state.store.reports(p)


@app.get("/v1/review")
def review(after: UUID | None = None, p: Principal = Depends(principal)):
    return app.state.store.reports(p, review=True, after=after)


@app.post("/v1/review/{rid}/decision")
def decide(rid: UUID, body: Decision, p: Principal = Depends(principal)):
    return app.state.store.decide(p, rid, body)


@app.post("/v1/review/{rid}/publish")
def publish(rid: UUID, p: Principal = Depends(principal)):
    return app.state.store.publish(p, rid, github(p))


@app.post("/v1/review/{rid}/reconcile")
def reconcile(rid: UUID, body: Reconcile, p: Principal = Depends(principal)):
    return app.state.store.reconcile(p, rid, body.issue_number, github(p))


@app.post("/v1/review/{rid}/close")
def close(rid: UUID, p: Principal = Depends(principal)):
    return app.state.store.close(p, rid)


@app.get("/v1/privacy/export")
def export(p: Principal = Depends(principal)):
    return app.state.store.export(p)


@app.delete("/v1/privacy/data")
def erase(confirm: str = Header(), p: Principal = Depends(principal)):
    if confirm != "delete-my-feedback":
        raise HTTPException(422, "Deletion confirmation required")
    return app.state.store.erase(p)
