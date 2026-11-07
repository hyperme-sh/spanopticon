"""Exercise authenticated HTTP delivery through a separate synthetic adapter."""
from collections import Counter
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
from threading import Event, Thread
import time
from uuid import uuid4

from profiles.minimum.setup import setup
from verification.qualify import request


def main():
    with tempfile.TemporaryDirectory() as tmp, socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as server:
        root = Path(tmp)
        cfg = root / "config"
        setup(cfg)
        (cfg / "secret").write_bytes(secrets.token_bytes(32))
        (cfg / "secret").chmod(0o444)
        adapter = root / "adapter"
        adapter.mkdir(mode=0o755)
        server.bind(str(adapter / "delivery.sock"))
        (adapter / "delivery.sock").chmod(0o666)  # Parent temp dir is 0700. Worker sees only the bind mount.
        server.listen()
        server.settimeout(0.2)
        stop = Event()
        calls = Counter()
        codes = {}

        def serve():
            while not stop.is_set():
                try:
                    peer, _ = server.accept()
                except TimeoutError:
                    continue
                with peer:
                    msg = json.loads(peer.recv(8192))
                    cid = msg["challenge_id"]
                    calls[cid] += 1
                    codes[cid] = msg["code"]
                    kind = msg["recipient"].split("@")[0]
                    if kind == "lost":
                        continue
                    status = "rejected" if kind == "reject" else "accepted"
                    peer.sendall(json.dumps({"version": 1, "challenge_id": cid, "status": status}).encode())

        thread = Thread(target=serve)
        thread.start()
        env = dict(os.environ, VERIFICATION_CONFIG=str(cfg), VERIFICATION_ADAPTER_DIRECTORY=str(adapter),
                   VERIFICATION_LIMIT_RECIPIENT="1", VERIFICATION_LIMIT_OPERATOR="3")
        project = "adapter-" + uuid4().hex[:12]

        def compose(*args):
            return subprocess.check_output(["docker", "compose", "-p", project, "-f", "verification/compose.yaml",
                                           "-f", "verification/compose.operator.yaml", *args],
                                          text=True, env=env, timeout=360).strip()

        try:
            compose("build", "api")
            compose("up", "--no-build", "-d", "--wait")
            api = "http://" + compose("port", "api", "8000")
            issuer = "http://" + compose("port", "identity", "8000")
            tokens = {name: request(issuer + "/token", body={"username": name, "password": row["password"]})["access_token"]
                      for name, row in json.loads((cfg / "clients.json").read_text()).items()}
            endpoint = api + "/v1/challenges"
            owner = tokens["owner"]
            for name, status in (("ok", "accepted"), ("reject", "rejected"), ("lost", "ambiguous")):
                address = {"address": name + "@sample.test"}
                key = str(uuid4())
                first = request(endpoint, body=address, key=key, token=owner)
                cid = first["challenge_id"]
                assert request(endpoint, body=address, key=key, token=owner) == first
                query = ("import sqlite3,sys; print(sqlite3.connect('/state/contact.db').execute("
                         "'SELECT dispatch FROM challenge WHERE id=?',(sys.argv[1],)).fetchone()[0])")
                for _ in range(30):
                    if compose("exec", "-T", "worker", "python", "-c", query, cid) == status:
                        break
                    time.sleep(0.3)
                else:
                    raise AssertionError("Delivery did not reach expected state: " + status)
                result = request(endpoint + "/" + cid + "/complete", body={"code": codes[cid]}, token=owner,
                                 expected=200 if status == "accepted" else 400)
                evidence = request(api + "/v1/evidence", token=owner)
                assert evidence["verified"] == (status == "accepted")
                if result:
                    assert result["method"] == evidence["method"] == "operator-delivery"
                if name == "ok":
                    request(endpoint, body=address, key=str(uuid4()), token=tokens["other"], expected=429)
            request(endpoint, body={"address": "new@sample.test"}, key=str(uuid4()), token=tokens["other"], expected=429)
            compose("restart", "worker")
            time.sleep(3)
            assert len(calls) == 3 and all(count == 1 for count in calls.values())
        finally:
            stop.set()
            thread.join(3)
            compose("down", "--volumes")


if __name__ == "__main__":
    main()
