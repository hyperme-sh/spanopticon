"""Compose regression: lose the committed enrollment reply and terminate the host."""
import hashlib
import json
import os
from pathlib import Path
import urllib.request

from examples.operator.host import recover_identity


def main():
    bootstrap = json.loads(Path(os.environ["HOST_BOOTSTRAP_FILE"]).read_text())
    state = Path("/state/host.json")
    if state.exists():
        raise RuntimeError("Regression requires a fresh host volume")

    def lose_response(path, body):
        pending = json.loads(state.read_text())
        assert pending["enrollment_pending"] is True
        assert pending["device_secret"] == body["device_secret"]
        assert state.stat().st_mode & 0o777 == 0o600
        request = urllib.request.Request(os.environ["OPERATOR_URL"] + "/api/v1/hypermesh" + path,
            data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "X-Tenant-ID": bootstrap["tenant_id"]})
        with urllib.request.urlopen(request, timeout=5) as response:
            committed = json.load(response)
        assert committed["device_secret"] == pending["device_secret"]
        proof = {"device_id": committed["device_id"], "wg_public_key": body["wg_public_key"],
                 "credential_hash": hashlib.sha256(pending["device_secret"].encode()).hexdigest(),
                 "private_key_hash": hashlib.sha256(pending["wg_private_key"].encode()).hexdigest()}
        with Path("/state/lost-response-proof.json").open("w") as stream:
            json.dump(proof, stream)
            stream.flush()
            os.fsync(stream.fileno())
        # No response reaches recover_identity; no acknowledged state is saved.
        os._exit(73)

    recover_identity(state, bootstrap, lose_response)
    raise AssertionError("Enrollment response was not discarded")


if __name__ == "__main__":
    main()
