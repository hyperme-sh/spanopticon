"""Run the authenticated HTTP journey against isolated Compose services."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from profiles.minimum.setup import setup


def request(url, *, body=None, token=None, key=None, expected=200):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if key:
        headers["Idempotency-Key"] = key
    req = Request(url, headers=headers, data=None if body is None else json.dumps(body).encode())
    try:
        with urlopen(req, timeout=8) as response:
            assert response.status == expected
            return json.load(response)
    except HTTPError as exc:
        if exc.code != expected:
            raise


def main():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config"
        setup(cfg)
        # Also exercise a second subject inside the owner's tenant.
        accounts_file = cfg / "accounts.json"
        clients_file = cfg / "clients.json"
        accounts = json.loads(accounts_file.read_text())
        clients = json.loads(clients_file.read_text())
        accounts["colleague"] = {**accounts["owner"], "subject": str(uuid4())}
        clients["colleague"] = clients["owner"]
        accounts_file.chmod(0o600)
        accounts_file.write_text(json.dumps(accounts))
        accounts_file.chmod(0o444)
        clients_file.write_text(json.dumps(clients))
        secret = cfg / "secret"
        secret.write_bytes(secrets.token_bytes(32))
        secret.chmod(0o444)
        env = {**os.environ, "VERIFICATION_CONFIG": str(cfg)}
        project = "verify-" + uuid4().hex[:12]
        prefix = ["docker", "compose", "-p", project, "-f", "verification/compose.yaml"]

        def compose(*args):
            return subprocess.check_output(prefix + list(args), env=env, text=True, timeout=360).strip()

        def mailbox(cid):
            query = ("import sqlite3,sys; db=sqlite3.connect('/mailbox/inbox.db');"
                     "row=db.execute('SELECT code FROM message WHERE id=?',(sys.argv[1],)).fetchone();"
                     "print(row[0] if row else '')")
            for _ in range(30):
                code = compose("exec", "-T", "worker", "python", "-c", query, cid)
                if code:
                    return code
                time.sleep(0.5)
            raise RuntimeError("Timed out waiting for local delivery")

        try:
            compose("build", "api")
            compose("up", "-d", "--no-build", "--wait")
            api = "http://" + compose("port", "api", "8000")
            issuer = "http://" + compose("port", "identity", "8000")
            tokens = {name: request(issuer + "/token", body={"username": name, "password": row["password"]})["access_token"]
                      for name, row in json.loads((cfg / "clients.json").read_text()).items()}
            owner, other = tokens["owner"], tokens["other"]
            endpoint = api + "/v1/challenges"
            body = {"address": "operator@example.test"}
            key = str(uuid4())
            request(endpoint, body=body, key=key, expected=401)
            first = request(endpoint, body=body, key=key, token=owner)
            assert first == request(endpoint, body=body, key=key, token=owner)
            request(endpoint, body={"address": "changed@example.test"}, key=key, token=owner, expected=409)
            cid = first["challenge_id"]
            answer = {"code": mailbox(cid)}
            finish = endpoint + "/" + cid + "/complete"
            request(finish, body=answer, token=other, expected=400)
            request(finish, body=answer, token=tokens["colleague"], expected=400)
            request(finish, body=answer, token=owner)
            request(finish, body=answer, token=owner, expected=400)
            compose("run", "--rm", "--no-deps", "migrate")
            compose("restart", "api", "worker")
            compose("up", "-d", "--wait")
            api = "http://" + compose("port", "api", "8000")
            assert request(api + "/v1/evidence", token=owner)["verified"]
            assert not request(api + "/v1/evidence", token=other)["verified"]
            assert not request(api + "/v1/evidence", token=tokens["colleague"])["verified"]
            request(api + "/v1/challenges", body={"address": "new@example.test"}, token=owner, key=str(uuid4()))
            assert not request(api + "/v1/evidence", token=owner)["verified"]
        finally:
            compose("down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    main()
