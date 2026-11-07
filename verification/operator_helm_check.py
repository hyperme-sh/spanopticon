"""Check real Kubernetes adapter dispatch outcomes and budgets using Keycloak."""
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from verification.keycloak_helm_check import service_tokens
from verification.qualify import request


def output(*cmd):
    return subprocess.check_output(cmd, timeout=30, text=True).strip()


def db_value(container, path, query, *args):
    script = ("import sqlite3,sys; row=sqlite3.connect(sys.argv[1]).execute(sys.argv[2],sys.argv[3:]).fetchone(); "
              "print(row[0] if row else '')")
    return output("kubectl", "exec", "deployment/contact", "-c", container, "--",
                  "python", "-c", script, path, query, *args)


def receipts(query, *args):
    return db_value("adapter", "/adapter/test-receipts.db", query, *args)


def worker_status():
    pod = json.loads(output("kubectl", "get", "pods", "-l", "app.kubernetes.io/instance=contact", "-o", "json"))["items"][0]
    return next(c for c in pod["status"]["containerStatuses"] if c["name"] == "worker")


def main(cfg, issuer, api, node):
    tokens = service_tokens(cfg, issuer)
    endpoint = api + "/v1/challenges"

    def create(name, principal, expected=200, key=None):
        return request(endpoint, body={"address": name + "@sample.test"}, token=tokens[principal],
                       key=key or str(uuid4()), expected=expected)

    create("unauthorized", "wrong-audience", 401)
    for name, principal, status in (("ok", "owner", "accepted"), ("reject", "owner", "rejected"),
                                    ("timeout", "owner", "ambiguous"), ("lost", "other", "ambiguous")):
        key = str(uuid4())
        first = create(name, principal, key=key)
        assert first == create(name, principal, key=key)
        cid = first["challenge_id"]
        for _ in range(30):
            if db_value("worker", "/state/contact.db", "SELECT dispatch FROM challenge WHERE id=?", cid) == status:
                break
            time.sleep(0.3)
        else:
            raise RuntimeError("Dispatch did not reach " + status)
        code = receipts("SELECT code FROM receipts WHERE id=?", cid)
        assert len(code) == 6
        finish = endpoint + "/" + cid + "/complete"
        if name == "ok":
            request(finish, token=tokens["other"], body={"code": code}, expected=400)
        request(finish, token=tokens[principal], body={"code": code}, expected=200 if name == "ok" else 400)
        if name == "ok":
            request(finish, token=tokens[principal], body={"code": code}, expected=400)
            evidence = request(api + "/v1/evidence", token=tokens[principal])
            assert evidence["verified"] and evidence["method"] == "operator-delivery"
            create(name, "other", 429)  # Below both subject and global budgets.
        else:
            assert not request(api + "/v1/evidence", token=tokens[principal])["verified"]
        if name == "timeout":
            create("subject-limit", "owner", 429)  # Global budget still has one slot.
    create("operator-limit", "other", 429)  # Second subject has used only one slot.

    before = worker_status()
    cid = before["containerID"].removeprefix("containerd://")
    output("docker", "exec", node, "crictl", "stop", "--timeout", "1", cid)
    for _ in range(45):
        after = worker_status()
        if after["restartCount"] > before["restartCount"] and after["ready"]:
            break
        time.sleep(1)
    else:
        raise RuntimeError("Worker did not restart")
    time.sleep(3)
    assert receipts("SELECT count(*) FROM receipts") == "4"
    assert receipts("SELECT count(DISTINCT id) FROM receipts") == "4"


if __name__ == "__main__":
    main(Path(sys.argv[1]), *sys.argv[2:])
