"""Run an isolated verification API against the temporary TEST broker signer."""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import textwrap
import time

import requests


def check(issuer, first, second, wrong_audience):
    response = requests.get(issuer, timeout=15)
    response.raise_for_status()
    public_key = response.json()["public_key"]
    with tempfile.TemporaryDirectory() as directory, socket.socket() as listener:
        root = Path(directory)
        signer = root / "signer.pem"
        signer.write_text("-----BEGIN PUBLIC KEY-----\n" + textwrap.fill(public_key, 64) + "\n-----END PUBLIC KEY-----\n")
        key = root / "verification.key"
        key.write_bytes(secrets.token_bytes(32))
        env = dict(os.environ, IDENTITY_PUBLIC_KEY_FILE=str(signer), IDENTITY_ISSUER=issuer,
                   IDENTITY_ALGORITHM="RS256", IDENTITY_AUDIENCE="verification-api",
                   VERIFICATION_KEY_FILE=str(key), VERIFICATION_DB=str(root / "db"), VERIFICATION_DELIVERY_MODE="local")
        subprocess.run([sys.executable, "-m", "verification.store"], env=env, check=True)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        api = f"http://127.0.0.1:{listener.getsockname()[1]}"
        fd = listener.fileno()
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "verification.service:app",
                                   "--fd", str(fd), "--no-access-log"], pass_fds=(fd,), env=env)
        try:
            for _ in range(60):
                if server.poll() is not None:
                    raise RuntimeError("Verification API exited during startup")
                try:
                    if requests.get(api + "/health/ready", timeout=0.5).ok:
                        break
                except (requests.ConnectionError, requests.Timeout):
                    pass
                time.sleep(0.25)
            else:
                raise RuntimeError("Verification API readiness timed out")
            for token, expected in [(first, 200), (second, 200), (wrong_audience, 401)]:
                result = requests.get(api + "/v1/evidence", headers={"Authorization": "Bearer " + token}, timeout=5)
                if result.status_code != expected:
                    raise RuntimeError(f"Federated evidence expected HTTP {expected}, got {result.status_code}")
                if expected == 200 and result.json()["verified"] is not False:
                    raise RuntimeError("Unexpected existing contact evidence")
        finally:
            server.terminate()
            server.wait(timeout=10)
    print("Verification API evidence returned 200 for both federated sessions and 401 for the other audience.")
