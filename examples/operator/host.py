"""Cedar protocol host: enroll a real key, heartbeat, and execute a bounded CPU job.

The worker runs inside its own restricted Compose container. It is not a native
host installer or a GPU certification substitute. No Docker socket is mounted.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives import serialization


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

    if state.exists():
        identity = json.loads(state.read_text())
    else:
        private = X25519PrivateKey.generate()
        public = base64.b64encode(private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
        identity = call("/agent/enroll", {"enroll_token": bootstrap["enroll_token"],
                         "host_name": "cedar-cpu-worker", "wg_public_key": public})
        identity["wg_private_key"] = base64.b64encode(private.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode()
        with state.open("x") as stream:
            os.chmod(state, 0o600)
            json.dump(identity, stream)
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
