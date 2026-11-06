"""Cedar protocol host: enroll a real key, heartbeat, and execute a bounded CPU job.

The worker runs inside its own restricted Compose container. It is not a native
host installer or a GPU certification substitute. No Docker socket is mounted.
"""
import base64
import fcntl
import json
import os
import secrets
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives import serialization


def save_identity(state, identity):
    """Durably replace a mode-0600 identity before any remote enrollment effect."""
    descriptor, temporary = tempfile.mkstemp(prefix=".host-", dir=state.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(identity, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, state)
        directory = os.open(state.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def recover_identity(state, bootstrap, call):
    # Multiple starts against one volume must never overwrite each other's keys.
    descriptor = os.open(state.with_suffix(".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if state.exists():
            identity = json.loads(state.read_text())
        else:
            private = X25519PrivateKey.generate()
            identity = {
                "wg_private_key": base64.b64encode(private.private_bytes(
                    serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode(),
                "device_secret": "hm_dev_" + secrets.token_urlsafe(32),
                "enrollment_pending": True,
                "tenant_id": bootstrap["tenant_id"],
                "enroll_token": bootstrap["enroll_token"],
                "host_name": "cedar-cpu-worker",
            }
            save_identity(state, identity)
        if identity.get("enrollment_pending"):
            if identity["tenant_id"] != bootstrap["tenant_id"] or identity["enroll_token"] != bootstrap["enroll_token"]:
                raise ValueError("Pending enrollment belongs to different bootstrap credentials")
            private = X25519PrivateKey.from_private_bytes(base64.b64decode(identity["wg_private_key"]))
            public = base64.b64encode(private.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
            response = call("/agent/enroll", {"enroll_token": identity["enroll_token"],
                "host_name": identity["host_name"], "wg_public_key": public, "device_secret": identity["device_secret"]})
            if response["device_secret"] != identity["device_secret"]:
                raise ValueError("Operator changed the persisted device credential")
            identity.update(response, enrollment_pending=False)
            del identity["enroll_token"]
            save_identity(state, identity)
        return identity


def main():
    bootstrap = json.loads(Path(os.environ["HOST_BOOTSTRAP_FILE"]).read_text())
    state = Path("/state/host.json")
    base = os.environ["OPERATOR_URL"] + "/api/v1/hypermesh"

    def call(path, body=None, secret=None):
        headers = {"Content-Type": "application/json", "X-Tenant-ID": bootstrap["tenant_id"]}
        if secret:
            headers["Authorization"] = "Bearer " + secret
        request = urllib.request.Request(base + path, headers=headers,
                                         data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    while True:
        try:
            identity = recover_identity(state, bootstrap, call)
            break
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                raise  # Authentication/conflict requires an explicit correction.
            time.sleep(1)
        except OSError:
            time.sleep(1)
    secret = identity["device_secret"]
    while True:
        try:
            call("/agent/heartbeat", {}, secret)
            Path("/state/healthy").touch()
            for job in call("/agent/jobs", secret=secret):
                if job["kind"] != "lease_start" or job["workload"] != "sha256-cedar-v1":
                    raise ValueError("Unknown workload; refusing execution")
                completed = subprocess.run([sys.executable, "-I", "-c",
                    "import hashlib; print(hashlib.sha256(b'cedar-self-use-workload-v1').hexdigest())"],
                    check=True, capture_output=True, text=True, timeout=5)
                call("/agent/jobs/" + job["id"] + "/result",
                     {"sha256": completed.stdout.strip(), "exit_code": completed.returncode}, secret)
                print(json.dumps({"executed_lease": job["id"], "workload": job["workload"]}), flush=True)
        except OSError:
            # A restart or lost response retries the same device/job identity.
            pass
        time.sleep(1)


if __name__ == "__main__":
    main()
