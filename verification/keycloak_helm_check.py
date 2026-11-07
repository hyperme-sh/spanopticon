"""Exercise the installed API with disposable Keycloak service-account tokens."""
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import uuid4

from verification.qualify import request


def service_tokens(cfg, issuer):
    tokens = {}
    for name, secret in json.loads((cfg / "credentials.json").read_text()).items():
        form = urlencode({"grant_type": "client_credentials", "client_id": "minimum-" + name,
                          "client_secret": secret}).encode()
        with urlopen(issuer + "/protocol/openid-connect/token", data=form, timeout=10) as response:
            tokens[name] = json.load(response)["access_token"]

    return tokens


def main(cfg, issuer, api):
    tokens = service_tokens(cfg, issuer)
    challenges = api + "/v1/challenges"
    params = {"body": {"address": "operator@example.test"}, "key": str(uuid4())}
    request(challenges, token=tokens["wrong-audience"], expected=401, **params)
    owner = tokens["owner"]
    challenge = request(challenges, token=owner, **params)
    assert request(challenges, token=owner, **params) == challenge
    cid = challenge["challenge_id"]
    query = ("import sqlite3, sys; db=sqlite3.connect('/mailbox/inbox.db'); "
             "row=db.execute('SELECT code FROM message WHERE id=?',(sys.argv[1],)).fetchone(); "
             "print(row[0] if row else '')")
    for _ in range(30):
        code = subprocess.check_output(["kubectl", "exec", "deployment/contact", "-c", "worker", "--",
                                        "python", "-c", query, cid], text=True, timeout=15).strip()
        if code:
            break
        time.sleep(1)
    else:
        raise RuntimeError("Local delivery timed out")
    complete = f"{challenges}/{cid}/complete"
    answer = {"code": code}
    request(complete, token=tokens["other"], body=answer, expected=400)
    request(complete, token=owner, body=answer)
    request(complete, token=owner, body=answer, expected=400)
    assert request(api + "/v1/evidence", token=owner)["verified"]
    assert not request(api + "/v1/evidence", token=tokens["other"])["verified"]


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
