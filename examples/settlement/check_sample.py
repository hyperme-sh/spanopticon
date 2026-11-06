"""Qualify the public sample from a clean Compose build; never move funds."""
import argparse
from dataclasses import asdict
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid

from .contract import SettlementIntent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.receipt.exists():
        raise ValueError("receipt must be fresh")
    project = "settlement-test-" + uuid.uuid4().hex[:12]
    compose = ["docker", "compose", "-p", project, "-f", str(Path(__file__).with_name("compose.yaml"))]
    receipt = {"complete": False, "live_funds": False, "project": project}
    with tempfile.TemporaryDirectory() as directory:
        key = secrets.token_bytes(32)
        path = Path(directory) / "keys.json"
        path.write_text(json.dumps([{"wallet": "test-wallet", "signer": "test-signer", "key_hex": key.hex()}]))
        path.chmod(0o444)
        env = dict(os.environ, SETTLEMENT_TEST_KEYS_FILE=str(path), SETTLEMENT_TEST_PORT="0")

        def run(*arguments, capture=False):
            return subprocess.check_output(compose + list(arguments), env=env, text=True) if capture else subprocess.run(compose + list(arguments), env=env, check=True)

        def endpoint():
            return "http://" + run("port", "settlement", "8000", capture=True).strip()

        def submit(intent, signature=None):
            body = asdict(intent) | {"signer": "test-signer", "signature_hex": signature or hmac.digest(key, intent.signing_bytes(), "sha256").hex()}
            request = urllib.request.Request(endpoint() + "/settlements", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.load(response)

        try:
            run("up", "-d", "--build", "--wait", "--wait-timeout", "90")
            with urllib.request.urlopen(endpoint() + "/health/ready", timeout=5) as response:
                assert json.load(response)["durable"] is True
            intent = SettlementIntent("test-wallet", 123456, "TEST", "local", "restart-one")
            original = submit(intent)
            assert original["status"] == "accepted"
            run("up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90")
            assert submit(intent) == original
            for attempt, signature, expected in (
                (SettlementIntent("test-wallet", 123457, "TEST", "local", "restart-one"), None, 409),
                (intent, "00", 403),
                (SettlementIntent("test-wallet", 1, "USDC", "mainnet", "other"), None, 409),
            ):
                try:
                    submit(attempt, signature)
                except urllib.error.HTTPError as exc:
                    assert exc.code == expected
                else:
                    raise AssertionError("invalid settlement accepted")
            count = run("exec", "-T", "settlement", "python", "-c", "import sqlite3; print(sqlite3.connect('/state/ledger.sqlite3').execute('SELECT count(*) FROM operations').fetchone()[0])", capture=True)
            assert int(count.strip()) == 1
            receipt.update(complete=True, same_receipt_after_recreation=True,
                           conflict_status=409, invalid_authority_status=403,
                           non_test_asset_status=409, operations=1)
            receipt["image"] = run("images", "-q", "settlement", capture=True).strip()
        finally:
            # Preserve the TEST ledger volume, as the ordinary operator teardown does.
            run("down", "--remove-orphans")
            receipt["containers_removed"] = not run("ps", "-aq", capture=True).strip()
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    main()
