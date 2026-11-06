"""Independent public TEST operator implementing the enrollment and lease boundary.

This reference operator is not the Hypermesh Plane binary. It runs a fixed CPU
job, requires subject-bound ownership, and emits the registered lease-ended wire
contract. No hardware certification, customer billing, or live rail is claimed.
"""
import asyncio
import base64
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import datetime, timezone
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import urllib.request
import uuid

from fastapi import FastAPI, HTTPException, Request
import jsonschema
import jwt
from pydantic import BaseModel, ConfigDict, Field

from examples.settlement.contract import SettlementIntent, SettlementResult
from .common import ISSUER, AUDIENCE, canonical, config, digest, initialize, transaction

SCHEMA = json.loads(Path(__file__).with_name("schemas").joinpath("hypermesh.lease.ended.v1.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA, format_checker=jsonschema.FormatChecker())
WORKLOAD = "sha256-cedar-v1"
EXPECTED_RESULT = digest("cedar-self-use-workload-v1")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DeviceCreate(Strict):
    host_org_id: str
    serial: str = Field(min_length=1, max_length=128)


class Enroll(Strict):
    enroll_token: str
    host_name: str = Field(min_length=1, max_length=128)
    wg_public_key: str
    device_secret: str = Field(pattern=r"^hm_dev_[A-Za-z0-9_-]{43}$", repr=False)


class LeaseCreate(Strict):
    device_id: str
    request_id: str = Field(min_length=1, max_length=128)
    self_use: bool
    reserved_hours: int = Field(ge=1, le=1)


class JobResult(Strict):
    sha256: str = Field(pattern="^[0-9a-f]{64}$")
    exit_code: int = Field(ge=0, le=0)


def bearer(request):
    value = request.headers.get("Authorization", "")
    if not value.startswith("Bearer "):
        raise HTTPException(401, "Bearer credential required")
    return value[7:]


def person(request, settings, permission):
    try:
        claims = jwt.decode(bearer(request), Path(os.environ["IDENTITY_PUBLIC_KEY_FILE"]).read_bytes(),
                            algorithms=["EdDSA"], audience=AUDIENCE, issuer=ISSUER,
                            options={"require": ["sub", "tenant_id", "exp", "iat", "iss", "aud"]})
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid identity token") from None
    member = settings["members"].get(claims["sub"])
    if (member is None or claims["tenant_id"] != settings["tenant_id"]
            or request.headers.get("X-Tenant-ID") != claims["tenant_id"]
            or permission not in member["permissions"]):
        raise HTTPException(403, "Subject, tenant, or permission denied")
    return claims["sub"], member


def machine(request, db):
    row = db.execute("SELECT * FROM devices WHERE secret_hash=?", (digest(bearer(request)),)).fetchone()
    if row is None:
        raise HTTPException(401, "Invalid host credential")
    if row["tenant"] != request.headers.get("X-Tenant-ID"):
        raise HTTPException(403, "Host tenant differs")
    return row


def dispatch(settings):
    """Retry the durable signed intent; a lost response cannot create a new key."""
    with transaction() as db:
        pending = db.execute("SELECT id,event FROM leases WHERE state='ended' AND settlement IS NULL LIMIT 10").fetchall()
    key = json.loads(Path(os.environ["SETTLEMENT_SIGNER_FILE"]).read_text())
    for row in pending:
        event = json.loads(row["event"])
        VALIDATOR.validate(event["payload"])
        if event["tenant_id"] != settings["tenant_id"] or event["payload"]["gross_cents"] != 0:
            raise ValueError("Unexpected self-use event")
        # Explicit sample policy, not a customer charge: one local TEST unit
        # granted by this operator's configured wallet authority per completed job.
        intent = SettlementIntent(key["wallet"], 1, "TEST", "local", row["id"])
        body = asdict(intent) | {"signer": key["signer"], "signature_hex": hmac.digest(
            bytes.fromhex(key["key_hex"]), intent.signing_bytes(), "sha256").hex()}
        request = urllib.request.Request(os.environ["SETTLEMENT_URL"] + "/settlements",
            data=canonical(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            result = SettlementResult(**json.load(response))
        if result.status != "pending":
            with transaction() as db:
                db.execute("UPDATE leases SET settlement=? WHERE id=? AND settlement IS NULL",
                           (canonical(asdict(result)), row["id"]))


def create_app():
    settings = config()

    @asynccontextmanager
    async def lifespan(app):
        initialize()
        async def relay():
            while True:
                try:
                    await asyncio.to_thread(dispatch, settings)
                except (OSError, ValueError, TypeError, jsonschema.ValidationError):
                    # Keep the durable event pending; never invent an acknowledgment.
                    pass
                await asyncio.sleep(1)
        task = asyncio.create_task(relay())
        yield
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    app = FastAPI(title="Cedar TEST operator", lifespan=lifespan)

    @app.get("/health/ready")
    def ready():
        return {"ready": True, "network": "Cedar", "test_only": True}

    @app.post("/api/v1/hypermesh/devices", status_code=201)
    def device(body: DeviceCreate, request: Request):
        _, member = person(request, settings, "host:manage")
        if body.host_org_id != member["organization"]:
            raise HTTPException(403, "Host organization differs")
        identifier, token = str(uuid.uuid4()), secrets.token_urlsafe(32)
        try:
            with transaction() as db:
                db.execute("INSERT INTO devices(id,tenant,organization,serial,enroll_hash) VALUES(?,?,?,?,?)",
                           (identifier, settings["tenant_id"], body.host_org_id, body.serial, digest(token)))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Host serial already registered") from None
        return {"id": identifier, "issued_enroll_token": token}

    @app.post("/api/v1/hypermesh/agent/enroll")
    def enroll(body: Enroll, request: Request):
        try:
            key = base64.b64decode(body.wg_public_key, validate=True)
            if len(key) != 32 or not any(key) or base64.b64encode(key).decode() != body.wg_public_key:
                raise ValueError("Invalid key")
        except ValueError:
            raise HTTPException(422, "Canonical WireGuard public key required") from None
        secret = body.device_secret
        token_hash = digest(body.enroll_token)
        try:
            with transaction() as db:
                # A consumed token alone grants nothing. Recovery requires the
                # already-bound device credential and exactly the original request.
                row = db.execute("SELECT * FROM devices WHERE secret_hash=? AND enrollment_receipt_hash=?",
                                 (digest(secret), token_hash)).fetchone()
                if row is not None:
                    if request.headers.get("X-Tenant-ID") != row["tenant"]:
                        raise HTTPException(403, "Enrollment tenant differs")
                    if row["wg_public_key"] != body.wg_public_key or row["host_name"] != body.host_name:
                        raise HTTPException(409, "Enrollment identity differs")
                    return {"device_id": row["id"], "device_secret": secret, "credential_prefix": secret[:8], "role": "host"}
                row = db.execute("SELECT * FROM devices WHERE enroll_hash=?", (token_hash,)).fetchone()
                if row is None:
                    raise HTTPException(401, "Enrollment token consumed or unknown")
                if request.headers.get("X-Tenant-ID") != row["tenant"]:
                    raise HTTPException(403, "Enrollment tenant differs")
                db.execute("UPDATE devices SET enroll_hash=NULL,enrollment_receipt_hash=?,secret_hash=?,wg_public_key=?,host_name=? WHERE id=?",
                           (token_hash, digest(secret), body.wg_public_key, body.host_name, row["id"]))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "WireGuard identity already enrolled") from None
        return {"device_id": row["id"], "device_secret": secret, "credential_prefix": secret[:8], "role": "host"}

    @app.post("/api/v1/hypermesh/agent/heartbeat")
    def heartbeat(request: Request):
        with transaction() as db:
            host = machine(request, db)
            db.execute("UPDATE devices SET heartbeat=? WHERE id=?", (time.time(), host["id"]))
        return {"accepted": True}

    @app.post("/api/v1/hypermesh/leases", status_code=201)
    def lease(body: LeaseCreate, request: Request):
        subject, member = person(request, settings, "lease:self-use")
        if not body.self_use:
            raise HTTPException(422, "Only organization self-use is supported")
        fingerprint = canonical(body.model_dump())
        try:
            with transaction() as db:
                host = db.execute("SELECT * FROM devices WHERE id=? AND tenant=?", (body.device_id, settings["tenant_id"])).fetchone()
                if host is None or host["organization"] != member["organization"]:
                    raise HTTPException(403, "Self-use requires the host's organization")
                previous = db.execute("SELECT * FROM leases WHERE tenant=? AND subject=? AND request_id=?",
                                      (settings["tenant_id"], subject, body.request_id)).fetchone()
                if previous is not None:
                    if previous["request"] != fingerprint:
                        raise HTTPException(409, "Conflicting lease retry")
                    return {"id": previous["id"], "status": previous["state"], "self_use": True}
                if not host["secret_hash"] or not host["heartbeat"] or time.time() - host["heartbeat"] > 30:
                    raise HTTPException(409, "An enrolled, heartbeating host is required")
                db.execute("UPDATE leases SET state='failed' WHERE device=? AND state='starting' AND created<?",
                           (host["id"], time.time() - 3600))
                identifier = str(uuid.uuid4())
                db.execute("INSERT INTO leases(id,tenant,subject,device,request_id,request,state,created) VALUES(?,?,?,?,?,?,?,?)",
                           (identifier, settings["tenant_id"], subject, host["id"], body.request_id, fingerprint, "starting", time.time()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Host already has a live lease") from None
        return {"id": identifier, "status": "starting", "self_use": True}

    @app.get("/api/v1/hypermesh/agent/jobs")
    def jobs(request: Request):
        with transaction() as db:
            host = machine(request, db)
            db.execute("UPDATE leases SET state='failed' WHERE device=? AND state='starting' AND created<?",
                       (host["id"], time.time() - 3600))
            return [{"id": row["id"], "kind": "lease_start", "workload": WORKLOAD}
                    for row in db.execute("SELECT id FROM leases WHERE device=? AND state='starting'", (host["id"],))]

    @app.post("/api/v1/hypermesh/agent/jobs/{lease_id}/result")
    def result(lease_id: str, body: JobResult, request: Request):
        if body.sha256 != EXPECTED_RESULT:
            raise HTTPException(422, "Workload result differs")
        with transaction() as db:
            host = machine(request, db)
            row = db.execute("SELECT * FROM leases WHERE id=? AND device=?", (lease_id, host["id"])).fetchone()
            if row is None:
                raise HTTPException(404, "Lease not assigned to this host")
            fingerprint = canonical(body.model_dump())
            if row["result"] is not None:
                if row["result"] != fingerprint:
                    raise HTTPException(409, "Conflicting result replay")
                return {"accepted": True}
            if time.time() > row["created"] + 3600:
                raise HTTPException(409, "Self-use lease window expired")
            payload = {"lease_id": lease_id, "tenant_id": row["tenant"], "kind": "p1_hardware", "status": "ended",
                "class_id": "cedar-test-cpu", "catalog_id": None, "device_id": host["id"],
                "host_org_id": host["organization"], "renter_user_id": row["subject"], "billing_record_id": None,
                "reserved_hours": 1, "gross_cents": 0, "purpose": "renter", "contract_id": None}
            VALIDATOR.validate(payload)
            envelope = {"schema_version": 1, "event_type": "hypermesh.lease.ended.v1", "service": "cedar-operator",
                "tenant_id": row["tenant"], "occurred_at": datetime.now(timezone.utc).isoformat(),
                "correlation_id": lease_id, "payload": payload}
            db.execute("UPDATE leases SET state='ended',result=?,event=? WHERE id=?", (fingerprint, canonical(envelope), lease_id))
        return {"accepted": True}

    @app.get("/api/v1/hypermesh/leases/{lease_id}")
    def read_lease(lease_id: str, request: Request):
        subject, _ = person(request, settings, "lease:self-use")
        with transaction() as db:
            row = db.execute("SELECT * FROM leases WHERE id=? AND subject=? AND tenant=?",
                             (lease_id, subject, settings["tenant_id"])).fetchone()
            if row is None:
                raise HTTPException(404, "Lease not found")
            return {"id": row["id"], "status": row["state"], "self_use": True, "billing_record_id": None,
                    "gross_cents": 0, "event": json.loads(row["event"]) if row["event"] else None,
                    "settlement": json.loads(row["settlement"]) if row["settlement"] else None}

    return app
