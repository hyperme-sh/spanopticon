"""Run identity -> enrollment -> executed self-use -> durable TEST settlement."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.receipt.exists():
        raise ValueError("receipt must be fresh")
    project = "cedar-test-" + uuid.uuid4().hex[:12]
    compose = ["docker", "compose", "-p", project, "-f", str(Path(__file__).with_name("compose.yaml"))]
    receipt = {"complete": False, "network": "Cedar", "live_funds": False,
               "implementation": "public-reference-operator", "physical_host_qualification": False}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        tenant, organization, other_organization, subject, stranger = [str(uuid.uuid4()) for _ in range(5)]
        passwords = {name: secrets.token_urlsafe(32) for name in ("owner", "stranger")}
        accounts = {name: {"subject": identifier, "tenant_id": tenant,
            "password_hash": hashlib.sha256(passwords[name].encode()).hexdigest()}
            for name, identifier in (("owner", subject), ("stranger", stranger))}
        settings = {"tenant_id": tenant, "members": {
            subject: {"organization": organization, "permissions": ["host:manage", "lease:self-use"]},
            stranger: {"organization": other_organization, "permissions": ["host:manage", "lease:self-use"]}}}
        signer = {"signer": "cedar-test-grant", "wallet": "cedar-test-wallet", "key_hex": secrets.token_hex(32)}

        def write(name, value):
            path = root / name
            if path.exists():
                path.chmod(0o600)
            path.write_text(json.dumps(value))
            path.chmod(0o444)
        write("accounts.json", accounts)
        write("operator.json", settings)
        write("signer.json", signer)
        write("settlement.json", [signer])
        write("host.json", {})
        subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(root / "issuer-private.pem")], check=True, capture_output=True)
        subprocess.run(["openssl", "pkey", "-in", str(root / "issuer-private.pem"), "-pubout", "-out", str(root / "issuer-public.pem")], check=True, capture_output=True)
        for path in root.glob("*.pem"):
            path.chmod(0o444)
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
            revision += "-dirty"
        receipt["source_revision"] = revision
        env = dict(os.environ, OPERATOR_TEST_SECRETS=str(root), OPERATOR_SOURCE_COMMIT=revision)

        def run(*arguments, capture=False):
            if capture:
                return subprocess.check_output(compose + list(arguments), env=env, text=True).strip()
            return subprocess.run(compose + list(arguments), env=env, check=True)

        def call(service, path, body=None, token=None, tenant_id=tenant, expected=200):
            headers = {"Content-Type": "application/json", "X-Tenant-ID": tenant_id}
            if token:
                headers["Authorization"] = "Bearer " + token
            # HTTP stays inside the isolated network. Credentials travel over
            # stdin, not command arguments or user-facing Compose logs.
            client = """import json,sys,urllib.request,urllib.error
value=json.load(sys.stdin)
request=urllib.request.Request(value['url'],headers=value['headers'],data=json.dumps(value['body']).encode() if value['body'] is not None else None)
try:
 with urllib.request.urlopen(request,timeout=5) as response:
  print(json.dumps({'status':response.status,'body':json.load(response)}))
except urllib.error.HTTPError as exc:
 print(json.dumps({'status':exc.code,'body':json.load(exc)}))
