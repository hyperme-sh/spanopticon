"""Cold-restore the local chart into a fresh PVC in the disposable kind cluster."""
import base64
import json
from pathlib import Path
import subprocess
import sys
from uuid import UUID, uuid4

from verification import authority, store
from verification.keycloak_helm_check import service_tokens
from verification.qualify import request


def kube(*args, data=None):
    return subprocess.run(["kubectl", *args], input=data, stdout=subprocess.PIPE,
                          check=True, timeout=120).stdout


def create(obj):
    kube("create", "-f", "-", data=json.dumps(obj).encode())


def maintenance(spec, claim):
    worker = next(c for c in spec["containers"] if c["name"] == "worker").copy()
    worker["command"] = ["sleep", "3600"]
    volumes = [v.copy() for v in spec["volumes"] if v["name"] in {"data", "worker-key"}]
    next(v for v in volumes if v["name"] == "data")["persistentVolumeClaim"] = {"claimName": claim}
    create({"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "recovery"},
            "spec": {"automountServiceAccountToken": False, "securityContext": spec["securityContext"],
                     "containers": [worker], "volumes": volumes}})
    kube("wait", "--for=condition=Ready", "pod/recovery", "--timeout=90s")


def drill(cfg, issuer):
    token = service_tokens(cfg, issuer)["owner"]
    payload = token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    scope = store.principal_scope(issuer, str(UUID(claims["tenant_id"])), claims["sub"])
    deploy = json.loads(kube("get", "deploy/contact", "-ojson"))
    spec = deploy["spec"]["template"]["spec"]
    kube("scale", "deploy/contact", "--replicas=0")
    kube("wait", "--for=delete", "pod", "-l", "app.kubernetes.io/instance=contact", "--timeout=90s")
    maintenance(spec, "contact-data")
    backup = kube("exec", "recovery", "--", "tar", "-cf", "-", "-C", "/", "state/contact.db", "mailbox/inbox.db")
    kube("delete", "pod/recovery", "--wait=true")
    authority.record(cfg / "secret", scope)
    patch = {"data": {"secret": base64.b64encode((cfg / "secret").read_bytes()).decode()}}
    kube("patch", "secret/verification-config", "--type=merge", "--patch-file=/dev/stdin",
         data=json.dumps(patch).encode())

    claim = "contact-restored"
    create({"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": {"name": claim},
            "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "128Mi"}}}})
    maintenance(spec, claim)
    kube("exec", "-i", "recovery", "--", "tar", "-xf", "-", "-C", "/", data=backup)
    gate = """from verification import store
try:
    store.ready()
except RuntimeError as exc:
    assert str(exc) == 'Verification erasure replay required'
else:
    raise RuntimeError('Stale backup accepted')
"""
    kube("exec", "recovery", "--", "python", "-c", gate)
    kube("exec", "recovery", "--", "python", "-m", "verification.erasure", "replay")
    kube("exec", "recovery", "--", "python", "-c",
         "import sqlite3; db=sqlite3.connect('/mailbox/inbox.db'); "
         "assert db.execute('SELECT address FROM message').fetchall()==[('peer@example.test',)]")
    kube("delete", "pod/recovery", "--wait=true")
    kube("patch", "deployment/contact", "--type=strategic", "-p", json.dumps({"spec": {"replicas": 1,
         "template": {"spec": {"volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": claim}}]}}}}))
    kube("rollout", "status", "deploy/contact", "--timeout=90s")


def verify(cfg, issuer, api):
    tokens = service_tokens(cfg, issuer)
    assert not request(api + "/v1/evidence", token=tokens["owner"])["verified"]
    assert request(api + "/v1/evidence", token=tokens["other"])["verified"]
    request(api + "/v1/challenges", token=tokens["owner"], body={"address": "operator@example.test"},
            key=str(uuid4()), expected=403)


if __name__ == "__main__":
    cfg, issuer = Path(sys.argv[1]), sys.argv[2]
    if len(sys.argv) == 3:
        drill(cfg, issuer)
    else:
        verify(cfg, issuer, sys.argv[3])
