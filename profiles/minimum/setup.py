"""Create private, fresh local issuer configuration in a new directory."""
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import sys
from uuid import uuid4


def setup(root):
    root.mkdir(mode=0o700)
    private = root / "issuer.key"
    public = root / "issuer.pub"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", private], check=True)
    subprocess.run(["openssl", "pkey", "-in", private, "-pubout", "-out", public], check=True)
    accounts = {}
    clients = {}
    for name in ("owner", "other"):
        password = secrets.token_urlsafe(32)
        tenant = str(uuid4())
        clients[name] = {"password": password, "tenant_id": tenant}
        accounts[name] = {"subject": str(uuid4()), "tenant_id": tenant,
                          "password_hash": hashlib.sha256(password.encode()).hexdigest()}
    (root / "accounts.json").write_text(json.dumps(accounts))
    (root / "clients.json").write_text(json.dumps(clients))
    (root / "compose.env").write_text(f"MINIMUM_CONFIG={root.resolve()}\nMINIMUM_TENANT={clients['owner']['tenant_id']}\n")
    for name in ("issuer.key", "issuer.pub", "accounts.json"):
        (root / name).chmod(0o444)  # Individual read-only mounts inside a mode-0700 parent.


if __name__ == "__main__":
    setup(Path(sys.argv[1]))