"""
            completed = subprocess.run(compose + ["exec", "-T", "identity", "python", "-c", client],
                env=env, input=json.dumps({"url": "http://" + service + ":8000" + path,
                    "headers": headers, "body": body}), text=True, capture_output=True, check=True)
            response = json.loads(completed.stdout)
            if response["status"] != expected:
                raise AssertionError(f"{path}: expected {expected}, got {response['status']}")
            return response["body"] if expected < 400 else None

        def wait_for(function, predicate, limit=60):
            until = time.monotonic() + limit
            while time.monotonic() < until:
                value = function()
                if predicate(value):
                    return value
                time.sleep(1)
            raise AssertionError("Timed out waiting for durable sample state")

        prefix = "/api/v1/hypermesh"
        try:
            run("up", "-d", "--build", "--wait", "--wait-timeout", "90", "identity", "operator", "settlement")
            call("identity", "/token", {"username": "owner", "password": "wrong"}, expected=401)
            tokens = {name: call("identity", "/token", {"username": name, "password": password})["access_token"]
                      for name, password in passwords.items()}
            owner, outsider = tokens["owner"], tokens["stranger"]
            device_body = {"host_org_id": organization, "serial": "cedar-worker-one"}
            call("operator", prefix + "/devices", device_body, token="forged", expected=401)
            for override in ({"iss": "https://other.test/identity"}, {"aud": "other-service"}, {"exp": 1}):
                code = ("import jwt,os,time; from pathlib import Path; "
                        "claims=" + repr({"iss": "https://cedar.test/identity", "aud": "cedar-operator",
                            "sub": subject, "tenant_id": tenant, "iat": int(time.time()), "exp": int(time.time()) + 300} | override)
                        + "; print(jwt.encode(claims,Path(os.environ['IDENTITY_PRIVATE_KEY_FILE']).read_bytes(),algorithm='EdDSA'))")
                invalid = run("exec", "-T", "identity", "python", "-c", code, capture=True)
                call("operator", prefix + "/devices", device_body, token=invalid, expected=401)
            call("operator", prefix + "/devices", device_body, token=owner, tenant_id=str(uuid.uuid4()), expected=403)
            call("operator", prefix + "/devices", device_body, token=outsider, expected=403)
            device = call("operator", prefix + "/devices", device_body, token=owner, expected=201)
            request = {"device_id": device["id"], "request_id": "self-use-one", "self_use": True, "reserved_hours": 1}
            call("operator", prefix + "/leases", request, token=owner, expected=409)
            write("host.json", {"tenant_id": tenant, "enroll_token": device["issued_enroll_token"]})
            run("--profile", "host", "up", "-d", "--build", "--wait", "--wait-timeout", "90", "host")
            call("operator", prefix + "/agent/heartbeat", {}, token=owner, expected=401)
            call("operator", prefix + "/agent/enroll", {"enroll_token": device["issued_enroll_token"],
                "host_name": "replay", "wg_public_key": base64.b64encode(b"a" * 32).decode()}, expected=401)
            call("operator", prefix + "/leases", request, token=outsider, expected=403)
            call("operator", prefix + "/leases", request | {"self_use": False}, token=owner, expected=422)
            # Complete work while the adapter is unavailable, then replace the
            # operator. The event and replay identity must survive independently.
            run("stop", "settlement")
            lease = call("operator", prefix + "/leases", request, token=owner, expected=201)
            assert call("operator", prefix + "/leases", request, token=owner, expected=201)["id"] == lease["id"]
            other_device = call("operator", prefix + "/devices",
                device_body | {"serial": "cedar-unbound-retry-target"}, token=owner, expected=201)
            call("operator", prefix + "/leases", request | {"device_id": other_device["id"]}, token=owner, expected=409)
            foreign = call("operator", prefix + "/devices",
                {"host_org_id": other_organization, "serial": "foreign-credential-fixture"}, token=outsider, expected=201)
            public_key = run("exec", "-T", "operator", "python", "-c",
                "import sqlite3; print(sqlite3.connect('/state/operator.sqlite3').execute('SELECT wg_public_key FROM devices WHERE id=?',(" + repr(device["id"]) + ",)).fetchone()[0])", capture=True)
            enrollment = {"enroll_token": foreign["issued_enroll_token"], "host_name": "foreign-fixture", "wg_public_key": public_key}
            call("operator", prefix + "/agent/enroll", enrollment, expected=409)
            foreign_host = call("operator", prefix + "/agent/enroll", enrollment | {
                "wg_public_key": base64.b64encode(hashlib.sha256(b"foreign-public-key-fixture").digest()).decode()})
            result = {"sha256": hashlib.sha256(b"cedar-self-use-workload-v1").hexdigest(), "exit_code": 0}
            call("operator", prefix + "/agent/jobs/" + lease["id"] + "/result", result,
                 token=foreign_host["device_secret"], expected=404)
            call("operator", prefix + "/agent/jobs/" + lease["id"] + "/result", result, token=owner, expected=401)
            call("operator", prefix + "/leases", request | {"self_use": False}, token=owner, expected=422)
            url = prefix + "/leases/" + lease["id"]
            call("operator", url, token=outsider, expected=404)
            terminal = wait_for(lambda: call("operator", url, token=owner), lambda row: row["status"] == "ended")
            assert terminal["settlement"] is None and terminal["billing_record_id"] is None and terminal["gross_cents"] == 0
            assert terminal["event"]["event_type"] == "hypermesh.lease.ended.v1"
            assert terminal["event"]["payload"]["tenant_id"] == tenant
            logs = run("logs", "--no-log-prefix", "host", capture=True)
            assert '"executed_lease": "' + lease["id"] + '"' in logs
            run("up", "-d", "--no-deps", "--force-recreate", "--wait", "operator")
            restored = call("operator", url, token=owner)
            assert restored["event"] == terminal["event"] and restored["settlement"] is None
            run("up", "-d", "--wait", "settlement")
            settled = wait_for(lambda: call("operator", url, token=owner), lambda row: row["settlement"] is not None)
            assert settled["settlement"]["status"] == "accepted"
            # Simulate the HTTP response having been lost after the adapter committed.
            run("exec", "-T", "operator", "python", "-c",
                "import sqlite3; db=sqlite3.connect('/state/operator.sqlite3'); db.execute('UPDATE leases SET settlement=NULL'); db.commit()")
            run("up", "-d", "--no-deps", "--force-recreate", "--wait", "operator", "settlement")
            replayed = wait_for(lambda: call("operator", url, token=owner), lambda row: row["settlement"] is not None)
            assert replayed["settlement"] == settled["settlement"]
            count = run("exec", "-T", "settlement", "python", "-c",
                        "import sqlite3; print(sqlite3.connect('/state/ledger.sqlite3').execute('SELECT count(*) FROM operations').fetchone()[0])", capture=True)
            assert int(count) == 1
            # Runtime has no private package or provider implementation installed.
            run("exec", "-T", "operator", "python", "-c",
                "import importlib.util; assert all(importlib.util.find_spec(p) is None for p in ('controlplane_billing','payment_service'))")
            receipt.update(complete=True, identity_verified=True, tenant_and_organization_denials=True,
                one_use_enrollment=True, host_executed_job=True, unbilled_self_use=True,
                duplicate_host_key_denied=True, foreign_host_result_denied=True, conflicting_lease_retry_denied=True,
                durable_event=terminal["event"], settlement=settled["settlement"],
                response_loss_replay=True, settlement_operations=1,
                test_grant={"amount": 1, "asset": "TEST", "network": "local"},
                images={name: run("images", "-q", name, capture=True) for name in ("identity", "operator", "host", "settlement")})
            for image_id in receipt["images"].values():
                actual = json.loads(subprocess.check_output(["docker", "image", "inspect", image_id], text=True))[0]
                assert actual["Config"]["Labels"]["org.opencontainers.image.revision"] == revision
        finally:
            run("--profile", "host", "down", "--remove-orphans")
            receipt["containers_removed"] = not run("--profile", "host", "ps", "-aq", capture=True)
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    main()
